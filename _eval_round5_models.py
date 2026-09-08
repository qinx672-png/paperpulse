# -*- coding: utf-8 -*-
"""一次性脚本（用完可删）：三档模型跑「生产管线（无 rerank）」横向对比。

2026-09-07 背景：第五轮 v4-flash A/B 对照完成，用户拍板关掉 rerank。
因此其他模型只需跑无 rerank 路径（= 现在 2.0 的真实生产管线），
回答「同一产品在不同模型上表现如何」。结果各自独立文件，不覆盖 5A/5B。
串行 workers=1（并发 3 会触发 ConnectionResetError，第五轮实测教训）。
kimi 走硅基流动（模型名含 / 自动路由），v4-pro / glm 走方舟。
"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import evaluate

NEW_BANK = r"C:\Users\17784\research-ai-assistant\科研文献助手人机交互资料\评测题库.xlsx"
OUT_DIR = r"C:\Users\17784\research-ai-assistant\科研文献助手人机交互资料"

judge = evaluate.CHAT_MODELS["v4-pro"]   # 判分模型固定强模型

for key, suffix in [("v4-pro", "pro"), ("glm", "glm"), ("kimi", "kimi")]:
    model = evaluate.CHAT_MODELS[key]
    print(f"\n=== 第五轮补充：{key}（{model}）· 生产管线（无 rerank）===")
    evaluate.run(model, judge, 0, set(), 1, False,
                 testbank=NEW_BANK, result_path=f"eval_results5_{suffix}.jsonl",
                 use_rerank=False)
    evaluate.summarize(OUT_DIR + rf"\评测结果.第五轮-{suffix}.xlsx", judge,
                       result_path=f"eval_results5_{suffix}.jsonl")

print("\n=== 三档模型补充评测结束 ===")
