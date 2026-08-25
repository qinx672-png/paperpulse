# -*- coding: utf-8 -*-
"""
核心 RAG 逻辑：解析 PDF → 切块 → 向量化 → 检索 → 生成

RAG = 检索增强生成（Retrieval-Augmented Generation）
思路：把"知识"（论文）提前切块、转成向量存好；
提问时先检索出最相关的几段，再让大模型"照着这些内容回答"，
从而做到有据可查、不乱编。
"""

import re
import numpy as np
import requests

from config import API_KEY, BASE_URL, EMBEDDING_MODEL, CHAT_MODEL

# 没填 Key 时直接给友好提示，而不是等运行到一半才报错
if not API_KEY.strip() or "在这里填" in API_KEY:
    raise SystemExit("❌ 请先打开 config.py，把 API_KEY 填成你自己的 Key")


# ---------- 底层 API 调用 ----------
def _post(endpoint, payload):
    """统一走 OpenAI 兼容接口（embedding 和 chat 用同一套格式）。"""
    headers = {"Authorization": f"Bearer {API_KEY}",
               "Content-Type": "application/json"}
    r = requests.post(BASE_URL + endpoint, headers=headers, json=payload, timeout=120)
    r.raise_for_status()          # 出错会抛异常，方便定位
    return r.json()


def get_embedding(text):
    """把一段文字转成向量。向量能表示语义，相似内容向量方向接近。"""
    data = _post("/embeddings", {"model": EMBEDDING_MODEL, "input": text})
    return np.array(data["data"][0]["embedding"], dtype=np.float32)


def chat(messages, temperature=0.3):
    """调用大模型对话。temperature 越低越稳定、越不容易乱编。"""
    data = _post("/chat/completions", {
        "model": CHAT_MODEL,
        "messages": messages,
        "temperature": temperature,
    })
    return data["choices"][0]["message"]["content"]


# ---------- 文档处理 ----------
def parse_pdf(file_obj):
    """解析 PDF。file_obj 可以是文件路径(str)或二进制流(io.BytesIO)。返回全文文本。"""
    from pypdf import PdfReader
    reader = PdfReader(file_obj)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def chunk_text(text, chunk_size=500, overlap=80):
    """
    把长文本切成小块。
    原因：大模型一次能读的内容有限，且小块检索更精准；
    overlap 让相邻块重叠一部分，避免一句话被从中间切断。
    """
    text = re.sub(r"\s+", " ", text).strip()
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start:start + chunk_size])
        if start + chunk_size >= len(text):
            break
        start = start + chunk_size - overlap
    return [c for c in chunks if len(c.strip()) > 20]


# ---------- 索引与检索 ----------
def _cosine(a, b):
    """余弦相似度：衡量两个向量方向是否接近（-1~1，越接近 1 越相关）。"""
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))


class DocIndex:
    """文档索引：把每篇论文切成块、算成向量存起来，供检索。"""

    def __init__(self):
        self.chunks = []     # 每块的文本
        self.sources = []    # 每块来自哪个文件
        self.vectors = []    # 每块对应的向量

    def add_document(self, filename, text):
        """解析后的全文 → 切块 → 每块向量化 → 存入索引。"""
        for chunk in chunk_text(text):
            self.chunks.append(chunk)
            self.sources.append(filename)
            self.vectors.append(get_embedding(chunk))

    def search(self, query, top_k=3):
        """把问题也转成向量，找出语义最接近的 top_k 个块。"""
        q = get_embedding(query)
        scores = [_cosine(q, v) for v in self.vectors]
        idx = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
        return [(self.sources[i], self.chunks[i], scores[i]) for i in idx]


# ---------- RAG 主流程 ----------
def answer_with_rag(query, index, top_k=3):
    """检索 → 拼接上下文 → 让大模型基于上下文回答。返回 (回答, 命中片段)。"""
    hits = index.search(query, top_k)
    context = "\n\n".join(f"[来源：{s}]\n{c}" for s, c, _ in hits)

    system = ("你是一个严谨的科研文献助手。只能根据提供的文献内容回答；"
              "如果文献里没有相关信息，就直说'文献中没有相关内容'，绝不能编造。")
    prompt = f"文献内容：\n{context[:4000]}\n\n用户问题：{query}"

    answer = chat([
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ])
    return answer, hits
