# -*- coding: utf-8 -*-
"""
步骤 6：问答层（2.0 的大脑）

三步流程（PRD 2.0）：
1. 问题分流：归纳题 / 细节题 / 图表题（一次轻量分类调用）
2. 按题型准备材料：
   - 归纳题 → 混合检索现查 + 摘要/亮点兜底（2026-09-08 起不再直接读 8 字段预答案：
     用户问题可能不在 8 字段范围内，预答案只留在论文档案 tab 展示）
   - 细节题 → 混合检索现查（步骤 5，向量 + BM25）
   - 图表题 → 图注清单 + 页码定位，声明「无法直接读图」
3. 组装提示词 → 大模型作答：必须带出处，禁止编造，术语保留英文
"""
import os

from config import TOP_K
from llm import chat
from preanswer import generate_preanswers
from retriever import hybrid_search

# ---------- 第 1 步：问题分流 ----------
# 修复记录 2026-09-02：问「变化趋势/含量差异/分布」的题曾被错分到「细节」，
# 走正文检索答数值但不提图号，判分 0 分——补强图表类描述和示例。
_ROUTE_SYSTEM = (
    "你是问答分流器。判断用户问题属于哪类，只回答一个词：\n"
    "- 归纳：问整篇论文的主线、意义、内容、结论、创新点等，看要点就能答\n"
    "- 细节：问具体数值、方法、某个点位/研究对象的来龙去脉，需要翻正文\n"
    "- 图表：问某张图或某个表的内容、数据、趋势；问「变化趋势」「随XX的变化」"
    "「含量/储量差异」「分布」这类答案藏在图/表里的问题，也算图表\n\n"
    "示例：\n"
    "「这篇论文研究了什么？」→ 归纳\n"
    "「研究区的潮差是多少？」→ 细节\n"
    "「Mo 在 YRE-S2 为什么异常？」→ 细节\n"
    "「图5展示了什么？」→ 图表\n"
    "「表格2里有哪些采样点？」→ 图表\n"
    "「C、N、S含量随采样地点的变化趋势？」→ 图表\n"
    "「河口和海洋单位面积有机碳储量有何差异？」→ 图表\n"
    "「植被生长阶段哪种元素的通量最高？」→ 图表\n"
    "「研究区域黄河水铁浓度是多少？」→ 图表"
)


def _route(question, model=None):
    """问题分流：归纳 / 细节 / 图表（解析失败默认细节，宁可不偷懒也要查正文）。"""
    try:
        out = chat([{"role": "system", "content": _ROUTE_SYSTEM},
                    {"role": "user", "content": question}],
                   model=model, max_tokens=300).strip()   # B 方案默认思考形态：留足余量
    except Exception:
        return "细节"
    for tag in ("归纳", "细节", "图表"):
        if tag in out:
            return tag
    return "细节"


# ---------- 第 2 步：按题型准备材料 ----------
def _format_hits(hits):
    """检索块 → 提示词文本（含章节和页码）。"""
    return "\n\n".join(f"[第{h['page']}页 {h['section']}]\n{h['text']}" for h in hits)


def _format_captions(profile):
    """图注清单 → 提示词文本（含编号和页码）。"""
    if not profile["captions"]:
        return "（本文没有提取到图表说明）"
    return "\n".join(f"{c['id']}（第{c['page']}页）：{c['text']}" for c in profile["captions"])


def _abstract_fallback(pdf_path):
    """摘要/亮点兜底（归纳、图表题共用）：主线类问题（目标/内容/结论）的答案
    常在摘要或 Highlights 里，中文问题检索英文正文容易漏掉这些块，
    固定加入备料兜底（本地解析秒级完成，不额外调 LLM）。"""
    try:
        from structured_parser import parse_pdf
        doc = parse_pdf(pdf_path)
        # Front matter 里跳过标题/作者/单位块，挑与答题相关的摘要或 Highlights：
        # ① 「•」开头的 Highlights 块（结论句密度最高，如「V 和 U 的通量在生长阶段更高」）
        # ② 「Abstract」开头的摘要块；③ 都没有就取最长的块
        front = [c for c in doc.chunks if c["section"] == "Front matter"]
        # 注意：chunk 文本带「Front matter: 」章节前缀，• 不在行首，用开头 60 字内查找
        picked = [c for c in front if "•" in c["text"][:60]]
        if not picked:
            picked = [c for c in front if c["text"].strip().lower().startswith("abstract")]
        if not picked:
            picked = [max(front, key=lambda c: len(c["text"]))] if front else []
        if picked:
            return "\n\n【论文摘要/亮点】\n" + _format_hits(picked[:2])
    except Exception:
        pass   # 解析失败不影响主流程，靠检索段作答
    return ""


