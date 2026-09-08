# -*- coding: utf-8 -*-
"""
步骤 3：向量化入库（Chroma 持久化 + 批量 embedding）

流程：步骤 2 解析好的 chunks → 每 16 块一批调 bge-m3 → 存进本地 Chroma 库。

好处（对比 1.0）：
- 1.0 每次启动都要重新向量化（11 页论文要等约 8 分钟）；2.0 只向量化一次，
  之后启动直接读本地库，秒级可用。
- 每块带 metadata（论文名/章节/页码），图表定位题就靠页码。
"""
import os
import time

import chromadb
import requests

from console_utils import safe_print

from config import SF_API_KEY, SF_BASE_URL, EMBEDDING_MODEL, EMBED_BATCH_SIZE, TOP_K
from structured_parser import parse_pdf, parse_pdf_smart

# ---------- Chroma 本地库 ----------
_DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chroma_db")
_client = chromadb.PersistentClient(path=_DB_DIR)
# 建库时指定余弦距离：bge-m3 的语义比较用的是余弦相似度（和 1.0 保持一致）
_collection = _client.get_or_create_collection("papers", metadata={"hnsw:space": "cosine"})


# ---------- 批量向量化 ----------
def _batch_embed(texts):
    """一批文本 → 一批向量。OpenAI 兼容接口，input 直接传数组，一次请求算一批。
    2026-09-06 混合方案：embedding 固定走硅基流动（bge-m3 评测基线，不换供应商）。"""
    payload = {"model": EMBEDDING_MODEL, "input": texts}
    r = requests.post(SF_BASE_URL + "/embeddings",
                      headers={"Authorization": f"Bearer {SF_API_KEY}",
                               "Content-Type": "application/json"},
                      json=payload, timeout=300)
    r.raise_for_status()
    data = r.json()["data"]
    data.sort(key=lambda d: d.get("index", 0))   # 按 index 排回原顺序，防止返回乱序
    return [d["embedding"] for d in data]


# ---------- 建索引 ----------
def build_index(pdf_path, force=False):
    """
    解析 PDF → 分批向量化 → 存入 Chroma。
    同一篇论文已经入库就直接跳过（force=True 强制重建）。
    返回解析结果（sections/captions 给后面步骤用）。
    """
    paper = os.path.basename(pdf_path)
    already = _collection.get(where={"paper": paper}, limit=1)["ids"]
    if already and not force:
        safe_print(f"{paper} 已在向量库里，跳过向量化（想重建用 force=True）")
        return parse_pdf(pdf_path)
    if force:
        # 强制重建：先把这篇的旧块删干净（新解析的块数可能不一样，不删会残留旧块）
        _collection.delete(where={"paper": paper})

    parsed = parse_pdf_smart(pdf_path)   # 扫描版 PDF 会自动走 OCR 兜底
    texts = [c["text"] for c in parsed.chunks]
    if not texts:
        safe_print("错误：连 OCR 兜底都没提取出正文，请确认 PDF 不是损坏或加密文件")
        raise SystemExit

    # 分批向量化（1.0 实测：逐块串行 8 分钟；批量后应该快一个数量级）
    safe_print(f"开始向量化：{parsed.title[:60]}… 共 {len(texts)} 块，每批 {EMBED_BATCH_SIZE} 块")
    t0 = time.time()
    vecs = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[start:start + EMBED_BATCH_SIZE]
        vecs.extend(_batch_embed(batch))
        done = min(start + EMBED_BATCH_SIZE, len(texts))
        safe_print(f"  进度 {done}/{len(texts)} 块，已用 {time.time() - t0:.0f} 秒")

    # 写入 Chroma（带 metadata：论文名/标题/章节/页码）
    ids = [f"{paper}_{i}" for i in range(len(texts))]
    metadatas = [{"paper": paper, "title": parsed.title,
                  "section": c["section"], "page": c["page"]} for c in parsed.chunks]
    for start in range(0, len(ids), 200):      # 分小批写入，防止单条请求过大
        _collection.add(ids=ids[start:start + 200],
                        documents=texts[start:start + 200],
                        metadatas=metadatas[start:start + 200],
                        embeddings=vecs[start:start + 200])
    safe_print(f"✅ 入库完成：{len(ids)} 块，总用时 {time.time() - t0:.0f} 秒")
    return parsed


# ---------- 检索（步骤 5 会在此基础上加 BM25 混合检索） ----------
def search(query, top_k=TOP_K, paper=None):
    """向量检索：返回 top_k 块（文本 + 章节 + 页码 + 相似度）。"""
    qvec = _batch_embed([query])[0]
    where = {"paper": paper} if paper else None
    res = _collection.query(query_embeddings=[qvec], n_results=top_k,
                            include=["documents", "metadatas", "distances"], where=where)
    hits = []
    for text, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
        hits.append({"text": text, "section": meta["section"],
                     "page": meta["page"], "score": round(1 - dist, 3)})  # 余弦距离 → 相似度
    return hits


def count_chunks(paper=None):
    """库里有多少块（调试/验收用）。"""
    where = {"paper": paper} if paper else None
    return _collection.count() if not where else len(_collection.get(where=where)["ids"])


if __name__ == "__main__":
    import io
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    if "--delete-paper" in sys.argv:
        # 从向量库删除一篇论文的全部块（清理测试数据/下架论文用）
        # 用法：python vector_store.py --delete-paper "<论文文件名>"
        name = sys.argv[sys.argv.index("--delete-paper") + 1]
        _collection.delete(where={"paper": name})
        safe_print(f"已从向量库删除 {name}，库里还剩 {count_chunks()} 块")
        raise SystemExit

    # 命令行用法：python vector_store.py "<PDF 路径>" --force
    args = [a for a in sys.argv[1:] if a != "--force"]
    PDF = args[0] if args else r"C:\Users\17784\Desktop\YRW和组会\文献精读笔记\YRW-潮汐影响氧化还原敏感元素的释放.pdf"
    parsed = build_index(PDF, force="--force" in sys.argv)
    safe_print(f"\n向量库共 {count_chunks()} 块\n")

    # 两条验收问题：一条归纳向、一条细节向
    for q in ["What were the main findings about Fe and Mn enrichment in the salt marsh?",
              "How was the diffusion flux calculated?"]:
        safe_print(f"问题：{q}")
        for h in search(q, top_k=3):
            # 存库的 text 本身已带「章节: 正文」前缀，这里只补页码
            print(f"  [{h['score']}] 第{h['page']}页 {h['text'][:70]}…")
        safe_print()
