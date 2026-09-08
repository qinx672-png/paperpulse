# -*- coding: utf-8 -*-
"""一次性补跑脚本（用完可删）：串行（workers=1）补 A/B 组剩余缺口。

背景：第 32 行（A 组）和第 8/14/19 行（B 组）连续两次因 ConnectionResetError(10054)
失败，其余全部完成。怀疑并发请求触发服务端断连，先用串行试一次——
run() 自带断点续跑，只会补缺的行，其余自动跳过。
"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import evaluate

NEW_BANK = r"C:\Users\17784\research-ai-assistant\科研文献助手人机交互资料\评测题库.xlsx"
OUT_DIR = r"C:\Users\17784\research-ai-assistant\科研文献助手人机交互资料"

model = evaluate.CHAT_MODELS["v4-flash"]
judge = evaluate.CHAT_MODELS["v4-pro"]

print("=== 串行补跑 A 组（缺第 32 行）===")
evaluate.run(model, judge, 0, set(), 1, False,
             testbank=NEW_BANK, result_path="eval_results5A.jsonl",
             use_rerank=False)
evaluate.summarize(OUT_DIR + r"\评测结果.第五轮A.xlsx", judge,
                   result_path="eval_results5A.jsonl")

print("\n=== 串行补跑 B 组（缺第 8/14/19 行）===")
evaluate.run(model, judge, 0, set(), 1, False,
             testbank=NEW_BANK, result_path="eval_results5B.jsonl",
             use_rerank=True)
evaluate.summarize(OUT_DIR + r"\评测结果.第五轮B.xlsx", judge,
                   result_path="eval_results5B.jsonl")

print("\n=== 串行补跑结束 ===")
