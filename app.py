# -*- coding: utf-8 -*-
"""
步骤 7：Streamlit 界面 + 精读笔记导出

启动：streamlit run app.py --server.port 8502
流程：上传 PDF → 自动预处理（解析/向量化/8 字段）→ 提问 → 一键导出精读笔记
"""
import io
import os
import re

import streamlit as st

from config import CHAT_MODELS
from preanswer import generate_preanswers
from qa import answer
from retriever import clear_bm25_cache
from roadmap import generate_roadmap, roadmap_png, roadmap_svg, TYPE_ORDER, TYPE_COLORS
from structured_parser import parse_pdf_smart
from vector_store import build_index

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploaded")

# 分流类型的界面标签（Material 图标；Word 导出时 _plain_route 会清洗掉图标语法）
ROUTE_LABEL = {"归纳": ":material/compass: 归纳题 · 走预答案",
               "细节": ":material/search: 细节题 · 混合检索",
               "图表": ":material/image: 图表题 · 图注兜底"}

st.set_page_config(page_title="PaperPulse", page_icon=":material/menu_book:", layout="wide")

# ---------- 全局排版（2026-09-06 用户反馈：整体字太小、标题层级差异不明显）----------
# 正文 16px→17px；h1/h3 明显拉开（大而重 vs 常规）；tab 标签加大加粗；caption 弱化。
st.markdown("""
<style>
html { font-size: 17px; }
h1 { font-size: 2.15rem !important; font-weight: 800 !important; }
h3 { font-size: 1.5rem !important; font-weight: 700 !important; }
[data-testid="stMarkdownContainer"] p { font-size: 1rem; }
[data-testid="stCaptionContainer"] p { font-size: .92rem !important; }
button[role="tab"] p { font-size: 1.05rem !important; font-weight: 700 !important; }
[data-testid="stSidebar"] { font-size: .95rem; }
</style>
""", unsafe_allow_html=True)


# 图谱渲染版本号：knowledge_graph 的浮窗内容/交互 JS 改动后 +1，
# 强制旧缓存失效（Streamlit 缓存按函数源码+参数记键，改的是它调用的模块时源码没变，
# 不加版本号会把旧浮窗内容一直端给用户）。
_GRAPH_VIEW_VER = "2026-09-07-a"


@st.cache_data(show_spinner="正在渲染文献图谱…")
def _graph_view(fp, generated_at, view_ver):
    """知识图谱 tab 视图：只读关系缓存并渲染，绝不触发抽取——重抽只发生在点
    「更新图谱」按钮时。2026-09-06 修复：原来这里调 build_graph()，库指纹变化时
    打开 tab 就会自动重抽（十几分钟），页面一直转圈、没法提问。
    缓存键 = 库指纹 + 图谱生成时间 + 渲染版本号：点完「更新图谱」生成时间变
    → 自动重渲染新图；渲染层改动 → 手动升版本号强制重渲染。"""
    from knowledge_graph import load_graph, graph_html
    g = load_graph()
    return g, graph_html(g)

# ---------- 顶部标题区：左标题右版本徽章 ----------
hero_left, hero_right = st.columns([5, 1], vertical_alignment="center")
with hero_left:
    st.title("PaperPulse")
    st.caption("Your Research Trace Weaver · 上传论文 → 自动建索引 + 生成 8 字段精读要点 → 直接提问")
with hero_right:
    st.badge("2.0", icon=":material/new_releases:", color="green")

