# -*- coding: utf-8 -*-
"""
知识图谱抽取层（MVP 第 1 天）：个人文献库关系图。

节点 = 论文（preanswers/ 下所有精读过的论文），边 = 5 种关系：
  1. 引用      A 引用了 B            —— 代码确定性匹配（References 全文 vs B 标题）
  2. 同类型研究区  是/否（类型级别）      —— LLM 判断（研究内容字段）
  3. 方法关系  相同/不同/不可比       —— LLM 判断（研究内容字段）
  4. 结论关系  支撑/违背/独立         —— LLM 判断（结论字段）
  5. 问题关系  从属/并列/相互联动/无关联 —— LLM 判断（拟解决问题字段）

设计原则（方案书 v2，2026-09-06 用户拍板，2026-09-07 去共引）：
- 引用交给代码而不是模型：「能查表验证的绝不让模型猜」，天然零编造
- 研究区域按「类型」判断不按「地点」：黄河口湿地 vs 长江口湿地 = 同属河口湿地
- 每条 LLM 判断必须带 evidence（对应字段原文句）
- 「不可比 / 无关联 / 否 / 独立」不连线（不硬凑边），判定仍记录在 skipped 里供评测
- 关系缓存：relations/library_graph.json，按「库指纹」（文件名集合 + 各自 8 字段
  生成时间 + 提示词版本）校验，变化后重抽；新增论文只补新边（增量 O(1)，见方案书）
"""
import json
import os
import re
import time

from console_utils import safe_print
from llm import chat
from preanswer import PRE_DIR, _PROMPT_VERSION
from structured_parser import parse_pdf_smart

REL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "relations")
GRAPH_FILE = os.path.join(REL_DIR, "library_graph.json")

# 增量预筛选：新论文只和向量相似度 top-8 的库内论文做 LLM 判断（方案书拍板）
PRESCREEN_K = 8
# 标题匹配的最短长度：太短的标题子串匹配容易误报
MIN_TITLE_MATCH = 25

# ---------- 引文提取（代码匹配，零编造） ----------

# PDF 提取英文常带排版连字（ﬁ 是一个字符不是 f+i），归一化前展开成普通字母，
# 否则「insigniﬁcant」归一化成「insigni cant」，标题匹配永远对不上。
_LIGATURES = str.maketrans({"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl",
                            "’": "'", "‘": "'", "“": '"', "”": '"'})


def _norm_title(t):
    """标题归一化：连字展开 → 小写 → 非字母数字变空格 → 压缩空白。"""
    t = (t or "").translate(_LIGATURES)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t.lower()).split())


def _references_text(pdf_path):
    """取一篇论文的 References 章节全文（归一化前先小写，便于标题匹配）。"""
    try:
        parsed = parse_pdf_smart(pdf_path)
        parts = [c["text"] for c in parsed.chunks if "eference" in c["section"]]
        return _norm_title("\n".join(parts))
    except Exception:
        return ""


def _code_edges(papers):
    """引用：两两代码匹配。返回 (edges, skipped)。
    papers: [{file, pdf_path, title}]。"""
    edges, skipped = [], []
    refs_cache = {}
    for a in papers:
        refs_cache[a["file"]] = _references_text(a["pdf_path"])
    for i, a in enumerate(papers):
        for b in papers[i + 1:]:
            # ① 引用：A 的 References 全文里能找到 B 的标题（双向都查）
            cited, direction = False, ""
            ta, tb = _norm_title(a["profile"].get("title", "")), _norm_title(b["profile"].get("title", ""))
            if len(tb) >= MIN_TITLE_MATCH and tb in refs_cache[a["file"]]:
                cited, direction = True, f"{a['file']} 引用了 {b['file']}"
            if not cited and len(ta) >= MIN_TITLE_MATCH and ta in refs_cache[b["file"]]:
                cited, direction = True, f"{b['file']} 引用了 {a['file']}"
            if cited:
                edges.append({"a": a["file"], "b": b["file"], "type": "引用",
                              "value": direction, "evidence": "（代码匹配：参考文献全文含对方标题）",
                              "source": "code"})
    return edges, skipped


# ---------- LLM 关系判断（3 次调用/对） ----------

def _pair_material(pa, pb):
    """两篇论文的 8 字段关键内容 → 一段对比材料。"""
    def block(p):
        prof = p["profile"]
        f = {x["field"]: x["text"] for x in prof["fields"]}
        return (f"【论文】《{prof.get('title') or p['file']}》\n"
                f"研究内容：{f.get('研究内容', '')}\n"
                f"结论：{f.get('结论', '')}\n"
                f"拟解决问题：{f.get('拟解决问题', '')}")
    return f"{block(pa)}\n\n{block(pb)}"


_SYSTEM_COMMON = (
    "你是严谨的科研文献关系分析师。基于给出的两篇论文的字段内容判断关系，"
    "只基于材料，不要脑补材料外的信息；证据必须引用材料里的原句。"
    "只输出 JSON，不要任何多余文字。"
)

