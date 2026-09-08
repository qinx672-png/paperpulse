# -*- coding: utf-8 -*-
"""
研究路线图生成器 3.0：单篇文献实体-关系流程图（2026-09-07 用户拍板重做）。

思维方式沿用最初的知识图谱方案书 v1（单篇实体图）：
  实体五类：样品 / 方法 / 参数 / 结果 / 结论（拿不准归「其他」）
  关系五种（固定，不让 LLM 自由发挥）：用 / 测得 / 影响 / 属于 / 得出
用意：让读者快速看懂一篇论文的结构关系——谁用什么测了什么、得出什么结论。

输出形式：普通流程图（框 + 线 + 箭头 + 关系词标注），SVG / PNG 双格式。
防编造（产品基因）：每个实体、每条关系必须带原文证据句 + 页码；
界面在流程图下方提供「逐条证据」折叠栏供人工核对（编造率 = 0 硬线）。

流程：8 字段预答案 + 方法原文检索 → LLM 抽取实体+关系（JSON）→ 流程图渲染
缓存：roadmaps/<论文名>.v3.json（与此前五层答辩版不兼容，用 .v3 后缀区分，旧缓存作废）
"""
import html
import io
import json
import os
import re

from PIL import Image, ImageDraw, ImageFont

from console_utils import safe_print
from llm import chat
from preanswer import generate_preanswers, _PROMPT_VERSION
from retriever import hybrid_search

ROADMAP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "roadmaps")

# 路线图缓存版本号：改 _KG_SYSTEM 提示词或渲染逻辑时必须改「roadmap-」前缀部分。
# 掺入 8 字段的提示词版本（@ 后面）——路线图的输入材料就是 8 字段，
# 8 字段提示词升级后路线图缓存自动跟着失效，不用人记两处版本。
_ROADMAP_VERSION = "roadmap-2026-09-07@" + _PROMPT_VERSION

# ---------- 实体/关系 schema（方案书 v1，2026-09-06 定，2026-09-07 复用于路线图） ----------

# 列顺序 = 流程图从左到右的层顺序（「属于」指向的类别归「其他」，放最右）
TYPE_ORDER = ["样品", "方法", "参数", "结果", "结论", "其他"]
# 学术稳重配色：低饱和浅底 + 蓝灰边框（与旧版路线图同色系）
TYPE_COLORS = {
    "样品": "#DCE9E5",
    "方法": "#E3EAF4",
    "参数": "#F3EEDF",
    "结果": "#E8F0E3",
    "结论": "#ECE9F2",
    "其他": "#EFEFEF",
}
# 列头下划线小条用的强调色（对应框底色的加深版，学术稳重）
TYPE_ACCENT = {
    "样品": "#4E7A6C",
    "方法": "#46649E",
    "参数": "#9A7B3C",
    "结果": "#5F7E4A",
    "结论": "#7266A8",
    "其他": "#8C8C8C",
}

_KG_SYSTEM = (
    "你是科研文献精读助手。把一篇论文的关键要素抽成一张实体-关系图，"
    "帮助读者快速看懂这篇论文的结构关系（谁用什么测了什么、得出什么结论）。\n"
    "实体限定以下类型（拿不准的归「其他」）：\n"
    " 样品：研究对象/材料（如「孔隙水」「表层沉积物」「红树林叶片」）\n"
    " 方法：实验/分析手段（如「Rhizon 原位采样」「ICP-MS 测定」）\n"
    " 参数：测定的指标/变量（如「DOC 含量」「Eh」「Fe 释放通量」）\n"
    " 结果：论文报告的核心发现（如「潮汐淹没增强孔隙水 Fe 释放」）\n"
    " 结论：由结果推出的结论/机制解释（如「植物根系供氧促进 Fe 氧化物形成」）\n"
    "关系词固定五种，不允许用其他词：\n"
    " 用（样品→方法）、测得（方法→参数）、影响（参数→参数或结果）、"
    "属于（实体→其上位类别）、得出（结果→结论）\n"
    "数量控制（宁缺毋滥）：实体 10~25 个（每类 ≤ 6），关系 ≤ 28 条。"
    "原文没明确写的不抽，抽不出就少抽。\n"
    "防编造硬线：每个实体、每条关系必须给出 evidence（原文原句，≤60 字）"
    "和 page（页码）。evidence 必须来自下面提供的材料；"
    "材料里找不到依据的实体/关系一律不抽。\n"
    "化学式写平文本（NO3-、Fe2+），渲染时会自动加上下标。\n"
    "只输出 JSON，不要任何多余文字：\n"
    '{"entities": [{"id": "e1", "label": "孔隙水", "type": "样品", '
    '"evidence": "原文句", "page": 2}], '
    '"relations": [{"source": "e1", "target": "e2", "relation": "用", '
    '"evidence": "原文句", "page": 3}]}'
)