# ---------- 侧边栏：模型选择 + 上传论文 ----------
with st.sidebar:
    st.markdown("### :material/menu_book: PaperPulse")
    st.caption("论文上传、解析与问答设置")
    st.space("small")
    st.markdown("**:material/tune: 模型设置**")
    model_key = st.selectbox("对话模型（评测对比用）", list(CHAT_MODELS.keys()),
                             format_func=lambda k: CHAT_MODELS[k])
    model = CHAT_MODELS[model_key]
    st.space("small")
    st.markdown("**:material/upload_file: 上传论文**")
    uploaded = st.file_uploader("上传论文 PDF", type="pdf")
    # one-shot 强刷按钮：解析算法更新后点一次，强制重建这篇论文的缓存。
    # 不用 checkbox——checkbox 每次刷新都保持勾选，会导致每次都重跑。
    # 旁边灰色 ? 图标：点击展开「预处理是什么」的通俗解释（st.popover 浮窗）。
    with st.container(horizontal=True):
        force_clicked = st.button("重新预处理", icon=":material/refresh:")
        with st.popover(":material/help:"):
            st.markdown("**什么是预处理？**")
            st.markdown("把这篇论文在向量库里**重新解析、切块并建立索引**，"
                        "同时重新生成 8 字段精读要点和研究路线图。")
            st.caption("解析 / 建库算法更新后点一次即可，平时不用管。")
    # 2026-09-06 用户反馈界面拥挤 → 去掉论文标题手填输入框，直接用解析标题；
    # 同日用户拍板：只留 v2.0，去掉「上海交大·求职作品集」字样
    st.caption("v2.0")

if uploaded is None:
    st.info("在左侧上传一篇论文 PDF 开始", icon=":material/arrow_back:")
    st.stop()

# ---------- 预处理（都有本地缓存，同一篇论文第二次上传会秒过） ----------
paper = uploaded.name
os.makedirs(UPLOAD_DIR, exist_ok=True)
path = os.path.join(UPLOAD_DIR, paper)
with open(path, "wb") as f:
    f.write(uploaded.getbuffer())

if st.session_state.get("paper") != paper or force_clicked:   # 换论文或点了强刷 → 重跑预处理
    st.session_state.chat_history = []
    st.session_state.paper = paper
    with st.status("预处理中…", expanded=True) as status:
        status.update(label="① 章节解析 + 段落切块（扫描版会自动 OCR）…")
        parse_pdf_smart(path)
        status.update(label="② 向量化入库（首次约 1 分钟，扫描版较慢，之后秒过）…")
        build_index(path, force=force_clicked)
        if force_clicked:
            clear_bm25_cache()   # 检索器里的 BM25 索引缓存一起作废，强制重算
        status.update(label="③ 生成 8 字段预答案（约 1~2 分钟，之后秒过）…")
        st.session_state.profile = generate_preanswers(path, force=force_clicked)
        # ④ 路线图保持同步直接生成（2026-09-06 用户拍板：改为直接生成，不搞后台线程；
        # 真正要隔离的是「文献图谱」——图谱已改为按钮手动更新，不挡提问）
        status.update(label="④ 生成研究路线图（约 1 分钟，之后秒过）…")
        st.session_state.roadmap = generate_roadmap(path, force=force_clicked)
        status.update(label="✅ 预处理完成，可以提问了", state="complete")

profile = st.session_state.profile
roadmap = st.session_state.get("roadmap")

# 兼容旧会话：2026-09-07 前是五层答辩版路线图（question/samples），新版是
# 实体-关系图（entities/relations）。浏览器还开着旧会话时刷新页面，
# session_state 里还是旧结构，直接渲染会报错——检测到旧结构就自动重生成一次
# （读 .v3 缓存，秒过；无缓存才现场抽）。
if roadmap is not None and "entities" not in roadmap:
    roadmap = generate_roadmap(path)
    st.session_state.roadmap = roadmap

# 档案页顶部标题：直接用解析出的标题（2026-09-06 去掉手填输入框后只剩这一路）。
# 2026-09-07 用户拍板：研究路线图图上不显示论文标题，display_title 只服务档案页。
display_title = (profile.get("title") or "").strip()

# ---------- 导出精读笔记（函数提前定义，供档案页按钮调用）----------
def _plain_route(route):
    """Word 导出用：去掉界面标签里的 :material/xxx: 图标语法（Word 里会显示成乱码文本）。"""
    import re
    return re.sub(r":material/[^:]+:\s*", "", ROUTE_LABEL[route])


def _xml_clean(text):
    """清洗 XML/Word 不兼容的控制字符（PDF 解析偶带 \x00、\x0b 等，写 Word 会报
    ValueError: All strings must be XML compatible）。保留 \t \n \r 正常空白。"""
    import re
    return re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", text or "")


