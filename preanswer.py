# -*- coding: utf-8 -*-
"""
步骤 4：8 字段预答案生成（预处理阶段的核心）

把论文按章节「定向归纳」成 8 个字段，每个字段带出处（章节+页码）：
  研究意义 / 领域问题 / 拟解决问题   ← Introduction
  研究内容                          ← Materials and methods
  主要结果 / 结论                   ← Results+Discussions / Conclusion
  创新点 / 不足与展望                ← 综合提炼

业务规则（PRD 2.0，2026-09-06 修订）：
- 归纳型字段（研究意义/领域问题/拟解决问题/创新点）：专家式专业概括，
  自然语言段落 3~4 句，不写实验细节；用自己语言概括，不照抄原文（学术诚信红线）
- 信息型字段（研究内容/主要结果/结论/不足与展望）：分条要点 + 保留关键数字
- 每条带出处，可溯源到章节/页码
- 严格基于原文；归纳不出来就写「论文未明确提及」，不许编造

产出：preanswers/<论文名>.json（论文档案，tab 展示用）。
2026-09-08 起问答层归纳题改走检索正文，不再直接读它回答。
"""
import json
import os
import time

from console_utils import safe_print
from llm import chat
from structured_parser import parse_pdf_smart

PRE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preanswers")
SECTION_CAP = 20000    # 每个字段喂给模型的最大字符数（12000 曾把长 Methods 章节截断丢细节，提到 20000）

# 提示词版本号：FIELD_PLAN 或两个 _SYSTEM 提示词有任何改动，必须改这个版本号。
# 缓存里会记录生成时的版本，读取时对不上就自动重生成。
# 2026-09-06 教训：提示词升级后旧缓存不失效，用户换论文看到的还是旧版要点式回答。
# 2026-09-08 研究内容字段改五要素模板（失分分析修复 B 档）。
_PROMPT_VERSION = "2026-09-08-elements"

# 字段计划：(字段名, 关键词组, 归纳指令, 模式)
# 模式分两类（2026-09-06 用户验收反馈后拆分）：
#   synth  = 归纳型：要求专家式专业概括（3~4 句自然语言段落），不写实验细节
#   detail = 信息型：分条要点 + 保留关键数字（评测证明细节题需要，不动）
# 关键词组规则：一组是一串章节关键词（子串匹配，兼容 "1. Introduction" 和光秃秃的
# "Introduction"）；组内是并集；组与组之间是兜底关系（如 Nature 论文没有 Introduction
# 章节，就退而用摘要所在的 Front matter）。
# 修复记录 2026-09-02：P2 没有 Methods 章节导致「研究内容」=「无匹配章节」0 分，
# 给各组加多写法关键词 + 摘要兜底；仍全空则代码里还有全文兜底。
FIELD_PLAN = [
    ("研究意义", [["Introduction", "Abstract", "Summary"], ["Front matter"]],
     "这篇研究为什么重要？它回应了什么现实需求或科学需求？请概括研究意义。", "synth"),
    ("领域问题", [["Introduction", "Abstract", "Summary"], ["Front matter"]],
     "该领域目前存在什么问题/空白？作者是怎么论证这个空白的？请概括领域问题。", "synth"),
    ("拟解决问题", [["Introduction", "Abstract", "Summary"], ["Front matter"]],
     "作者想解决的具体科学问题是什么？研究目标或假设是什么？请概括为 2~3 句的简明陈述。", "synth"),
    ("研究内容", [["Methods", "Materials and methods", "Material and methods",
                   "Study area", "Sampling", "Experimental", "Site description", "Data"],
                  ["Front matter"]],
     "作者具体做了什么。请按五要素逐项列出，每项一条：①研究地点 ②研究对象（物种/样品类型）③采样与实验设计（点位数量、频次、样品处理方式等）④仪器与测定方法（如 EA-IRMS）⑤统计分析与图件。保留关键数字，宁全勿漏。", "detail"),
    ("主要结果", [["Results", "Discussion"]],
     "核心发现有哪些？分条列出主要结果，保留关键数值和趋势，并说明作者如何解释这些结果。", "detail"),
    ("结论", [["Conclusion", "Conclusions", "Summary"], ["Discussion"]],
     "论文最终得出的结论是什么？", "detail"),
    ("创新点", [["Introduction", "Discussion", "Conclusion"], ["Front matter"]],
     "这篇论文相对已有研究的创新之处（方法、视角或发现上的突破）。分条列出 2~4 条，每条一句话，不展开细节。", "synth"),
    ("不足与展望", [["Discussion", "Conclusion", "Conclusions", "Summary"],
                    ["Front matter"]],
     "作者承认的局限性，以及对后续研究的建议/展望。", "detail"),
]

