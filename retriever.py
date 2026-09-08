# -*- coding: utf-8 -*-
"""
步骤 5：混合检索（向量 + BM25）+ rerank 精排

为什么两种都要（PRD 2.0）：
- 向量检索管「语义相近」：中文问题也能找到英文论文里意思相近的段落
- BM25 管「关键词精确」：像 "Mo"、"YRE-S2"、"diffusion flux" 这种精确术语，
  光靠向量容易漏（1.0 评测里细节题的丢分点）

融合方式：RRF（Reciprocal Rank Fusion）——
不用纠结两种分数怎么换算，只看「名次」：两边都靠前的块排名最高。

2026-09-06 新增 rerank 精排（用户拍板固定为管线一环）：
RRF 只是「综合两个评委的投票」，排序精度到头后第 3 名以后全是噪音
（用户实测反馈：溯源里真正有用的只有前两条）。
rerank 是「再请一个专业评委逐篇细读打分」：问题与每个候选块拼在一起
完整读一遍输出相关分，把 RRF 漏掉的第 9~12 名好块捞进前排。
"""
import re
from collections import defaultdict

import jieba
from rank_bm25 import BM25Okapi

from config import TOP_K, BM25_K, RERANK_ENABLED, RERANK_FETCH_K
from llm import chat, rerank
from vector_store import _collection, _batch_embed

RRF_K = 60   # RRF 常数：越大越接近「纯名次相加」

# BM25 索引缓存：{paper: (ids, BM25Okapi 实例)}，避免每次提问都重建
_bm25_cache = {}

# 中文问题 → 英文关键词缓存（同一问题重复问不用反复调 LLM）
_kw_cache = {}

_CN_RE = re.compile(r"[一-鿿]")


def _tokenize(text):
    """分词：英文按小写字母/数字串（术语如 YRE-S2 拆成 yre / s2），
    中文交给 jieba（只留两字以上的词，单字太碎没有检索意义）。"""
    words = re.findall(r"[a-z0-9]+", text.lower())
    for seg in re.findall(r"[一-鿿]+", text):
        words.extend(w for w in jieba.cut(seg) if len(w.strip()) > 1)
    return words


def _get_bm25(paper):
    """构建（或取缓存）某篇论文的 BM25 索引。"""
    if paper not in _bm25_cache:
        data = _collection.get(where={"paper": paper}, include=["documents"])
        ids = data["ids"]
        _bm25_cache[paper] = (ids, BM25Okapi([_tokenize(d) for d in data["documents"]]))
    return _bm25_cache[paper]


def clear_bm25_cache():
    """重建向量库后调用，防止缓存和库里的块对不上。"""
    _bm25_cache.clear()


def _vector_hits(query, paper, top_k):
    """向量召回：返回 [{id, text, section, page, score}]。"""
    qvec = _batch_embed([query])[0]
    res = _collection.query(query_embeddings=[qvec], n_results=top_k,
                            include=["documents", "metadatas", "distances"],
                            where={"paper": paper} if paper else None)
    hits = []
    for cid, text, meta, dist in zip(res["ids"][0], res["documents"][0],
                                     res["metadatas"][0], res["distances"][0]):
        hits.append({"id": cid, "text": text, "section": meta["section"],
                     "page": meta["page"], "score": round(1 - dist, 3)})
    return hits


def _bm25_hits(query, paper, top_k):
    """BM25 召回：返回 [{id, text, section, page, score}]。
    查询词一个都没命中时返回空（避免拿一堆 0 分块凑数）。"""
    ids, bm25 = _get_bm25(paper)
    scores = bm25.get_scores(_tokenize(query))
    if max(scores, default=0) <= 0:
        return []
    ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
    data = _collection.get(ids=[ids[i] for i in ranked], include=["documents", "metadatas"])
    info_by_id = {cid: (text, meta) for cid, text, meta in
                  zip(data["ids"], data["documents"], data["metadatas"])}
    hits = []
    for i in ranked:
        text, meta = info_by_id[ids[i]]
        hits.append({"id": ids[i], "text": text, "section": meta["section"],
                     "page": meta["page"], "score": round(float(scores[i]), 3)})
    return hits