def _parse_json(text):
    """从模型输出里抠出 JSON（容忍 ```json 包裹、前后杂字）。"""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.M)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


_METHOD_QUERIES = ["实验方法 样品采集 仪器", "分析方法 测定", "XRF XRD ICP 光谱 分析"]

def _method_material(paper):
    """检索论文里方法相关的原文片段。8 字段要点没有独立的「研究方法」字段，
    直接抽实体时模型会缺材料开始编仪器名，所以把方法章节原文检索出来一起喂给模型。
    检索失败就返回空串（不强求）。"""
    seen, parts = set(), []
    for q in _METHOD_QUERIES:
        try:
            hits = hybrid_search(q, paper=paper, top_k=2)
        except Exception:
            continue
        for h in hits:
            key = (h.get("page"), h.get("text", "")[:80])
            if key in seen:
                continue
            seen.add(key)
            parts.append(f"【论文原文 · 第{h['page']}页 · {h.get('section', '')}】\n"
                         f"{h['text'][:500]}")
    return "\n\n".join(parts)


def _kg_structure(profile, paper, model=None):
    """8 字段要点 + 方法原文检索 → LLM 抽取实体 + 关系。
    解析失败重试一次，还失败返回 None。"""
    material = "\n\n".join(
        f"【{f['field']}】（{f['source']}）\n{f['text']}" for f in profile["fields"])
    material += "\n\n" + _method_material(paper)
    prev = ""
    for _ in range(2):
        extra = (f"\n\n上一次输出无法解析成 JSON，请严格只输出 JSON：{prev}"
                 if prev else "")
        out = chat([{"role": "system", "content": _KG_SYSTEM},
                    {"role": "user", "content": material + extra}],
                   model=model, max_tokens=3000)   # JSON+证据留足余量
        kg = _parse_json(out)
        if kg and kg.get("entities"):
            return kg
        prev = out[:400]
    return None


def generate_roadmap(pdf_path, force=False, model=None):
    """生成（或读缓存）一篇论文的实体-关系图结构。
    返回 {paper, title, entities, relations}，并落盘 roadmaps/<论文名>.v3.json。
    旧五层路线图缓存（.v2.json）与本版不兼容，自动作废（读新后缀，旧文件不碰）。"""
    paper = os.path.basename(pdf_path)
    cache = os.path.join(ROADMAP_DIR, paper + ".v3.json")
    if os.path.exists(cache) and not force:
        with open(cache, encoding="utf-8") as f:
            roadmap = json.load(f)
        # 版本检查：路线图提示词或 8 字段提示词升级后，旧缓存自动失效重生成
        if roadmap.get("roadmap_version") != _ROADMAP_VERSION:
            safe_print(f"路线图提示词已升级（缓存 {roadmap.get('roadmap_version', '无版本')} → "
                       f"{_ROADMAP_VERSION}），自动重新生成…")
            return generate_roadmap(pdf_path, force=True)
        # 旧缓存可能把摘要首句当论文标题（如「摘 要 河口-海岸带湿地是…」）：
        # 以摘要标志开头 → 运行时置空，空标题由界面引导用户填写（同 preanswer 修复）
        t = (roadmap.get("title") or "").strip()
        if t.startswith(("摘 要", "摘  要", "摘要", "Abstract", "ABSTRACT")):
            roadmap["title"] = ""
        return roadmap

    profile = generate_preanswers(pdf_path)   # 8 字段（有缓存则秒读）
    kg = _kg_structure(profile, paper, model=model)
    if kg is None:
        raise RuntimeError("实体-关系抽取失败（两次 JSON 解析都失败）")

    roadmap = {"paper": paper, "title": profile["title"],
               "roadmap_version": _ROADMAP_VERSION,   # 读取时校验（改提示词必须改 _ROADMAP_VERSION）
               "entities": kg.get("entities", []),
               "relations": kg.get("relations", [])}
    os.makedirs(ROADMAP_DIR, exist_ok=True)
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(roadmap, f, ensure_ascii=False, indent=2)
    return roadmap