# ---------- 第 3 步：分题型提示词 ----------
_PROMPTS = {
    "归纳": {
        "system": (
            "你是严谨的科研文献精读助手。下面是从论文中检索到的相关段落（带章节和页码）、"
            "图表说明清单和论文摘要/亮点。请基于这些材料回答关于论文主线的问题。要求："
            "① 先在材料里把与问题相关的全部要点找齐，再逐条作答，每条 1~2 句话，"
            "宁全勿漏——判分按要点清单逐点核对，漏答即失分；"
            "② 问研究内容/方法时，研究地点、研究对象、采样设计、仪器、统计分析等要素"
            "逐项答全，保留关键数字；"
            "③ 回答末尾用「（第X页，章节名）」给出出处；"
            "④ 只有材料里确实没有时才说「论文未明确提及」或「检索到的内容中没有相关信息」，"
            "绝不编造——编造比漏答扣分更重；"
            "⑤ 专业术语保留英文原名。"
        ),
        "prefix": "检索到的段落：",
    },
    "细节": {
        "system": (
            "你是严谨的科研文献精读助手。下面是从论文中检索到的相关段落（带章节和页码）"
            "以及论文的图表说明（caption）清单。请基于这些材料回答问题。要求："
            "① 回答末尾用「（第X页，章节名）」给出出处；"
            "② 如果段落中没有直接答案，先检查图表说明清单——很多数值（浓度、通量、储量）"
            "在图/表里：若答案在图表中，指出图/表编号和页码，并给出图表说明里的相关信息；"
            "③ 只有段落和图表说明都没有时才说「检索到的内容中没有相关信息」，绝不编造；"
            "④ 简洁；⑤ 专业术语保留英文原名。"
        ),
        "prefix": "检索到的段落：",
    },
    "图表": {
        "system": (
            "你是严谨的科研文献精读助手。下面提供了论文的图表说明（caption）清单、"
            "论文摘要/亮点和与问题相关的正文段落。请基于这些材料回答关于图表的问题，"
            "并指出图/表编号和页码。要求："
            "① 先把与问题相关的图注原文逐句完整复述一遍（包括数据归一化方式、"
            "箱线图须/盒子/中线含义、误差线、显著性星号等统计描述，一句不漏），"
            "再给出你的分析——判分按图注要点逐句核对，漏复述即失分；"
            "② 你无法直接查看图片本身；如果正文段落或摘要里讨论了图/表中的关键数据"
            "或结论（如「某个元素通量最高」「某区域储量更大」），优先采用并同时给出"
            "图/表编号和页码；"
            "③ 如果材料不足以回答（如问具体数据趋势而正文也没提到），明确说明这一点，"
            "并告诉用户「建议翻到原图第 X 页查看」。"
        ),
        "prefix": "材料：",
    },
}


def answer(question, pdf_path, model=None, use_rerank=None):
    """
    问答主入口：分流 → 备料 → 作答。
    model：指定对话模型（评测对比 V3 / V4-Flash / V4-Pro 时切换用），默认用配置的 Flash。
    use_rerank：默认读配置（管线固定开）；评测第五轮对照实验 A 组传 False 走旧基线。
    返回 {route, answer, hits}（hits 为检索块，归纳/细节/图表题都有，供界面展示溯源）。
    """
    profile = generate_preanswers(pdf_path)   # 有缓存则秒读
    paper = os.path.basename(pdf_path)
    route = _route(question, model=model)

    if route == "归纳":
        # 修复 2026-09-08 第五轮失分分析：归纳题曾直接读 8 字段预答案作答，
        # 但用户问题可能不在 8 字段范围内（「研究空白」的要点在 Intro 正文里），
        # 且个别论文字段生成质量差（P4 曾甩锅「论文未明确提及」）→
        # 归纳题改走检索正文 + 摘要兜底，预答案只保留在论文档案 tab 展示。
        hits = hybrid_search(question, paper=paper, top_k=TOP_K, use_rerank=use_rerank)
        material = _format_hits(hits) or "（没有检索到相关段落）"
        material += _abstract_fallback(pdf_path)
        material += "\n\n【图表说明清单】\n" + _format_captions(profile)
    elif route == "图表":
        # 修复 2026-09-02 第四轮：caption 只描述图表画了什么、不含图内数据；
        # 「哪种元素通量最高」这类题的答案在正文的讨论句里，
        # 图表题也检索正文兜底，不再只能报图号+页码。
        hits = hybrid_search(question, paper=paper, top_k=TOP_K, use_rerank=use_rerank)
        material = _format_captions(profile)
        # 摘要/亮点兜底（同归纳题）：结论句（如「V 和 U 的通量在植物生长阶段更高」）
        # 在 Highlights 里，中文问题检索英文正文容易漏掉
        material += _abstract_fallback(pdf_path)
        if hits:
            material += "\n\n【正文相关段落】\n" + _format_hits(hits)
    else:
        hits = hybrid_search(question, paper=paper, top_k=TOP_K, use_rerank=use_rerank)
        material = _format_hits(hits) or "（没有检索到相关段落）"
        # 修复 2026-09-02：数值常藏在图/表里，细节题把图表说明清单一并带上兜底，
        # 否则「浓度是多少」这类题正文检索不到就直接 0 分。
        material += "\n\n【图表说明清单】\n" + _format_captions(profile)

    prompt = _PROMPTS[route]
    reply = chat([
        {"role": "system", "content": prompt["system"]},
        {"role": "user", "content": f"{prompt['prefix']}\n{material}\n\n用户问题：{question}"},
    ], model=model, max_tokens=3000)   # B 方案默认思考形态：思考+正文留足余量
    return {"route": route, "answer": reply.strip(), "hits": hits}


if __name__ == "__main__":
    import io
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    PDF = r"C:\Users\17784\Desktop\YRW和组会\文献精读笔记\YRW-潮汐影响氧化还原敏感元素的释放.pdf"
    questions = [
        "这篇论文主要研究了什么？",
        "研究区的潮差是多少？",
        "图5展示了什么？",
    ]
    for q in questions:
        r = answer(q, PDF)
        print(f"问题：{q}\n分流：{r['route']}\n回答：{r['answer']}\n")
        print("-" * 60)
