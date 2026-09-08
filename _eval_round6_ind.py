# -*- coding: utf-8 -*-
"""第六轮专项复测（一次性脚本）：归纳型题全量重跑，验证「归纳题改走检索」修复。

2026-09-08 背景：第五轮失分分析定位归纳题 0/20/50 分题根因=直接读 8 字段预答案
（用户问题可能不在 8 字段范围内 + 个别论文字段质量差）→ qa.py 归纳题改走
混合检索 + 摘要兜底，本脚本重跑题库全部归纳型题对比。
配置与生产管线一致：v4-flash 无 rerank、判分 v4-pro、串行 workers=1
（并发 3 触发 ConnectionResetError，第五轮实测教训）。
结果独立文件 eval_results6_ind.jsonl，不覆盖第五轮。
"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd

import evaluate

TESTBANK = r"C:\Users\17784\research-ai-assistant\科研文献助手人机交互资料\评测题库.xlsx"
OUT_DIR = r"C:\Users\17784\research-ai-assistant\科研文献助手人机交互资料"

# 从题库筛出所有归纳型题的行号（不手写死，题库改版也不会漏）
df = pd.read_excel(TESTBANK)
rows = {idx + 2 for idx, row in df.iterrows() if str(row["问题类型"]).strip() == "归纳型"}
print(f"归纳型题共 {len(rows)} 题，行号：{sorted(rows)}")

model = evaluate.CHAT_MODELS["v4-flash"]
judge = evaluate.CHAT_MODELS["v4-pro"]
print(f"被评测：{model}（生产管线，无 rerank）| 判分：{judge} | workers=1 串行")

evaluate.run(model, judge, 0, rows, 1, False,
             testbank=TESTBANK, result_path="eval_results6_ind.jsonl",
             use_rerank=False)
evaluate.summarize(OUT_DIR + r"\评测结果.第六轮-归纳题.xlsx", judge,
                   result_path="eval_results6_ind.jsonl")

print("\n=== 第六轮归纳题专项复测结束 ===")