# ---------- 化学式上下标转换 ----------
# 模型按 prompt 输出平文本化学式（NO3-、NH4+），渲染前统一转成 Unicode 上下标。
# 先整词替换常见离子/分子式，再走通用规则（元素符号后的数字 → 下标；电荷 → 上标）。
_CHEM_IONS = {   # 复合离子/分子：原子数是下标，电荷是上标
    "NO3-": "NO₃⁻", "NO2-": "NO₂⁻", "NH4+": "NH₄⁺", "NH4-N": "NH₄-N",
    "SO42-": "SO₄²⁻", "SO4 2-": "SO₄²⁻", "CO32-": "CO₃²⁻", "PO43-": "PO₄³⁻",
    "HCO3-": "HCO₃⁻", "HS-": "HS⁻", "OH-": "OH⁻",
    "H2S": "H₂S", "H2O": "H₂O", "CO2": "CO₂", "CH4": "CH₄",
    "N2O": "N₂O", "O2": "O₂", "N2": "N₂",
}
_CHEM_METALS = {  # 金属离子：数字是价态 → 上标
    "Fe2+": "Fe²⁺", "Fe3+": "Fe³⁺", "Mn2+": "Mn²⁺", "Cu2+": "Cu²⁺",
    "Zn2+": "Zn²⁺", "Pb2+": "Pb²⁺", "Cd2+": "Cd²⁺", "Ni2+": "Ni²⁺",
    "Co2+": "Co²⁺", "Cr3+": "Cr³⁺", "Al3+": "Al³⁺", "Ca2+": "Ca²⁺",
    "Mg2+": "Mg²⁺", "K+": "K⁺", "Na+": "Na⁺", "Cl-": "Cl⁻",
}
_SUB = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")
_SUP = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def _chem(text):
    """化学式平文本 → Unicode 上下标。普通数字（年份、cm、页数）不受影响。"""
    if not text:
        return text
    for k, v in {**_CHEM_IONS, **_CHEM_METALS}.items():
        text = text.replace(k, v)
    out, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        # 元素符号 + 数字：数字后面紧跟电荷符号 → 价态上标；否则原子数下标
        # 2026-09-02 修复：中文字符 isalpha() 也为真，把「年4月」错转成「年₄月」，
        # 元素符号只可能是 ASCII 字母，加 isascii 限定
        if ch.isascii() and ch.isalpha() and i + 1 < n and text[i + 1].isdigit():
            out.append(ch)
            j = i + 1
            while j < n and text[j].isdigit():
                j += 1
            if j < n and text[j] in "+-" and (j + 1 >= n or not text[j + 1].isdigit()):
                out.append(text[i + 1:j].translate(_SUP))
                out.append("⁺" if text[j] == "+" else "⁻")
                i = j + 1
            else:
                out.append(text[i + 1:j].translate(_SUB))
                i = j
        # 数字后面的电荷符号 → 上标（"2024-2025" 的 - 后跟数字，不动）
        elif ch in "+-" and i > 0 and text[i - 1].isdigit() \
                and (i + 1 >= n or not text[i + 1].isdigit()):
            out.append("⁺" if ch == "+" else "⁻")
            i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


# ---------- 文字排版工具（沿用旧版，SVG/PNG 共用） ----------

def _wrap(text, per_line):
    """中文文本按每行字数拆行（英文术语按词尽量不拆）。"""
    lines, buf = [], ""
    for ch in text:
        buf += ch
        if len(buf) >= per_line:
            lines.append(buf)
            buf = ""
    if buf:
        lines.append(buf)
    return lines or [""]


def _short(text, n):
    """超出 n 字截断加省略号，防止长文字撑爆布局。"""
    text = text or ""
    return text if len(text) <= n else text[:n - 1] + "…"


# 字体文件（Windows 自带）
_SIMSUN = r"C:\Windows\Fonts\simsun.ttc"   # 宋体（中文）
_TIMES = r"C:\Windows\Fonts\times.ttf"     # Times New Roman（英文/数字）
_TIMESBD = r"C:\Windows\Fonts\timesbd.ttf" # Times New Roman Bold
_MSYHBD = r"C:\Windows\Fonts\msyhbd.ttc"   # 微软雅黑 Bold（真粗体，列头专用）

# 英文/数字/常见符号段（其余字符按中文段处理，用宋体画）
# 化学式上下标字符也归英文段：Times New Roman 有这些字形，宋体没有
_EN_RE = re.compile(r"[A-Za-z0-9%.,;:()+/\\\-–—~≈±°×·μµ≥≤<>=\"'₀-₉⁰-⁹⁺⁻]+")