_AREA_METHOD_SYSTEM = _SYSTEM_COMMON + (
    " 判断两项：\n"
    "1. 同类型研究区（宽口径判断）：满足以下任一条件即判「是」——"
    "① 两篇研究同一地理区域/水环境系统（如都研究黄河口区域：一篇做水质、一篇做湿地沉积物，"
    "也算「是」）；② 两篇的研究区域属于同一生态类型（如河口湿地、远岸大洋、近岸海水、土壤、"
    "红树林）——黄河口湿地 vs 长江口湿地算「是」（都是河口湿地）；太平洋采样 vs 印度洋采样算"
    "「是」（都是远岸大洋）。不要因为具体地点不同或研究对象不同（水质 vs 沉积物 vs 湿地）就判否，"
    "只要地理区域相同或生态类型相同就判「是」。\n"
    "2. 方法关系：相同（同类方法）/ 不同（同一问题不同解法，如一篇数据+模拟、另一篇实验+分析+推论）/ "
    "不可比（主题差异太大，无法比较方法）。\n"
    '输出格式：{"same_area": "是/否", "area_evidence": "两篇研究区域的原文描述", '
    '"method": "相同/不同/不可比", "method_evidence": "两篇方法描述的原句"}'
)

_CONCLUSION_SYSTEM = _SYSTEM_COMMON + (
    " 判断两篇论文结论的关系：支撑（结论互相印证）/ 违背（结论矛盾）/ 独立（无交集、无互证）。\n"
    '输出格式：{"relation": "支撑/违背/独立", "evidence": "两篇结论的原句"}'
)

_QUESTION_SYSTEM = _SYSTEM_COMMON + (
    " 判断两篇论文科学问题（拟解决问题）的结构关系：从属（A 是 B 的子问题）/ "
    "并列（同主题下互不包含）/ 相互联动（彼此依赖共同推进）/ 无关联。\n"
    '输出格式：{"relation": "从属/并列/相互联动/无关联", "evidence": "两篇拟解决问题的原句"}'
)


def _parse_json(text):
    """从模型输出里抠出 JSON（容忍 ```json 包裹、前后杂字）。"""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.M)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def _llm_judge(system, material):
    """一次 LLM 判断，JSON 解析失败重试一次，还失败返回 None。"""
    prev = ""
    for _ in range(2):
        extra = f"\n\n上一次输出无法解析成 JSON，请严格只输出 JSON：{prev}" if prev else ""
        out = chat([{"role": "system", "content": system},
                    {"role": "user", "content": material + extra}],
                   temperature=0.2, max_tokens=1200)
        parsed = _parse_json(out)
        if parsed:
            return parsed
        prev = out[:300]
    return None


# 「不连线」的值域（不硬凑边，判定存 skipped 供评测核对）
_SKIP_VALUES = {"同类型研究区": {"否"}, "方法关系": {"不可比"},
                "结论关系": {"独立"}, "问题关系": {"无关联"}}


def _llm_edges(pa, pb):
    """一对论文的 3 次 LLM 判断（区域+方法、结论、问题）。返回 (edges, skipped)。"""
    edges, skipped = [], []
    material = _pair_material(pa, pb)

    r1 = _llm_judge(_AREA_METHOD_SYSTEM, material)
    if r1:
        # 注意 evidence 键名：area_evidence / method_evidence（与提示词输出格式一致）
        for key, etype, ekey in (("same_area", "同类型研究区", "area_evidence"),
                                 ("method", "方法关系", "method_evidence")):
            v = str(r1.get(key, "")).strip()
            if not v:
                continue
            if v in _SKIP_VALUES[etype]:
                skipped.append({"a": pa["file"], "b": pb["file"], "type": etype,
                                "value": v, "evidence": r1.get(ekey, r1.get("evidence", ""))})
            else:
                edges.append({"a": pa["file"], "b": pb["file"], "type": etype,
                              "value": v, "source": "llm",
                              "evidence": r1.get(ekey, r1.get("evidence", ""))})

    r2 = _llm_judge(_CONCLUSION_SYSTEM, material)
    if r2:
        v = str(r2.get("relation", "")).strip()
        if v in _SKIP_VALUES["结论关系"]:
            skipped.append({"a": pa["file"], "b": pb["file"], "type": "结论关系",
                            "value": v, "evidence": r2.get("evidence", "")})
        elif v:
            edges.append({"a": pa["file"], "b": pb["file"], "type": "结论关系",
                          "value": v, "source": "llm", "evidence": r2.get("evidence", "")})

    r3 = _llm_judge(_QUESTION_SYSTEM, material)
    if r3:
        v = str(r3.get("relation", "")).strip()
        if v in _SKIP_VALUES["问题关系"]:
            skipped.append({"a": pa["file"], "b": pb["file"], "type": "问题关系",
                            "value": v, "evidence": r3.get("evidence", "")})
        elif v:
            edges.append({"a": pa["file"], "b": pb["file"], "type": "问题关系",
                          "value": v, "source": "llm", "evidence": r3.get("evidence", "")})

    return edges, skipped


# ---------- 论文级向量（增量预筛选用） ----------