def _note_bytes():
    """把 8 字段 + 图表清单 + 问答记录拼成 Word 笔记，返回文件字节。"""
    from docx import Document
    from docx.shared import Pt, RGBColor
    doc = Document()
    doc.add_heading(_xml_clean(profile["title"]), 0)
    doc.add_heading("一、8 字段精读要点", level=1)
    for f in profile["fields"]:
        doc.add_heading(_xml_clean(f["field"]), level=2)
        doc.add_paragraph(_xml_clean(f["text"]))
        src = doc.add_paragraph()
        run = src.add_run(f"出处：{_xml_clean(f['source'])}")
        run.font.size = Pt(9)
        run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)
    doc.add_heading("二、图表清单", level=1)
    for c in profile["captions"]:
        doc.add_paragraph(_xml_clean(f"{c['id']}（第{c['page']}页）：{c['text']}"))
    doc.add_heading("三、问答记录", level=1)
    for m in st.session_state.chat_history:
        doc.add_heading(_xml_clean(f"Q：{m['q']}"), level=2)
        doc.add_paragraph(f"（{_plain_route(m['route'])}）")
        doc.add_paragraph(_xml_clean(m["answer"]))
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------- 主区域：论文档案 / 研究路线图 两个 tab（2026-09-06 美化第 2 步）----------
# 8 字段卡片图标：字段名固定（FIELD_PLAN 定义），按名字配 Material 图标。
FIELD_ICONS = {
    "研究意义": ":material/flag:",
    "领域问题": ":material/forum:",
    "拟解决问题": ":material/question_mark:",
    "研究内容": ":material/science:",
    "主要结果": ":material/query_stats:",
    "结论": ":material/check_circle:",
    "创新点": ":material/lightbulb:",
    "不足与展望": ":material/trending_up:",
}
tab_archive, tab_roadmap, tab_graph = st.tabs([
    ":material/article: 论文档案", ":material/route: 研究路线图", ":material/hub: 我的文献图谱"])

