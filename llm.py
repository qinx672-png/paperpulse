# -*- coding: utf-8 -*-
"""
共用的模型调用入口（步骤 4 预答案生成、步骤 5 精排、步骤 6 问答层都用它）。

支持指定模型（model 参数）——后面做 V3 / V4-Flash / V4-Pro 对比评测时，
同一批问题换着模型跑，只改一个参数。
2026-09-06 新增 rerank()：RAG 管线精排环节（用户拍板固定为一环），走硅基流动。
"""
import time

import requests

from config import API_KEY, BASE_URL, SF_API_KEY, SF_BASE_URL, CHAT_MODEL, RERANK_MODEL

# 网络偶发中断（SSL 断开/超时）时的重试参数
_RETRIES = 3
_RETRY_WAIT = 3   # 秒，逐次翻倍


def post_chat(payload, base_url=None, api_key=None, endpoint="/chat/completions"):
    """把请求体发给指定接口，返回完整 JSON。网络抽风自动重试（最多 3 次）。
    单独抽出来是为了给 OCR 兜底复用——DeepSeek-OCR 也是「对话式」接口，
    只是消息里带的是图片而不是纯文字。
    2026-09-06 混合方案：默认走方舟（对话），OCR 传参走硅基流动。
    2026-09-06 加 endpoint 参数：rerank 走硅基的 /rerank 接口（不是对话接口）。"""
    base_url = base_url or BASE_URL
    api_key = api_key or API_KEY
    for attempt in range(_RETRIES):
        try:
            r = requests.post(base_url + endpoint,
                              headers={"Authorization": f"Bearer {api_key}",
                                       "Content-Type": "application/json"},
                              json=payload, timeout=300)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            if attempt == _RETRIES - 1:
                # 把接口返回的报错内容一起带出来（400/404 通常能直接看出原因）
                if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
                    raise RuntimeError(
                        f"API 报错 {e.response.status_code}：{e.response.text[:800]}") from e
                raise
            time.sleep(_RETRY_WAIT * (attempt + 1))   # 3 秒、6 秒后重试
    raise RuntimeError("unreachable")


def rerank(query, documents, top_n=None):
    """Rerank 精排：把「问题 + 每个候选块」逐对打分，返回按分数降序的 [(index, score)]。
    2026-09-06 用户拍板固定为管线一环，模型 Qwen3-Reranker-4B，固定走硅基流动
    （方舟 rerank 未开通；供应商路由规则只看模型名含 /，这里显式指定）。
    调用失败抛异常，由调用方兜底降级（retriever 会保持 RRF 顺序继续答）。"""
    payload = {"model": RERANK_MODEL, "query": query, "documents": documents}
    if top_n:
        payload["top_n"] = top_n
    data = post_chat(payload, base_url=SF_BASE_URL, api_key=SF_API_KEY, endpoint="/rerank")
    results = data.get("results", [])
    # 接口约定按分数降序返回，这里再排一次兜底，然后统一为 (index, score) 对
    return sorted(((r["index"], r.get("relevance_score", 0.0)) for r in results),
                  key=lambda p: p[1], reverse=True)


def chat(messages, model=None, temperature=0.3, max_tokens=None):
    """调用大模型对话。temperature 越低越稳定、越不容易乱编。
    2026-09-06 用户拍板（B 方案·默认形态）：不干预思考模式——各家模型以出厂
    默认形态跑（DeepSeek 默认思考就思考），评测测的是「用户真实体验」。
    因此调用方给的 max_tokens 要留足思考+正文的余量（调用处已统一调大）。"""
    m = model or CHAT_MODEL
    payload = {
        "model": m,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    # 供应商路由：硅基的模型名带厂商前缀「厂商/模型」（如 moonshotai/Kimi-K2.6），
    # 方舟是纯模型名（如 deepseek-v4-flash-ga-260731）。按这个规则分流。
    if "/" in m:
        base_url, api_key = SF_BASE_URL, SF_API_KEY
    else:
        base_url, api_key = BASE_URL, API_KEY
    return post_chat(payload, base_url=base_url, api_key=api_key)["choices"][0]["message"]["content"]