def _segments(text):
    """把一行文字切成 (段文本, 是否英文段) 列表，供中英混排绘制。
    必须用 finditer 而不是 re.split——split 返回的是匹配之间的空隙，
    会把英文段本身丢掉（2026-09-07 用户报「空框」的根因：
    纯英文标签拆完只剩空串，框内一个字都不画）。"""
    segs, pos = [], 0
    for m in _EN_RE.finditer(text):
        if m.start() > pos:
            segs.append((text[pos:m.start()], False))   # 匹配之前的段是中文
        segs.append((m.group(), True))                  # 英文/数字/上下标段
        pos = m.end()
    if pos < len(text):
        segs.append((text[pos:], False))
    return segs


def _text_width(text, pt, bold=False):
    """实测一行文字的画布宽度（px）。中英混排：中文宋体、英文/数字 Times。"""
    size = round(pt)
    en_font = ImageFont.truetype(_TIMESBD if bold else _TIMES, size)
    zh_font = ImageFont.truetype(_SIMSUN, size)
    return sum(en_font.getlength(t) if is_en else zh_font.getlength(t)
               for t, is_en in _segments(text))


def _draw_mixed(draw, x, y, text, pt, fill=(0, 0, 0), anchor="ls", bold=False, scale=1):
    """中英混排绘制：中文用宋体、英文/数字用 Times New Roman；y 是文字基线。
    anchor 只支持 "ls"（左对齐基线）/"ms"（居中基线）。bold 用描边伪加粗。"""
    size = round(pt * scale)
    en_font = ImageFont.truetype(_TIMESBD if bold else _TIMES, size)
    zh_font = ImageFont.truetype(_SIMSUN, size)
    stroke = max(1, scale) if bold else 0   # 宋体没有原生粗体，用描边模拟
    segs = _segments(text)
    widths = [draw.textlength(t, en_font if is_en else zh_font) for t, is_en in segs]
    total = sum(widths)
    if anchor == "ms":
        x -= total / 2
    for (t, is_en), w in zip(segs, widths):
        draw.text((x, y), t, font=en_font if is_en else zh_font, fill=fill,
                  anchor="ls", stroke_width=stroke, stroke_fill=fill)
        x += w


# ---------- 流程图布局（框 + 线，SVG/PNG 共用同一套坐标计算） ----------
# 布局：按实体类型分层（左→右：样品→方法→参数→结果→结论→其他）。
# 每类一列，框在列内自上而下排列；关系画成折线箭头，关系词标在线旁。

_M = 30             # 左右页边距
_GAP = 64           # 列间距（关系词标签和同列回环线都走列间缝隙）
_HEAD_H = 34        # 列头（类型名）高度
_BOX_PAD_Y = 9      # 框内上下边距
_LINE_H = 19        # 框内文字行高
_BOX_MIN_W = 150    # 框宽下限/上限（超上限文字换行）
_BOX_MAX_W = 250
_ROW_GAP = 18       # 同列框间距
_BOX_CHARS = 16     # 框内文字每行字数（先按字数拆行，再实测收窄宽度）

_HEAD_PT = 15       # 列头类型名（2026-09-07 换微软雅黑真粗体，宋体描边伪粗发糊）
_BOX_PT = 12        # 框内实体名
_EDGE_PT = 10.5     # 关系词标注
_BLUE_GRAY = (68, 84, 106)   # 边框/线色 #44546A
_HEAD_FILL = (31, 56, 100)   # 列头文字深藏青 #1F3864，对比度拉满