def _paper_vector(profile):
    """论文级向量：标题 + 拟解决问题 + 研究内容 → bge-m3（一次 embedding）。
    向量缓存在 profile 缓存里（paper_vector 字段），重算一次后永久复用。"""
    if profile.get("paper_vector"):
        return profile["paper_vector"]
    f = {x["field"]: x["text"] for x in profile["fields"]}
    text = f"{profile.get('title', '')}\n{f.get('拟解决问题', '')}\n{f.get('研究内容', '')}"
    from vector_store import _batch_embed
    vec = _batch_embed([text[:3000]])[0]
    profile["paper_vector"] = vec
    with open(os.path.join(PRE_DIR, profile["paper"] + ".json"), "w", encoding="utf-8") as fh:
        json.dump(profile, fh, ensure_ascii=False, indent=2)
    return vec


def _cosine(v1, v2):
    """两个向量的余弦相似度（纯 Python，1024 维 × 几篇论文开销可忽略）。"""
    dot = sum(a * b for a, b in zip(v1, v2))
    n1 = sum(a * a for a in v1) ** 0.5
    n2 = sum(b * b for b in v2) ** 0.5
    return dot / (n1 * n2) if n1 and n2 else 0.0


# ---------- 库与缓存 ----------

# PDF 原始文件的已知存放目录（按文件名匹配找原文，引文提取需要）
_UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploaded")
_PDF_SEARCH_DIRS = [
    _UPLOAD_DIR,
    r"C:\Users\17784\research-ai-assistant\科研文献助手人机交互资料\题库论文PDF",
    r"C:\Users\17784\Desktop\YRW和组会\文献精读笔记",
]


def _library():
    """库成员：preanswers/ 下所有论文缓存（精读过的论文）。返回
    [{file, pdf_path, profile}]。找不到 PDF 原文件的跳过（引文提取需要原文）。"""
    papers = []
    for name in sorted(os.listdir(PRE_DIR)):
        if not name.endswith(".pdf.json"):
            continue
        with open(os.path.join(PRE_DIR, name), encoding="utf-8") as f:
            profile = json.load(f)
        file_name = name[:-5]   # 去掉 .json → 原始 PDF 文件名
        pdf_path = None
        for d in _PDF_SEARCH_DIRS:
            candidate = os.path.join(d, file_name)
            if os.path.exists(candidate):
                pdf_path = candidate
                break
        if pdf_path:
            papers.append({"file": file_name, "profile": profile, "pdf_path": pdf_path})
        else:
            safe_print(f"跳过「{file_name}」：三个目录里都找不到 PDF 原文件")
    return papers


def _library_fingerprint(papers):
    """库指纹：文件名集合 + 各自的 8 字段生成时间 + 提示词版本。
    论文重新预处理（generated_at 变）或提示词升级 → 指纹变 → 重抽。"""
    items = sorted((p["file"], p["profile"].get("generated_at", ""),
                    p["profile"].get("prompt_version", "")) for p in papers)
    return repr(items)