with tab_archive:
    # 档案页顶部：论文标题 + 一句说明
    st.markdown(f"### {display_title or '（未解析出标题，可在左侧边栏填写）'}")
    st.caption("8 字段精读要点 · 每张卡片附原文出处")
    # 字段网格：每行 2 个、共 4 行。可折叠式（2026-09-06 用户要求）：
    # 默认全部收起，用户点哪个字段名就展开哪个，避免 8 段文字同时堆在页面上。
    for i in range(0, len(profile["fields"]), 2):
        row = st.columns(2, gap="medium")
        for col, f in zip(row, profile["fields"][i:i + 2]):
            with col:
                with st.expander(f["field"], icon=FIELD_ICONS.get(f["field"])):
                    st.caption(f["source"])
                    st.write(f["text"])
    # 图表清单：字段卡片下面，折叠起来不抢视线
    with st.expander("图表清单（图注兜底的依据）", icon=":material/image:"):
        for c in profile["captions"]:
            st.markdown(f"- **{c['id']}**（第{c['page']}页）：{c['text']}")
    # 导出精读笔记：8 字段 + 图表清单 + 问答记录拼成一份 Word（答辩材料直接用）。
    # 2026-09-06 用户反馈「导出对用户很便捷」→ 按钮升主色（type="primary"）+ 浅蓝底说明条，
    # 让整块导出区在档案页上跳出来。
    st.markdown(
        '<div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:10px;'
        'padding:10px 14px;margin:6px 0 12px 0">'
        '<span style="color:#1d4ed8;font-weight:700">一键导出精读笔记</span>　'
        '<span style="color:#475569;font-size:0.92em">8 字段 + 图表清单 + 问答记录 → 一份 Word，答辩材料直接用</span>'
        '</div>', unsafe_allow_html=True)
    st.download_button("导出精读笔记（Word）", icon=":material/description:",
                       data=_note_bytes(), type="primary",
                       file_name=f"精读笔记-{paper}.docx",
                       mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

with tab_roadmap:
    # 研究路线图 3.0（2026-09-07 用户拍板重做）：单篇文献实体-关系流程图。
    # 思维沿用最初方案书 v1（实体五类 + 关系五种），输出普通流程图（框 + 线 + 关系词）。
    # 防编造：每条实体/关系带原文证据 + 页码，折叠栏里逐条可人工核对。
    st.markdown("**研究路线图**（点图看原大图）")
    roadmap_view = roadmap   # 图上不显示论文标题（2026-09-07 用户拍板）
    # 图例：只显示图中实际出现的类型（学术稳重配色，与图内框色一致）
    ents = roadmap_view.get("entities") or []
    used = [t for t in TYPE_ORDER if any(e.get("type") == t for e in ents)]
    if used:
        st.markdown("　".join(
            f'<span style="display:inline-block;width:14px;height:14px;'
            f'border-radius:3px;background:{TYPE_COLORS[t]};'
            f'vertical-align:-2px;margin-right:5px"></span>{t}'
            for t in used), unsafe_allow_html=True)
    st.image(roadmap_png(roadmap_view))
    # 逐条证据折叠栏（编造率 = 0 硬线的人工核对入口）
    rels = roadmap_view.get("relations") or []
    by_id = {e.get("id"): e for e in ents}
    def _flat(x):
        """证据句可能带换行/连续空白，进 markdown 前压平。"""
        return re.sub(r"\s+", " ", str(x or "—")).strip()
    with st.expander(f"逐条证据（{len(rels)} 条关系 / {len(ents)} 个要素，每条都带原文出处）"):
        if rels:
            st.markdown("**关系（连线）**")
            for r in rels:
                s = _flat(by_id.get(r.get("source"), {}).get("label") or r.get("source"))
                t = _flat(by_id.get(r.get("target"), {}).get("label") or r.get("target"))
                st.markdown(f"- **{s}** —{_flat(r.get('relation'))}→ **{t}**"
                            f"｜第 {r.get('page')} 页：{_flat(r.get('evidence'))}")
        if ents:
            st.markdown("**要素（节点）**")
            for e in ents:
                st.markdown(f"- **{_flat(e.get('label'))}**（{_flat(e.get('type'))}）"
                            f"｜第 {e.get('page')} 页：{_flat(e.get('evidence'))}")
    # 下载文件名去掉 .pdf 后缀，避免出现「xxx.pdf.png」这种怪名字
    stem = paper[:-4] if paper.lower().endswith(".pdf") else paper
    d1, d2 = st.columns(2)
    d1.download_button("下载 PNG", icon=":material/download:",
                       data=roadmap_png(roadmap_view),
                       file_name=f"研究路线图-{stem}.png",
                       mime="image/png", width="stretch")
    d2.download_button("下载 SVG（矢量）", icon=":material/download:",
                       data=roadmap_svg(roadmap_view).encode("utf-8"),
                       file_name=f"研究路线图-{stem}.svg",
                       mime="image/svg+xml", width="stretch")

with tab_graph:
    # 我的文献图谱（2026-09-06 知识图谱 MVP 第 2 天）：库级视角，不依赖当前上传哪篇
    from knowledge_graph import (build_graph, load_graph, EDGE_COLORS, EDGE_LEGEND,
                                 _library, _library_fingerprint)
    st.markdown("**我的文献图谱**")
    st.caption("精读库全景：节点 = 论文，连线 = 关系")
    # 图例：大色块两行三列（2026-09-06 用户反馈小圆点颜色分不清，改成 16px 色块）
    legend_cols = st.columns(3)
    for i, t in enumerate(EDGE_COLORS):
        with legend_cols[i % 3]:
            st.markdown(
                f'<span style="display:inline-block;width:16px;height:16px;'
                f'border-radius:4px;background:{EDGE_COLORS[t]};'
                f'vertical-align:-3px;margin-right:6px"></span>'
                f'<b>{t}</b> <span style="color:#64748b">{EDGE_LEGEND[t]}</span>',
                unsafe_allow_html=True)
    st.caption("操作：单击节点 → 聚焦放大 · 双击空白处 → 回到全景 · 拖动移动画布 · 滚轮缩放")
    try:
        lib = _library()
        if len(lib) < 2:
            st.info("精读库里的论文还不足 2 篇，继续上传论文后图谱会自动出现。", icon=":material/hub:")
        else:
            # 2026-09-06 体验解耦（第二次修复）：打开 tab 只读缓存、旧图秒开；
            # 库指纹不一致时提示点「更新图谱」。只有按钮才触发抽取，绝不挡提问。
            up1, up2 = st.columns([1, 5])
            if up1.button("更新图谱", icon=":material/refresh:",
                          help="库里新传了论文后点一次，增量补上新论文的关系（约 1~2 分钟）"):
                with st.spinner("正在更新图谱（并行抽取，约 1~2 分钟）…"):
                    build_graph()   # 库指纹变化 → 自动增量；无变化秒回
                st.rerun()
            fp = _library_fingerprint(lib)
            g = load_graph()   # 只读缓存文件，绝不触发抽取
            if not g.get("papers"):
                st.info("还没有图谱缓存，点「更新图谱」首次建图。", icon=":material/hub:")
            elif g.get("library_fingerprint") != fp:
                st.info("库里有新论文 / 重新预处理过的论文，图谱显示的是更新前的结果。"
                        "点「更新图谱」补上新关系（约 1~2 分钟），期间可以正常提问。",
                        icon=":material/sync:")
            _, html = _graph_view(fp, g.get("generated_at", ""), _GRAPH_VIEW_VER)
            st.components.v1.html(html, height=680, scrolling=False)
            st.caption(f"共 {len(g['papers'])} 篇论文 · {len(g['edges'])} 条关系"
                       f" · 生成于 {g.get('generated_at', '')} · 悬停节点/连线看详情")
    except Exception as e:
        st.warning(f"图谱暂时不可用（{e}），告诉我报错内容我来修。")

# ---------- 聊天区（2026-09-06 美化第 3 步：去分隔线、定制头像、统一图标）----------
st.space("small")
if not st.session_state.chat_history:
    st.caption("还没有问答记录，在下方输入框向论文提问吧。")
for m in st.session_state.chat_history:
    with st.chat_message("user"):
        st.write(m["q"])
    with st.chat_message("assistant", avatar=":material/menu_book:"):
        st.caption(ROUTE_LABEL[m["route"]])
        st.write(m["answer"])
        if m["hits"]:
            with st.expander("检索到的原文片段（溯源）", icon=":material/search:"):
                # 溯源只展示最相关 3 条（2026-09-06 用户验收：全量展示太冗余）。
                # 答案生成仍用完整召回池保证质量，这里只是展示层截断。
                for h in m["hits"][:3]:
                    st.markdown(f"**第{h['page']}页 · {h['section']}**")
                    st.write(h["text"])
                if len(m["hits"]) > 3:
                    st.caption(f"共 {len(m['hits'])} 条相关片段，已按相关度排序，仅展示最相关 3 条")

q = st.chat_input("向论文提问…")
if q:
    with st.chat_message("user"):
        st.write(q)
    with st.chat_message("assistant", avatar=":material/menu_book:"):
        with st.spinner("思考中…"):
            r = answer(q, path, model=model)
        st.caption(ROUTE_LABEL[r["route"]])
        st.write(r["answer"])
        if r["hits"]:
            with st.expander("检索到的原文片段（溯源）", icon=":material/search:"):
                # 同历史消息展示：只显示最相关 3 条，其余提示数量
                for h in r["hits"][:3]:
                    st.markdown(f"**第{h['page']}页 · {h['section']}**")
                    st.write(h["text"])
                if len(r["hits"]) > 3:
                    st.caption(f"共 {len(r['hits'])} 条相关片段，已按相关度排序，仅展示最相关 3 条")
    st.session_state.chat_history.append(
        {"q": q, "route": r["route"], "answer": r["answer"], "hits": r["hits"]})