# 归纳提示词（中文输出，术语保留英文，防编造，防复述）
# 修复记录 2026-09-02：①「未明确提及」曾导致 P4 等论文 5 个字段全被甩锅（评测 0 分），
# 收紧为「确实完全没有才允许」；④ 从「1~3 句话」改为「分条要点」，保留关键数字，
# 否则采样设计等细节在预答案阶段就被挤掉，归纳题漏要点丢分。
_SUMMARY_SYSTEM = (
    "你是严谨的科研文献精读助手。基于提供的论文片段，用中文归纳要点。"
    "要求：① 按要点分条输出（每条一行），每条 1 句话，最多 8 条；提炼所有关键要点，"
    "包括关键数字（数量、频次、深度、指标名），不要为了简短而省略细节；"
    "② 只提炼要点，禁止复述长段落；③ 专业术语保留英文原名；"
    "④ 只有片段中确实完全没有相关内容时，才允许只回答「论文未明确提及」——"
    "只要片段里有任何沾边的论述，都必须尽力提炼出来，不要因为内容不典型就放弃；"
    "⑤ 不许编造。"
)

# 概括提示词（归纳型字段专用：研究意义/领域问题/拟解决问题/创新点）
# 2026-09-06 用户验收反馈：归纳型字段内容太多、没有概括（如「研究意义」混入
# redox potential 实验细节）。改为专家角色 + 自然语言段落 + 学术诚信硬约束。
_SYNTH_SYSTEM = (
    "你是相关研究领域的资深学者（领域从论文片段判断），正在为学术汇报做专业概括。"
    "基于提供的论文片段，用中文输出。要求："
    "① 输出一段有逻辑的自然语言文本，句子之间要有因果或递进衔接，不要分条列要点；"
    "② 严格控制在 3~4 句话；"
    "③ 只讲宏观层面的意义/问题/主张，不写实验细节、具体数据和过程性描述；"
    "④ 用自己的语言概括，不要照抄原文句子（学术诚信红线）；"
    "⑤ 严谨规范：不夸大、不编造，片段中没有的内容不写；"
    "⑥ 专业术语保留英文原名；"
    "⑦ 只有片段中确实完全没有相关内容时，才允许只回答「论文未明确提及」。"
)


def _parent_map(sections):
    """给每个章节找「父章节」：最近出现、层级比自己小的章节。"""
    stack, parents = [], {}
    for s in sections:
        while stack and stack[-1][0] >= s["level"]:
            stack.pop()
        parents[s["full"]] = stack[-1][1] if stack else None
        stack.append((s["level"], s["full"]))
    return parents


def _top_section(section, parents):
    """沿父链找到所属的一级章节（如 4.2.1 → 4. Discussions）。"""
    while parents.get(section):
        section = parents[section]
    return section


