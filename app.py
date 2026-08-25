# -*- coding: utf-8 -*-
"""
Streamlit 界面：上传论文 PDF → 对话提问
运行：streamlit run app.py
"""

import io
import streamlit as st

from rag import DocIndex, parse_pdf, answer_with_rag

st.set_page_config(page_title="科研文献 AI 助手", page_icon="📚")
st.title("📚 科研文献 AI 助手")
st.caption("上传论文 PDF，即可对文献内容提问（基于 RAG 检索增强生成）")

# 索引放在 session_state 里，刷新页面不会丢失
if "index" not in st.session_state:
    st.session_state.index = DocIndex()
    st.session_state.loaded = []   # 已载入的文件名，避免重复

index = st.session_state.index

# ----- 侧边栏：上传文献 -----
with st.sidebar:
    st.header("① 上传论文 PDF")
    files = st.file_uploader("可多选，选择后点下方按钮", type="pdf",
                             accept_multiple_files=True)
    if files and st.button("解析并建立索引", type="primary"):
        for f in files:
            if f.name in st.session_state.loaded:
                continue
            with st.spinner(f"正在解析 {f.name} ..."):
                try:
                    text = parse_pdf(io.BytesIO(f.getvalue()))
                except Exception as e:
                    st.error(f"{f.name} 解析失败：{e}")
                    continue
            if not text.strip():
                st.warning(f"{f.name} 提取不到文字（可能是扫描版图片 PDF）")
                continue
            with st.spinner(f"正在向量化 {f.name} ..."):
                index.add_document(f.name, text)
            st.session_state.loaded.append(f.name)
            st.success(f"已载入：{f.name}")

    st.divider()
    st.caption(f"已载入 {len(st.session_state.loaded)} 篇文献")

# ----- 主区：对话 -----
if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("sources"):
            st.caption("📖 参考来源：" + "、".join(dict.fromkeys(msg["sources"])))

if q := st.chat_input("问一个关于文献的问题，例如：这篇论文的核心结论是什么？"):
    st.session_state.messages.append({"role": "user", "content": q})
    with st.chat_message("user"):
        st.markdown(q)

    with st.chat_message("assistant"):
        if not st.session_state.loaded:
            answer, sources = "请先在左侧上传论文 PDF 并建立索引。", []
        else:
            with st.spinner("检索相关段落并生成回答..."):
                answer, hits = answer_with_rag(q, index)
            sources = [s for s, _, _ in hits]
        st.markdown(answer)
        if sources:
            st.caption("📖 参考来源：" + "、".join(dict.fromkeys(sources)))

    st.session_state.messages.append(
        {"role": "assistant", "content": answer, "sources": sources})
