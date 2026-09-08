# -*- coding: utf-8 -*-
"""临时余额检查（跑第五轮前验货，用完可删）。

第五轮会用到两家供应商：
  - 火山方舟：chat 作答（v4-flash）+ 判分（v4-pro）
  - 硅基流动：embedding（bge-m3）+ rerank（Qwen3-Reranker-4B）
任何一家欠费都会让 40 分钟的白跑。每项发一个最小的真实请求验证。
"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config
import llm


def try_ark(model_key, label):
    """方舟 chat 最小请求（1 个 token 级问题）。"""
    try:
        r = llm.chat([{"role": "user", "content": "只回复：OK"}],
                     model=config.CHAT_MODELS[model_key], max_tokens=16)
        print(f"方舟 chat {label}   ✅  返回：{r[:24]!r}")
    except Exception as e:
        print(f"方舟 chat {label}   ❌  {str(e)[:400]}")


def try_sf_rerank():
    """硅基 rerank 最小请求（2 个短文档）。"""
    try:
        r = llm.rerank("测试", ["候选文档甲", "候选文档乙"])
        print("硅基 rerank   ✅ ", r)
    except Exception as e:
        print("硅基 rerank   ❌ ", str(e)[:400])


def try_sf_embed():
    """硅基 embedding 最小请求（bge-m3，检索环节必需）。"""
    import requests
    try:
        r = requests.post(
            config.SF_BASE_URL + "/embeddings",
            headers={"Authorization": f"Bearer {config.SF_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model": config.EMBEDDING_MODEL, "input": "测试"},
            timeout=60)
        ok = r.status_code == 200
        print(f"硅基 embedding   {'✅' if ok else '⚠️'}   状态码 {r.status_code}  {r.text[:200] if not ok else '正常'}")
    except Exception as e:
        print("硅基 embedding   ❌ ", str(e)[:400])


if __name__ == "__main__":
    print("== 第五轮跑前验货 ==")
    try_ark("v4-flash", "v4-flash（作答）")
    try_ark("v4-pro", "v4-pro（判分）")
    try_ark("glm", "glm（补充档·方舟）")
    try_ark("kimi", "kimi（补充档·硅基）")
    try_sf_rerank()
    try_sf_embed()