def _extract_en_keywords(question):
    """中文问题 → 英文检索词（LLM 轻量提取，带缓存）。
    论文是英文的，中文词在英文块里一个都匹配不到，必须架一座「翻译桥」：
    把问题里能检索的概念翻译成英文术语，再交给 BM25。"""
    if question in _kw_cache:
        return _kw_cache[question]
    kws = []
    try:
        out = chat([
            {"role": "system", "content": (
                "你是检索词提取器。把用户的中文问题翻译/提取成适合在英文论文里检索的"
                "英文关键词。要求：只输出关键词，用逗号分隔，最多 6 个，不要任何解释；"
                "优先保留问题里已有的英文术语（如 Mo、Eh、RSMs）。")},
            {"role": "user", "content": question},
        ], temperature=0, max_tokens=60).strip()
        kws = [w.strip().lower() for w in re.split(r"[,\n，、]", out) if w.strip()]
    except Exception:
        kws = []
    _kw_cache[question] = kws
    return kws


def chinese_keyword_search(question, paper, top_k=BM25_K):
    """中文关键词检索兜底：BM25 分词器只认英文数字，中文问题直接跑会全 0 分。
    两条腿一起上：
    ① LLM 把中文问题提取成英文检索词（库里是英文论文时靠它命中）
    ② jieba 切的中文词直接保留（库里将来有中文论文时可命中）"""
    query_words = _extract_en_keywords(question) + _tokenize(question)
    if not query_words:
        return []
    return _bm25_hits(" ".join(query_words), paper, top_k)


def hybrid_search(query, paper=None, top_k=TOP_K, bm25_k=BM25_K, use_rerank=None):
    """向量 + BM25 双路召回，RRF 融合，可选 rerank 精排（2026-09-06 新增固定环节）。
    BM25 一路全 0 分且问题是中文时，自动走中文关键词兜底（LLM 英译桥 + jieba）。
    use_rerank 默认读配置（管线固定开）；评测对照实验 A 组传 False 走旧基线路径
    （向量只召回 top_k，与评测 1~4 轮完全一致）。
    rerank 路径：向量多捞到 RERANK_FETCH_K 块 → RRF 合并约 12~16 候选 →
    rerank 逐对打分 → 取前 top_k。多捞不改旧路径的排名（RRF 只按名次算分，
    多召回不影响已召回块的名次），所以 A/B 两组唯一差异 = 有没有精排环节。
    返回 [{id, text, section, page, rrf, via}]，via 标记该块来自哪一路（调试用）；
    rerank 后附加 rscore（精排分数，溯源/评测调试用）。"""
    if use_rerank is None:
        use_rerank = RERANK_ENABLED
    fetch_k = RERANK_FETCH_K if use_rerank else top_k
    vh = _vector_hits(query, paper, fetch_k)
    bh = _bm25_hits(query, paper, bm25_k)
    if not bh and _CN_RE.search(query):
        bh = chinese_keyword_search(query, paper, bm25_k)

    rrf = defaultdict(float)
    for rank, h in enumerate(vh):
        rrf[h["id"]] += 1.0 / (RRF_K + rank + 1)
    for rank, h in enumerate(bh):
        rrf[h["id"]] += 1.0 / (RRF_K + rank + 1)

    vids = {h["id"] for h in vh}
    bids = {h["id"] for h in bh}
    by_id = {h["id"]: h for h in vh + bh}
    ranked = sorted(rrf.items(), key=lambda kv: kv[1], reverse=True)
    ranked = ranked[: (fetch_k if use_rerank else top_k)]

    hits = []
    for cid, score in ranked:
        h = by_id[cid]
        via = "both" if cid in vids and cid in bids else ("vector" if cid in vids else "bm25")
        hits.append({**h, "rrf": round(score, 4), "via": via})

    if use_rerank and len(hits) > 1:
        # 精排：rerank 把「问题 + 每个候选块」逐对打分，按分数重排取前 top_k。
        # 分数写进 rscore，rrf 保留融合名次不变（评测可对比两者排序差异）。
        try:
            pairs = rerank(query, [h["text"] for h in hits])[:top_k]
            hits = [{**hits[i], "rscore": round(s, 4)} for i, s in pairs]
        except Exception:
            pass   # 精排失败安全降级：保持 RRF 顺序继续答，不让精排成为问答的致命依赖
    return hits


if __name__ == "__main__":
    import io
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    PAPER = "YRW-潮汐影响氧化还原敏感元素的释放.pdf"
    # 三条验收问题：英文术语题 / 中文语义题 / 中英混合题
    for q in ["Mo anomaly in YRE-S2",
              "潮汐淹没对金属扩散通量的影响是什么？",
              "研究区潮差是多少"]:
        print(f"问题：{q}")
        for h in hybrid_search(q, paper=PAPER, top_k=3):
            print(f"  [{h['rrf']}|{h['via']:6}] 第{h['page']}页 {h['text'][:60]}…")
        print()