def build_graph(force=False, verbose=False):
    """主入口：全量建图（或读缓存）。返回 {papers, edges, skipped, generated_at}。
    增量逻辑：缓存指纹的文件集合比新库少 → 只补新论文相关的边（与已有论文的
    代码匹配 + 向量预筛选 top-8 的 LLM 判断），旧边原样保留。"""
    papers = _library()
    papers = [p for p in papers if p["pdf_path"]]
    if len(papers) < 2:
        safe_print(f"库里论文不足 2 篇（有效 {len(papers)} 篇），无法建图")
        return {"papers": papers, "edges": [], "skipped": [], "generated_at": ""}
    fingerprint = _library_fingerprint(papers)
    os.makedirs(REL_DIR, exist_ok=True)

    old = None
    if os.path.exists(GRAPH_FILE):
        with open(GRAPH_FILE, encoding="utf-8") as f:
            old = json.load(f)

    if old and old.get("library_fingerprint") == fingerprint and not force:
        safe_print(f"关系缓存有效（{len(old['edges'])} 条边），直接读取")
        return old

    # ---------- 全量重建 / 增量补边 ----------
    old_files = {p["file"] for p in old["papers"]} if old else set()
    old_times = {p["file"]: p.get("generated_at", "") for p in old["papers"]} if old else {}
    added = [p for p in papers if p["file"] not in old_files]
    # 重新预处理过的旧论文（generated_at 变了）：和新增论文一样走增量重判
    changed = [p for p in papers if p["file"] in old_files
               and old_times.get(p["file"]) != p["profile"].get("generated_at", "")]
    touched = added + changed
    pairs = []

    if old and touched and len(touched) < len(papers):
        # 增量：只重判「新论文 / 重新预处理论文」相关的边，其余旧边原样保留。
        # 2026-09-06 用户反馈：上传库里已有的论文会触发全量重抽、等十几分钟——
        # 现在重新预处理和新增论文一样，只对变化论文做向量预筛选 top-8 重判。
        touched_files = {p["file"] for p in touched}
        edges = [e for e in old["edges"]
                 if e["a"] not in touched_files and e["b"] not in touched_files]
        skipped = [s for s in old.get("skipped", [])
                   if s["a"] not in touched_files and s["b"] not in touched_files]
        seen = set()
        for np in touched:
            np_vec = _paper_vector(np["profile"])
            sims = sorted(((_cosine(np_vec, _paper_vector(op["profile"])), op)
                           for op in papers if op["file"] != np["file"]),
                          key=lambda x: x[0], reverse=True)[:PRESCREEN_K]
            tag = "重新预处理" if np["file"] in {p["file"] for p in changed} else "新增"
            safe_print(f"增量（{tag}）：「{np['file'][:40]}」预筛选 top-{len(sims)} 相似论文")
            for _, op in sims:
                key = tuple(sorted((np["file"], op["file"])))
                if key not in seen:
                    seen.add(key)
                    pairs.append((np, op))
    else:                             # 全量：两两配对，旧边清空重抽
        edges, skipped = [], []
        # （首次建图 / 变化的论文太多时不值得增量，直接全量）
        pairs = [(papers[i], papers[j]) for i in range(len(papers))
                 for j in range(i + 1, len(papers))]

    code_edges, _ = _code_edges(papers)   # 引用全量做（本地匹配零成本）
    edges.extend(e for e in code_edges
                 if not any(x["type"] == e["type"] and {x["a"], x["b"]} == {e["a"], e["b"]}
                            for x in edges))

    # 2026-09-06 提速：LLM 判断从串行改 4 线程并行（全是网络等待，并行提速约 4 倍）。
    # 全量 45 对从约 15 分钟压到 4 分钟；增量 8 对从约 1 分钟压到约 20 秒。
    t0 = time.time()
    from concurrent.futures import ThreadPoolExecutor, as_completed
    done = 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(_llm_edges, pa, pb): (k, pa, pb)
                for k, (pa, pb) in enumerate(pairs, 1)}
        for fut in as_completed(futs):
            k, pa, pb = futs[fut]
            e2, s2 = fut.result()
            edges.extend(e2)
            skipped.extend(s2)
            done += 1
            if verbose:
                safe_print(f"  [{k}/{len(pairs)}] {pa['file'][:30]} ↔ {pb['file'][:30]} "
                           f"+{len(e2)} 边，跳过 {len(s2)}")
            if done % 10 == 0:
                safe_print(f"  进度 {done}/{len(pairs)} 对，用时 {time.time() - t0:.0f} 秒")

    # 发表年份元数据在按钮等待期间一起抽掉（graph_html 渲染时只读缓存、零网络调用）
    _ensure_years([p["file"] for p in papers])
    # 作者/期刊元数据同理（节点浮窗用，2026-09-06 用户拍板）
    _ensure_meta([p["file"] for p in papers])

    graph = {
        "library_fingerprint": fingerprint,
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
        "papers": [{"file": p["file"], "title": p["profile"].get("title", ""),
                    "generated_at": p["profile"].get("generated_at", "")} for p in papers],
        "edges": edges,
        "skipped": skipped,
    }
    with open(GRAPH_FILE, "w", encoding="utf-8") as f:
        json.dump(graph, f, ensure_ascii=False, indent=2)
    safe_print(f"✅ 已保存关系图：{GRAPH_FILE}（{len(edges)} 条边，{len(skipped)} 条不连线判定）")
    return graph


def load_graph():
    """只读关系缓存（图谱 tab 打开时秒开用）。绝不触发抽取——重抽只发生在点
    「更新图谱」按钮时。文件不存在/损坏时返回空图结构，由界面提示首次建图。
    2026-09-06 修复：原来界面每次渲染都调 build_graph()，库里一有新论文就自动
    全量重抽十几分钟，整个页面（包括提问框）被卡死。"""
    if os.path.exists(GRAPH_FILE):
        try:
            with open(GRAPH_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"papers": [], "edges": [], "skipped": [], "generated_at": ""}


# ---------- 渲染层（MVP 第 2 天）：pyvis 交互图 ----------

# 边颜色：一类一色。2026-09-06 第二轮按用户反馈重选：
# 原「方法=赭石 vs 结论=暗红」色相太近分不清，改成黄褐 vs 玫红（色相拉开约 55°）。
# 同日用户拍板：① 共引关系去掉（证据太弱，2026-09-07 已从抽取层删除）；
# ② 「同研究区」改名「同类型研究区」（强调按生态类型判断，不是具体地点）。
# _TYPE_ALIAS 让旧缓存里的旧名照常显示（关系 json 不重抽也能正确上图）。
_TYPE_ALIAS = {"同研究区": "同类型研究区"}
EDGE_COLORS = {
    "引用": "#1d4ed8",        # 蓝
    "同类型研究区": "#047857",  # 绿
    "方法关系": "#ca8a04",      # 黄褐
    "结论关系": "#be123c",      # 玫红
    "问题关系": "#7c3aed",      # 紫
}
EDGE_LEGEND = {
    "引用": "谁引用了谁", "同类型研究区": "同一生态/生境类型",
    "方法关系": "方法相同或不同", "结论关系": "结论支撑或违背", "问题关系": "科学问题的结构关系",
}


def _short_label(name, limit=22):
    """节点标签：文件名去 .pdf，超长截断。"""
    s = name[:-4] if name.lower().endswith(".pdf") else name
    return s if len(s) <= limit else s[:limit - 1] + "…"


# ---------- 发表年份元数据（渲染用：节点大小按年份远近） ----------

YEAR_FILE = os.path.join(REL_DIR, "library_years.json")