def _flow_layout(kg):
    """计算流程图各框坐标与每条关系的折线路径。
    返回 (W, H, cols, edges, title_lines)：
    cols = {类型: [(实体, box dict), ...]}，edges = [(relation, points, label_pos, label)]。"""
    ents, rels = kg.get("entities") or [], kg.get("relations") or []
    by_id = {}
    for e in ents:
        e["label"] = (e.get("label") or "").strip() or "?"
        if e.get("type") not in TYPE_ORDER:
            e["type"] = "其他"
        e["lines"] = _wrap(_chem(e["label"]), _BOX_CHARS)
        by_id[e.get("id")] = e
    edges = []
    for r in rels:
        s, t = r.get("source"), r.get("target")
        if s != t and s in by_id and t in by_id and r.get("relation"):
            edges.append(r)

    # 分列（按 TYPE_ORDER），只保留非空列
    col_types = [t for t in TYPE_ORDER if any(e["type"] == t for e in ents)]

    # 列内排序：barycenter 一轮——实体按相连实体的平均位置排，减少线条交叉
    col_ent = {t: [e for e in ents if e["type"] == t] for t in col_types}
    pos = {e["id"]: k for t in col_types for k, e in enumerate(col_ent[t])}
    for t in col_types:
        for e in col_ent[t]:
            neigh = []
            for r in edges:
                if r["source"] == e["id"] and r["target"] in pos:
                    neigh.append(pos[r["target"]])
                elif r["target"] == e["id"] and r["source"] in pos:
                    neigh.append(pos[r["source"]])
            e["_bc"] = sum(neigh) / len(neigh) if neigh else pos[e["id"]]
        col_ent[t].sort(key=lambda e: (e["_bc"], pos[e["id"]]))

    # 每列宽度：按框内文字实测收窄（限 [下限, 上限]）
    col_w = {}
    for t in col_types:
        maxw = _BOX_MIN_W
        for e in col_ent[t]:
            tw = max(_text_width(l, _BOX_PT) for l in e["lines"]) + 44
            maxw = max(maxw, min(round(tw), _BOX_MAX_W))
        col_w[t] = maxw
    W = max(2 * _M + sum(col_w[t] for t in col_types) + _GAP * (len(col_types) - 1),
            900)
    if W > 1500:   # 超宽兜底：等比压缩每列（不小于下限）
        budget = 1500 - 2 * _M - _GAP * (len(col_types) - 1)
        scale = budget / max(1, sum(col_w.values()))
        for t in col_types:
            col_w[t] = max(140, int(col_w[t] * scale))
        W = 2 * _M + sum(col_w[t] for t in col_types) + _GAP * (len(col_types) - 1)

    # 列 x 坐标 + 框 y 坐标（自上而下堆叠）
    col_x = {}
    x = _M
    for t in col_types:
        col_x[t] = x
        x += col_w[t] + _GAP
    col_idx = {t: i for i, t in enumerate(col_types)}
    y = _M            # 2026-09-07 用户拍板：图上不显示论文标题，列从顶部页边距起排
    col_head_y = {}   # 每列头文字基线
    bottom = 0        # 所有框的底边最大值（最后一行距 _ROW_GAP 要去掉）
    for t in col_types:
        yy = y + _HEAD_H
        col_head_y[t] = y + 20
        for e in col_ent[t]:
            h = len(e["lines"]) * _LINE_H + 2 * _BOX_PAD_Y
            e["box"] = {"x": col_x[t], "y": yy, "w": col_w[t], "h": h}
            yy += h + _ROW_GAP
            bottom = max(bottom, yy - _ROW_GAP)
    H = bottom + _M

    def mid_y(e):
        b = e["box"]
        return b["y"] + b["h"] / 2

    def _free_cross_y(sc, tc, y1, y2):
        """跨多列长线的穿越高度：在中间各列的框间缝隙里找同时避开所有中间列的 y。
        找不到返回 None（调用方兜底画直线）。"""
        cands = [y1, y2]
        for i in range(sc + 1, tc):
            t = col_types[i]
            bs = [e["box"] for e in col_ent[t]]
            if not bs:
                continue
            cands.append(bs[0]["y"] - 12)
            cands.append(bs[-1]["y"] + bs[-1]["h"] + 12)
            for a, b in zip(bs, bs[1:]):
                cands.append((a["y"] + a["h"] + b["y"]) / 2)
        best = None
        for c in sorted(cands):
            ok = True
            for i in range(sc + 1, tc):
                for e in col_ent[col_types[i]]:
                    b = e["box"]
                    if b["y"] - 4 < c < b["y"] + b["h"] + 4:
                        ok = False
                        break
                if not ok:
                    break
            if ok:
                d = abs(c - y1) + abs(c - y2)
                if best is None or d < best[0]:
                    best = (d, c)
        return best[1] if best else None

    # 关系折线路径
    loops = {}
    routed = []
    for r in edges:
        s, t = by_id[r["source"]], by_id[r["target"]]
        sb, tb = s["box"], t["box"]
        sc, tc = col_idx[s["type"]], col_idx[t["type"]]
        sy, ty = mid_y(s), mid_y(t)
        rel = _chem(r.get("relation") or "")
        if sc == tc:   # 同列：从框右缘绕列右缝隙回环
            k = loops.get(s["type"], 0)
            loops[s["type"]] = k + 1
            gx = sb["x"] + sb["w"] + 14 + 11 * k
            pts = [(sb["x"] + sb["w"], sy), (gx, sy), (gx, ty), (tb["x"] + tb["w"], ty)]
            routed.append((r, pts, (gx + 5, (sy + ty) / 2 - 5), rel))
        elif sc < tc:
            if tc - sc == 1:   # 相邻列：直角折线走列间缝隙
                gx = sb["x"] + sb["w"] + _GAP / 2
                pts = [(sb["x"] + sb["w"], sy), (gx, sy), (gx, ty), (tb["x"], ty)]
                routed.append((r, pts, ((sb["x"] + sb["w"] + gx) / 2, sy - 5), rel))
            else:
                ystar = _free_cross_y(sc, tc, sy, ty)
                if ystar is None:   # 中间列没缝可穿：兜底直线
                    pts = [(sb["x"] + sb["w"], sy), (tb["x"], ty)]
                    routed.append((r, pts,
                                   ((sb["x"] + sb["w"] + tb["x"]) / 2, (sy + ty) / 2 - 6), rel))
                else:
                    g1 = sb["x"] + sb["w"] + _GAP / 2
                    g2 = tb["x"] - _GAP / 2
                    pts = [(sb["x"] + sb["w"], sy), (g1, sy), (g1, ystar),
                           (g2, ystar), (g2, ty), (tb["x"], ty)]
                    routed.append((r, pts, ((g1 + g2) / 2, ystar - 5), rel))
        else:   # 反向（右→左）：从源框左缘出、进目标框右缘（镜像路径）
            if sc - tc == 1:
                gx = sb["x"] - _GAP / 2
                pts = [(sb["x"], sy), (gx, sy), (gx, ty), (tb["x"] + tb["w"], ty)]
                routed.append((r, pts, ((sb["x"] + gx) / 2, sy - 5), rel))
            else:
                ystar = _free_cross_y(tc, sc, ty, sy)
                if ystar is None:
                    pts = [(sb["x"], sy), (tb["x"] + tb["w"], ty)]
                    routed.append((r, pts,
                                   ((sb["x"] + tb["x"] + tb["w"]) / 2, (sy + ty) / 2 - 6), rel))
                else:
                    g1 = sb["x"] - _GAP / 2
                    g2 = tb["x"] + tb["w"] + _GAP / 2
                    pts = [(sb["x"], sy), (g1, sy), (g1, ystar),
                           (g2, ystar), (g2, ty), (tb["x"] + tb["w"], ty)]
                    routed.append((r, pts, ((g1 + g2) / 2, ystar - 5), rel))

    # 关系词合并（2026-09-07 用户反馈「一根连线上相同的字合并」）：
    # 指向同一目标框、关系词相同的多条线，只保留离目标框最近的那一个标签，
    # 线全部保留——只合字、不删线，避免同一个词叠成一团、互相盖住看不清。
    groups = {}
    for item in routed:
        groups.setdefault((item[3], item[0]["target"]), []).append(item)
    merged = []
    for items in groups.values():
        t = by_id[items[0][0]["target"]]
        tb = t["box"]
        tcx, tcy = tb["x"] + tb["w"] / 2, tb["y"] + tb["h"] / 2
        keep = min(items, key=lambda it:
                   (it[2][0] - tcx) ** 2 + (it[2][1] - tcy) ** 2)
        for item in items:
            r, pts, lpos, rel = item
            merged.append((r, pts, lpos if item is keep else None, rel))
    routed = merged

    # 标签去重叠（2026-09-07 实测 YRW 24 实体 21 关系时标签密集碰撞）：
    # 首选位置被占（压框或压其他标签）就沿主线横移、再上下挪，直到找到空位。
    def _rect_hit(a, b):
        return not (a[0] + a[2] <= b[0] or b[0] + b[2] <= a[0] or
                    a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1])

    boxes = [e["box"] for t in col_types for e in col_ent[t]]
    offs = ([(0, 0)]
            + [(dx, 0) for dx in (6, -6, 12, -12, 18, -18, 24, -24, 30, -30,
                                  36, -36, 42, -42, 50, -50)]
            + [(0, dy) for dy in (9, -9, 18, -18, 27, -27, 36, -36, 45, -45, 54, -54)])
    placed, final = [], []
    for r, pts, lpos, rel in routed:
        if not lpos:   # 同词合并时被去掉的标签，直接放行（线还在）
            final.append((r, pts, None, rel))
            continue
        tw = _text_width(rel, _EDGE_PT) + 8
        base = (lpos[0] - tw / 2, lpos[1] - 12, tw, 16)
        spot = None
        for dx, dy in offs:
            c = (base[0] + dx, base[1] + dy, tw, 16)
            if any(_rect_hit(c, (b["x"], b["y"], b["w"], b["h"])) for b in boxes):
                continue
            if any(_rect_hit(c, q) for q in placed):
                continue
            spot = c
            break
        if spot is None:
            # 实在没空位：压框就干脆不画这个标签（线还在），
            # 绝不能让白底 rect 盖住框内文字（2026-09-07 空框成因之一）
            if any(_rect_hit(base, (b["x"], b["y"], b["w"], b["h"])) for b in boxes):
                final.append((r, pts, None, rel))
                continue
            spot = base
        placed.append(spot)
        final.append((r, pts, (spot[0] + tw / 2, spot[1] + 12), rel))
    routed = final

    cols = {t: col_ent[t] for t in col_types}
    return W, H, cols, routed, col_head_y, col_x, col_w