def generate_preanswers(pdf_path, force=False):
    """
    生成（或读取缓存）8 字段预答案。
    返回论文档案 dict：{title, fields[{field,text,source}], sections, captions}
    同一篇论文已生成过就直接读缓存（force=True 强制重新生成）。
    """
    paper = os.path.basename(pdf_path)
    os.makedirs(PRE_DIR, exist_ok=True)
    cache = os.path.join(PRE_DIR, paper + ".json")
    if os.path.exists(cache) and not force:
        with open(cache, encoding="utf-8") as f:
            profile = json.load(f)
        # 提示词版本检查：版本对不上（含旧缓存没有这个字段）→ 强制重生成。
        # 否则提示词升级后，旧论文缓存永远停留在旧格式。
        if profile.get("prompt_version") != _PROMPT_VERSION:
            safe_print(f"提示词已升级（缓存 {profile.get('prompt_version', '无版本')} → "
                       f"{_PROMPT_VERSION}），自动重新生成 8 字段…")
            return generate_preanswers(pdf_path, force=True)
        # 旧缓存可能把摘要当标题（解析器修复前生成）：标题以摘要标志开头 → 运行时置空，
        # 不用 force 重跑也能生效；空标题由界面引导用户填写。
        t = (profile.get("title") or "").strip()
        if t.startswith(("摘 要", "摘要", "Abstract", "ABSTRACT")):
            profile["title"] = ""
        return profile

    parsed = parse_pdf_smart(pdf_path)   # 扫描版 PDF 会自动走 OCR 兜底
    parents = _parent_map(parsed.sections)

    # 按一级章节把正文分组
    by_section = {}
    for c in parsed.chunks:
        by_section.setdefault(_top_section(c["section"], parents), []).append(c)

    fields = []
    safe_print(f"开始生成 8 字段预答案：{parsed.title[:50]}…")
    for name, groups, task, mode in FIELD_PLAN:
        # 挑出该字段需要的章节正文：按组顺序找，第一组有内容就用（组间兜底）
        picked = []
        for group in groups:
            matched = set()   # 一个章节可能同时命中组内多个关键词，去重防重复
            for sec in group:
                for doc_sec, chunks in by_section.items():
                    if doc_sec not in matched and sec.lower() in doc_sec.lower():
                        matched.add(doc_sec)
                        picked.extend(chunks)
            if picked:
                break
        if not picked:
            # 兜底（修复 2026-09-02）：所有关键词组都没匹配到章节时（如短文把方法
            # 塞进 Results and Discussion），用全文喂模型，由提示词判断有无相关内容，
            # 绝不直接写「无匹配章节」——之前 P2 因此「研究内容」0 分。
            picked = [c for cs in by_section.values() for c in cs]
        picked.sort(key=lambda c: (c["page"], c["section"]))
        tops = {_top_section(c["section"], parents) for c in picked}
        cap_each = SECTION_CAP // max(len(tops), 1)
        kept, used_budget = [], {}
        for c in picked:
            top = _top_section(c["section"], parents)
            if used_budget.get(top, 0) >= cap_each:
                continue
            kept.append(c)
            used_budget[top] = used_budget.get(top, 0) + len(c["text"])
        context = "\n\n".join(c["text"] for c in kept)

        if not kept:
            answer = "论文未明确提及"
        else:
            # 归纳型字段用专家概括提示词；信息型字段用分条要点提示词（保留数字）。
            # B 方案（默认思考形态）max_tokens 含思考内容，调大防截断。
            system = _SYNTH_SYSTEM if mode == "synth" else _SUMMARY_SYSTEM
            answer = chat([
                {"role": "system", "content": system},
                {"role": "user",
                 "content": f"【任务】{task}\n\n【论文片段】\n{context}\n\n请直接输出归纳结果。"},
            ], max_tokens=1200 if mode == "synth" else 2000)

        # 出处：用到的章节 + 页码范围
        src_names = []
        for c in kept:
            top = _top_section(c["section"], parents)
            if top not in src_names:
                src_names.append(top)
        pages = [c["page"] for c in kept]
        source = (f"{'、'.join(src_names)}（第 {min(pages)}~{max(pages)} 页）"
                  if kept else "无匹配章节")
        fields.append({"field": name, "text": answer.strip(), "source": source})
        safe_print(f"  ✅ {name}  ← {source}")

    profile = {
        "paper": paper,
        "title": parsed.title,
        "prompt_version": _PROMPT_VERSION,   # 提示词版本，读取时校验（改提示词必须改 _PROMPT_VERSION）
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
        "fields": fields,
        "sections": parsed.sections,
        "captions": parsed.captions,
    }
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)
    safe_print(f"✅ 已保存论文档案：{cache}")
    return profile


if __name__ == "__main__":
    import io
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    # 命令行用法：python preanswer.py "<PDF 路径>" --force
    args = [a for a in sys.argv[1:] if a != "--force"]
    PDF = args[0] if args else r"C:\Users\17784\Desktop\YRW和组会\文献精读笔记\YRW-潮汐影响氧化还原敏感元素的释放.pdf"
    p = generate_preanswers(PDF, force="--force" in sys.argv)
    safe_print(f"\n{'=' * 60}\n论文档案：{p['title']}\n{'=' * 60}")
    for f in p["fields"]:
        safe_print(f"\n{f['field']}（{f['source']}）\n  {f['text']}")