def _ensure_years(files, force=False):
    """每篇论文的发表年份。一次 LLM 调用批量判断（只传标题 + 摘要片段，不传全文，
    约 ¥0.05），结果缓存 relations/library_years.json；抽不出的记 null，下次不重抽。
    失败兜底：年份缺失不影响图谱渲染，节点退回默认大小。"""
    try:
        with open(YEAR_FILE, encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        cache = {}
    missing = list(files) if force else [f for f in files if f not in cache]
    if not missing:
        return cache
    infos = []
    for f in missing:
        title, content = f, ""
        try:
            with open(os.path.join(PRE_DIR, f + ".json"), encoding="utf-8") as fp:
                prof = json.load(fp)
            title = prof.get("title") or f
            fields = {x["field"]: x["text"] for x in prof.get("fields", [])}
            content = ((fields.get("研究意义") or "")[:120] + " | "
                       + (fields.get("研究内容") or "")[:120])
        except Exception:
            pass
        infos.append(f"{f}\n  标题：{title}\n  摘要片段：{content}")
    prompt = ("以下是个人文献库中的论文（文件名 / 标题 / 摘要片段）。请给出每篇论文的"
              "发表年份最佳估计（best estimate），只输出一个 JSON：{\"文件名\": 四位年份}。"
              "判断顺序：① 你认识的知名论文（如 Nature Communications 等期刊文章）直接答"
              "已知年份；② 不认识的按摘要推断——摘要里的数据时间范围通常比发表年早 0~2 年"
              "（如『2011—2022 年数据』≈ 2023 年发表）；③ 硕士/学位论文按数据截止年份推；"
              "④ 实在没有任何线索才写 null。宁可给近似年，不要大量 null。\n\n"
              + "\n\n".join(infos))
    try:
        text = chat([{"role": "user", "content": prompt}], temperature=0)
        m = re.search(r"\{.*\}", text, re.S)
        data = json.loads(m.group(0)) if m else {}
        for f in missing:
            v = data.get(f)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                cache[f] = int(v) if 1950 <= int(v) <= 2027 else None
            elif isinstance(v, str) and v.strip().isdigit():
                y = int(v.strip())
                cache[f] = y if 1950 <= y <= 2027 else None
            else:
                cache[f] = None
    except Exception:
        for f in missing:
            cache.setdefault(f, None)
    os.makedirs(REL_DIR, exist_ok=True)
    with open(YEAR_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)
    return cache


# ---------- 作者/期刊元数据（渲染用：节点浮窗显示题目/作者/期刊） ----------

META_FILE = os.path.join(REL_DIR, "library_meta.json")


def _read_meta():
    """只读作者/期刊缓存（渲染用，绝不触发 LLM 抽取）。"""
    try:
        with open(META_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _first_page_text(paper):
    """直接从 PDF 第 1 页抽文本（最多 1200 字）。
    作者/期刊就印在首页标题下方；但预处理缓存的 sections 不含第 1 页
    （结构化解析器把首页当封面跳过了），2026-09-07 诊断后改为直读 PDF。"""
    try:
        import pymupdf
        with pymupdf.open(os.path.join(_UPLOAD_DIR, paper)) as doc:
            t = doc[0].get_text()
        return re.sub(r"\s+", " ", t)[:1200]
    except Exception:
        return ""


def _ensure_meta(files, force=False, only_missing=False):
    """每篇论文的作者 + 发表期刊。LLM 批量判断，缓存 relations/library_meta.json。
    输入材料 = 标题 + PDF 首页片段（作者/期刊在首页）+ 摘要片段。
    首页也抽不出的（纯封面页等），第二遍只给标题+摘要让模型按已有知识识别，
    并明确「不认识就写 null，绝对不许编造」。
    only_missing=True：只重抽缓存里 authors 或 journal 为 null 的论文。
    失败兜底：缺失不影响图谱渲染，浮窗对应行显示「—」。
    2026-09-06 用户拍板：节点浮窗只显示题目/作者/发表期刊（原来显示拟解决问题/结论）。"""
    cache = _read_meta()
    if force:
        missing = list(files)
    elif only_missing:
        missing = [f for f in files
                   if f in cache and (not cache[f].get("authors") or not cache[f].get("journal"))]
    else:
        missing = [f for f in files if f not in cache]
    if not missing:
        return cache
    infos = []
    for f in missing:
        title, content = f, ""
        try:
            with open(os.path.join(PRE_DIR, f + ".json"), encoding="utf-8") as fp:
                prof = json.load(fp)
            title = prof.get("title") or f
            fields = {x["field"]: x["text"] for x in prof.get("fields", [])}
            first_page = _first_page_text(f)
            content = (("首页片段：" + first_page + " | ") if first_page else "") \
                + ((fields.get("研究意义") or "")[:120] + " | "
                   + (fields.get("研究内容") or "")[:120])
        except Exception:
            pass
        infos.append(f"{f}\n  标题：{title}\n  {content}")
    prompt = ("以下是个人文献库中的论文（文件名 / 标题 / 首页片段 / 摘要片段）。"
              "作者和期刊信息在首页片段里（标题下方的名字行、期刊名行），请优先从首页片段提取。"
              "请给出每篇论文的：\n"
              "authors：作者。英文论文给前 3 位作者，格式如 \"Zhang, Li and Wang\"；"
              "中文论文给前 3 位姓名；学位论文写第一作者姓名。无法判断写 null。\n"
              "journal：发表期刊名。学位论文写 \"学位论文\"；会议论文写会议名。无法判断写 null。\n"
              "只输出一个 JSON：{\"文件名\": {\"authors\": \"...\", \"journal\": \"...\"}}，"
              "不要任何多余文字。宁可给最可能的结果，不要大量 null。\n\n"
              + "\n\n".join(infos))
    try:
        text = chat([{"role": "user", "content": prompt}], temperature=0)
        m = re.search(r"\{.*\}", text, re.S)
        data = json.loads(m.group(0)) if m else {}
        for f in missing:
            v = data.get(f)
            if isinstance(v, dict):
                cache[f] = {"authors": (str(v.get("authors") or "").strip() or None),
                            "journal": (str(v.get("journal") or "").strip() or None)}
            else:
                cache[f] = {"authors": None, "journal": None}
    except Exception:
        for f in missing:
            cache.setdefault(f, {"authors": None, "journal": None})
    # 第二遍兜底（2026-09-07 用户提议「读不出就把标题+摘要发给大模型识别」）：
    # 只给标题让模型按已有学术知识识别，不认识必须写 null，不许编造。
    still = [f for f in missing
             if not cache[f].get("authors") or not cache[f].get("journal")]
    if still:
        infos2 = []
        for f in still:
            title = f
            try:
                with open(os.path.join(PRE_DIR, f + ".json"), encoding="utf-8") as fp:
                    prof = json.load(fp)
                title = prof.get("title") or f
            except Exception:
                pass
            infos2.append(f"{f}\n  标题：{title}")
        prompt2 = ("以下论文（文件名/标题）的作者或期刊没从论文首页识别出来，"
                   "请根据你对学术文献的了解给出 authors（前 3 位作者，格式 "
                   "\"Zhang, Li and Wang\"；中文论文给前 3 位姓名）和 journal（期刊全称）。"
                   "规则：只写你确实知道的；不认识、不确定的一律写 null，"
                   "绝对不许编造作者或期刊。学位论文 journal 写 \"学位论文\"。\n"
                   "只输出一个 JSON：{\"文件名\": {\"authors\": \"...\", \"journal\": \"...\"}}\n\n"
                   + "\n\n".join(infos2))
        try:
            text2 = chat([{"role": "user", "content": prompt2}], temperature=0)
            m2 = re.search(r"\{.*\}", text2, re.S)
            data2 = json.loads(m2.group(0)) if m2 else {}
            for f in still:
                v = data2.get(f)
                if isinstance(v, dict):
                    if not cache[f].get("authors"):
                        cache[f]["authors"] = (str(v.get("authors") or "").strip() or None)
                    if not cache[f].get("journal"):
                        cache[f]["journal"] = (str(v.get("journal") or "").strip() or None)
        except Exception:
            pass
    os.makedirs(REL_DIR, exist_ok=True)
    with open(META_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)
    return cache


def _tip_text(s, n):
    """浮窗文本清洗：去开头的「作者」套话（用户 2026-09-06 反馈）、压空白、截断。"""
    s = (s or "—").strip()
    s = re.sub(r"^作者", "", s)
    s = re.sub(r"\s+", " ", s)
    return s if len(s) <= n else s[:n - 1] + "…"


def _esc(s):
    """HTML 转义（浮窗内容来自论文文本，可能含 < > &）。"""
    import html
    return html.escape(str(s), quote=False)


# 自定义多行浮窗 + 点击聚焦的 JS/CSS 模板。%s1 = 节点浮窗 JSON，%s2 = 边浮窗 JSON，
# %s3 = 边端点对 JSON（[[a, b], ...]，与 edgeTips 同序，供兜底命中检测用）。
# 不用浏览器原生 tooltip：原生是单行文字气泡，长内容被截断、不认 HTML（用户验收反馈）。
_KG_JS = """
<style>
#kg-tip{position:fixed;display:none;z-index:9999;max-width:380px;background:#fff;
border:1px solid #cbd5e1;border-radius:10px;box-shadow:0 6px 20px rgba(15,23,42,.18);
padding:12px 14px;font:13px/1.7 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
color:#1e293b;pointer-events:none}
#kg-tip .kg-title{font-weight:700;font-size:14px;color:#0f172a;margin-bottom:6px;
border-bottom:1px solid #e2e8f0;padding-bottom:6px;word-break:break-all}
#kg-tip .kg-row{margin-top:6px;word-break:break-word}
#kg-tip .kg-k{display:inline-block;color:#2563eb;font-weight:600;margin-right:6px}
</style>
<div id="kg-tip"></div>
<script>
(function(){
  var nodeTips = %s;
  var edgeTips = %s;
  var edgePairs = %s;
  var allIds = network.body.data.nodes.getIds();
  var tip = document.getElementById("kg-tip");
  // 鼠标坐标用 document 级 mousemove 自己记录：vis.js 新版 hover 事件对象里
  // 不一定带 event/pointer 字段，依赖它们会取不到坐标、回调直接崩（浮窗不弹）。
  var lastX = 8, lastY = 8;
  document.addEventListener("mousemove", function(ev){ lastX = ev.clientX; lastY = ev.clientY; });
  function show(x,y,html){
    tip.innerHTML = html; tip.style.display = "block";
    var w = tip.offsetWidth, h = tip.offsetHeight;
    // 浮窗贴右/下边缘时翻到另一侧，保证完整可见（用户反馈：别被遮住）
    var lx = (x + 16 + w > window.innerWidth)  ? x - 16 - w : x + 16;
    var ly = (y + 16 + h > window.innerHeight) ? y - 16 - h : y + 16;
    tip.style.left = Math.max(4, lx) + "px";
    tip.style.top  = Math.max(4, ly) + "px";
  }
  function hide(){ tip.style.display = "none"; }
  // tipMode 记录浮窗当前由谁占用：vis 的 hover 事件和下面的 mousemove 兜底
  // 都可能触发显示，用同一个状态位避免「事件路径显示、兜底路径又把它藏掉」打架。
  var tipMode = null;
  function showTip(html, mode){ tipMode = mode; show(lastX, lastY, html); }
  network.on("hoverNode", function(p){
    var t = nodeTips[p.node];
    if (t) { showTip(t, "node"); } else { tipMode = null; hide(); }
  });
  network.on("blurNode", function(){ if (tipMode === "node") { tipMode = null; hide(); } });
  network.on("hoverEdge", function(p){
    // vis.js 给无 id 边按添加顺序自配 1 起的数字 id，edgeTips 是顺序列表 → 直接索引
    var t = edgeTips[p.edge - 1];
    if (t) { showTip(t, "edge"); } else { tipMode = null; hide(); }
  });
  network.on("blurEdge", function(){ if (tipMode === "edge") { tipMode = null; hide(); } });
  // 兜底（2026-09-06 二轮）：上面的事件在嵌入环境（Streamlit iframe）实测不可靠，
  // 且 vis 9.1.2 的公开 getNodeAt 实测在节点正中心都返回空——这里完全不依赖 vis
  // 事件与查询接口，mousemove 坐标 + 节点物理坐标 + view 变换自己算命中。
  // 坐标换算（已实测验证）：屏幕坐标 = 物理坐标 × scale + translation。
  document.addEventListener("mousemove", function(ev){
    lastX = ev.clientX; lastY = ev.clientY;
    try {
      var cv = document.querySelector("#mynetwork canvas");
      if (!cv) return;
      var cr = cv.getBoundingClientRect();
      var s = network.body.view.scale;
      var tr = network.body.view.translation;
      var px = (ev.clientX - cr.left - tr.x) / s;
      var py = (ev.clientY - cr.top - tr.y) / s;
      // 先找节点：物理坐标距离 < 节点半径（加 4px 屏幕余量，小节点也好点中）
      var bestN = null, bestND = 1e9;
      for (var i = 0; i < allIds.length; i++) {
        var nd = network.body.nodes[allIds[i]];
        if (!nd) continue;
        var d = Math.sqrt((nd.x - px) * (nd.x - px) + (nd.y - py) * (nd.y - py));
        var r = (nd.options && nd.options.size) ? nd.options.size / 2 : 12;
        if (d < r + 4 / s && d < bestND) { bestND = d; bestN = allIds[i]; }
      }
      if (bestN && nodeTips[bestN]) { showTip(nodeTips[bestN], "node"); return; }
      // 没命中节点再找边：鼠标到「两端节点连线段」的距离（曲线边略有偏移，阈值放宽）
      var bestE = -1, bestED = 1e9;
      for (var k = 0; k < edgePairs.length; k++) {
        var na = network.body.nodes[edgePairs[k][0]];
        var nb = network.body.nodes[edgePairs[k][1]];
        if (!na || !nb) continue;
        var dx = nb.x - na.x, dy = nb.y - na.y;
        var len2 = dx * dx + dy * dy;
        var t2 = len2 ? ((px - na.x) * dx + (py - na.y) * dy) / len2 : 0;
        t2 = Math.max(0, Math.min(1, t2));
        var qx = na.x + t2 * dx, qy = na.y + t2 * dy;
        var d2 = Math.sqrt((px - qx) * (px - qx) + (py - qy) * (py - qy));
        if (d2 < 8 / s && d2 < bestED) { bestED = d2; bestE = k; }
      }
      if (bestE >= 0 && edgeTips[bestE]) { showTip(edgeTips[bestE], "edge"); return; }
      tipMode = null; hide();
    } catch (err) {}
  });
  // 单击节点 → 动画聚焦放大到该节点附近；双击空白处 → 回到全景（2026-09-06 用户要求）
  // 用 getPositions + moveTo 而不是 focus()：moveTo 的动画参数在各版本 vis.js 更稳定。
  function focusNode(id){
    try {
      var pos = network.getPositions([id])[id];
      if (!pos) return;
      network.moveTo({position: pos, scale: 1.7,
                      animation: {duration: 600, easingFunction: "easeInOutQuad"}});
      network.selectNodes([id]);
    } catch (err) {
      // 兜底：moveTo 失败（老版本）退回 focus 无动画版
      try { network.focus(id, {scale: 1.7, animation: false}); } catch (err2) {}
    }
  }
  network.on("click", function(p){
    if (p.nodes.length > 0){ focusNode(p.nodes[0]); }
  });
  network.on("doubleClick", function(p){
    if (p.nodes.length === 0 && p.edges.length === 0){
      try { network.fit({animation: true}); } catch (err) { try { network.fit(); } catch (err2) {} }
    }
  });
})();
</script>
"""


def graph_html(graph, height="660px"):
    """知识图谱 → pyvis 交互图 HTML（vis.js 内联，不依赖外网 CDN）。

    渲染层（2026-09-06 用户验收第二轮改造）：
    - 浮窗 = 自绘多行卡片（原生 tooltip 单行截断且不认 HTML，已弃用）
    - 节点大小按发表年份：年份越近节点越大（_ensure_years 抽一次并缓存）
    - 单击节点 → 动画聚焦；双击空白 → 回到全景
    """
    from pyvis.network import Network

    profiles = {}
    for p in graph["papers"]:
        try:
            with open(os.path.join(PRE_DIR, p["file"] + ".json"), encoding="utf-8") as f:
                profiles[p["file"]] = json.load(f)
        except Exception:
            profiles[p["file"]] = {}

    # 节点尺寸：年份线性映射 14~28；年份未知的中档偏小 16（不误导）
    years = _ensure_years([p["file"] for p in graph["papers"]])
    known = [y for y in years.values() if y]
    ymin, ymax = (min(known), max(known)) if len(known) > 1 else (2015, 2025)

    def _node_size(f):
        y = years.get(f)
        return 14 + (y - ymin) / max(1, ymax - ymin) * 14 if y else 16

    net = Network(height=height, width="100%", bgcolor="#ffffff",
                  font_color="#1e3a5f", directed=False, cdn_resources="in_line")

    # 浮窗内容不进 vis 原生 title（原生气泡单行截断、HTML 标签原样露出），
    # 收集成 JS 字典，注入自定义多行浮窗（见 _KG_JS）。
    # 2026-09-06 用户拍板：节点浮窗只显示 题目/作者/发表期刊；连线浮窗只显示
    # 关系类型 + 判定值（证据句留给评测，不上浮窗）。
    meta = _read_meta()
    node_tips = {}
    for p in graph["papers"]:
        prof = profiles.get(p["file"], {})
        m = meta.get(p["file"], {})
        node_tips[p["file"]] = (
            f'<div class="kg-title">{_esc(_tip_text(prof.get("title") or p["file"], 160))}</div>'
            f'<div class="kg-row"><span class="kg-k">作者</span>'
            f'{_esc(_tip_text(m.get("authors"), 60))}</div>'
            f'<div class="kg-row"><span class="kg-k">期刊</span>'
            f'{_esc(_tip_text(m.get("journal"), 60))}</div>')
        net.add_node(p["file"], label=_short_label(p["file"]),
                     shape="dot", size=_node_size(p["file"]),
                     color={"background": "#2563eb", "border": "#1e40af"})

    edge_tips = []
    edge_pairs = []   # 与 edge_tips 同序的 [a, b] 端点对，兜底命中检测用
    for e in graph["edges"]:
        dtype = _TYPE_ALIAS.get(e["type"], e["type"])   # 旧缓存「同研究区」显示为新名
        # pyvis 0.3.2 生成的边不带 id，vis.js 按添加顺序自配 1 起的数字 id；
        # 同一对论文常有多种关系边（a|b 键会互相覆盖），所以用「添加顺序列表」，
        # JS 侧直接 edgeTips[p.edge - 1] 索引。
        edge_tips.append(
            f'<div class="kg-title">{_esc(dtype)}</div>'
            f'<div class="kg-row">{_esc(e.get("value", ""))}</div>')
        edge_pairs.append([e["a"], e["b"]])
        net.add_edge(e["a"], e["b"], color=EDGE_COLORS.get(dtype, "#64748b"), width=2.5)

    # 力导向布局（节点少，默认物理引擎即可，关掉过强的引力让图舒展）
    net.set_options("""{
      "physics": {
        "barnesHut": {"gravitationalConstant": -4500, "springLength": 220}
      }
    }""")
    html = net.generate_html(notebook=False)
    extra = _KG_JS % (json.dumps(node_tips, ensure_ascii=False),
                      json.dumps(edge_tips, ensure_ascii=False),
                      json.dumps(edge_pairs, ensure_ascii=False))
    return html.replace("</body>", extra + "</body>")


if __name__ == "__main__":
    import io
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    g = build_graph(force="--force" in sys.argv, verbose=True)
    print(f"\n{'=' * 70}\n知识图谱抽取结果（{len(g['papers'])} 篇论文，{len(g['edges'])} 条边）\n{'=' * 70}")
    from collections import Counter
    for etype, n in Counter(_TYPE_ALIAS.get(e["type"], e["type"]) for e in g["edges"]).most_common():
        print(f"  {etype}：{n} 条")
    print(f"\n--- 边清单（人工验收用）---")
    for e in g["edges"]:
        a = e["a"].replace(".pdf", "")[:28]
        b = e["b"].replace(".pdf", "")[:28]
        ev = str(e.get("evidence", ""))[:70].replace("\n", " ")
        print(f"  [{_TYPE_ALIAS.get(e['type'], e['type'])}] {a} ↔ {b}：{e['value']}")
        print(f"      证据：{ev}")