# ---------- SVG ----------
# 字体：font-family 按字符回退——英文/数字优先 Times New Roman，中文落到 SimSun
_SVG_FONT = '"Times New Roman", SimSun, serif'
_SVG_STROKE = "#44546A"


def _arrowhead(p1, p2):
    """按最后一段线段方向算箭头三角形三点（长度 8、半宽 4.5）。"""
    import math
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    L = math.hypot(dx, dy) or 1
    ux, uy = dx / L, dy / L
    px, py = -uy, ux
    tip = p2
    base = (p2[0] - ux * 8, p2[1] - uy * 8)
    return f"{tip[0]:.1f},{tip[1]:.1f} {base[0] + px * 4.5:.1f},{base[1] + py * 4.5:.1f} " \
           f"{base[0] - px * 4.5:.1f},{base[1] - py * 4.5:.1f}"


def roadmap_svg(roadmap):
    """生成实体-关系流程图 SVG。"""
    W, H, cols, routed, col_head_y, col_x, col_w = _flow_layout(roadmap)
    p = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'font-family="{_SVG_FONT}">',
        f'<rect width="{W}" height="{H}" fill="#fff"/>',
    ]

    # 先画关系线（压在框下面，避免线穿过框内文字）
    for r, pts, lpos, rel in routed:
        poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        p.append(f'<polyline points="{poly}" fill="none" stroke="{_SVG_STROKE}" '
                 f'stroke-width="1.5"/>')
        p.append(f'<polygon points="{_arrowhead(pts[-2], pts[-1])}" fill="{_SVG_STROKE}"/>')

    # 框 + 框内文字
    for t in cols:
        for e in cols[t]:
            b = e["box"]
            fill = TYPE_COLORS.get(e["type"], "#EFEFEF")
            p.append(f'<rect x="{b["x"]}" y="{b["y"]}" width="{b["w"]}" height="{b["h"]}" '
                     f'rx="6" fill="{fill}" stroke="{_SVG_STROKE}" stroke-width="1.5"/>')
            ty = b["y"] + _BOX_PAD_Y + 14
            for line in e["lines"]:
                p.append(f'<text x="{b["x"] + b["w"] // 2}" y="{ty:.1f}" text-anchor="middle" '
                         f'font-size="{_BOX_PT}">{html.escape(line)}</text>')
                ty += _LINE_H

    # 关系词标注（白底小字，压在线条上方保证可读；lpos 为 None 表示放弃该标签）
    for r, pts, lpos, rel in routed:
        if not lpos:
            continue
        tw = _text_width(rel, _EDGE_PT) + 8
        p.append(f'<rect x="{lpos[0] - tw / 2:.1f}" y="{lpos[1] - 12:.1f}" '
                 f'width="{tw:.1f}" height="16" fill="#fff"/>')
        p.append(f'<text x="{lpos[0]:.1f}" y="{lpos[1]:.1f}" text-anchor="middle" '
                 f'font-size="{_EDGE_PT}" fill="{_SVG_STROKE}">{html.escape(rel)}</text>')

    # 列头（类型名，微软雅黑真粗体 15pt 深藏青 + 类型色下划线小条，2026-09-07 换清晰版）
    for t in cols:
        cx = col_x[t] + col_w[t] // 2
        p.append(f'<text x="{cx}" y="{col_head_y[t]}" text-anchor="middle" '
                 f'font-size="{_HEAD_PT}" font-weight="bold" fill="#1F3864" '
                 f'font-family="\'Microsoft YaHei\', \'PingFang SC\', SimHei, sans-serif">'
                 f'{html.escape(t)}</text>')
        p.append(f'<rect x="{cx - 20}" y="{col_head_y[t] + 9}" width="40" height="3" '
                 f'rx="1.5" fill="{TYPE_ACCENT.get(t, "#8C8C8C")}"/>')

    p.append("</svg>")
    return "\n".join(p)


# ---------- PNG ----------

def roadmap_png(roadmap, scale=2):
    """生成实体-关系流程图 PNG（2 倍分辨率更清晰）。返回 bytes。"""
    W, H, cols, routed, col_head_y, col_x, col_w = _flow_layout(roadmap)
    img = Image.new("RGB", (W * scale, H * scale), "white")
    draw = ImageDraw.Draw(img)
    fill_map = {k: tuple(int(v[i:i + 2], 16) for i in (1, 3, 5))
                for k, v in TYPE_COLORS.items()}

    def dm(x, y, text, pt, anchor="ls", bold=False, fill=(0, 0, 0)):
        """_draw_mixed 的坐标放大版（x/y 是逻辑坐标，内部乘 scale）。"""
        _draw_mixed(draw, x * scale, y * scale, text, pt,
                    fill=fill, anchor=anchor, bold=bold, scale=scale)

    # 关系线（先画，框后画压上去）
    for r, pts, lpos, rel in routed:
        draw.line([(x * scale, y * scale) for x, y in pts],
                  fill=_BLUE_GRAY, width=max(1, round(1.5 * scale)))
        # 箭头（按最后一段方向）
        import math
        p1, p2 = pts[-2], pts[-1]
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        L = math.hypot(dx, dy) or 1
        ux, uy = dx / L, dy / L
        px, py = -uy, ux
        tip = (p2[0] * scale, p2[1] * scale)
        base = ((p2[0] - ux * 8) * scale, (p2[1] - uy * 8) * scale)
        draw.polygon([tip,
                      (base[0] + px * 4.5 * scale, base[1] + py * 4.5 * scale),
                      (base[0] - px * 4.5 * scale, base[1] - py * 4.5 * scale)],
                     fill=_BLUE_GRAY)

    # 框（圆角矩形）+ 框内文字（居中）
    for t in cols:
        for e in cols[t]:
            b = e["box"]
            draw.rounded_rectangle(
                [b["x"] * scale, b["y"] * scale,
                 (b["x"] + b["w"]) * scale, (b["y"] + b["h"]) * scale],
                radius=6 * scale, fill=fill_map.get(e["type"], (239, 239, 239)),
                outline=_BLUE_GRAY, width=max(1, round(1.5 * scale)))
            ty = b["y"] + _BOX_PAD_Y + 14
            for line in e["lines"]:
                dm(b["x"] + b["w"] // 2, ty, line, _BOX_PT, anchor="ms")
                ty += _LINE_H

    # 关系词标注（白底；lpos 为 None 表示放弃该标签）
    for r, pts, lpos, rel in routed:
        if not lpos:
            continue
        tw = _text_width(rel, _EDGE_PT) + 8
        draw.rectangle(
            [(lpos[0] - tw / 2) * scale, (lpos[1] - 12) * scale,
             (lpos[0] + tw / 2) * scale, (lpos[1] + 4) * scale], fill="white")
        dm(lpos[0], lpos[1], rel, _EDGE_PT, anchor="ms", fill=_BLUE_GRAY)

    # 列头（类型名，微软雅黑真粗体 15pt 深藏青 + 类型色下划线小条）
    # 2026-09-07 换掉 SimSun 描边伪加粗：小字号描边发糊，用户反馈看不清
    accent_map = {k: tuple(int(v[i:i + 2], 16) for i in (1, 3, 5))
                  for k, v in TYPE_ACCENT.items()}
    head_font = ImageFont.truetype(_MSYHBD, round(_HEAD_PT * scale))
    for t in cols:
        cx = (col_x[t] + col_w[t] // 2) * scale
        draw.text((cx, col_head_y[t] * scale), t, font=head_font,
                  fill=_HEAD_FILL, anchor="ms")
        twh = head_font.getlength(t)
        draw.rounded_rectangle(
            [cx - twh / 2 - 3 * scale, (col_head_y[t] + 9) * scale,
             cx + twh / 2 + 3 * scale, (col_head_y[t] + 12) * scale],
            radius=1.5 * scale,
            fill=accent_map.get(t, (140, 140, 140)))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
