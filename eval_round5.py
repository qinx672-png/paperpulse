# -*- coding: utf-8 -*-
"""
第五轮评测（2026-09-06 更新）：rerank 对照实验 A/B 两组 + 五档模型。

2026-09-06 用户拍板：rerank（Qwen3-Reranker-4B 精排）固定为管线一环。
为量化精排环节的贡献，第五轮跑 A/B 对照实验：
  A 组 use_rerank=False —— 旧基线（与评测 1~4 轮完全一致：向量召回 8 + BM25 4，RRF 取 8）
  B 组 use_rerank=True  —— 新管线（向量多捞 12 + BM25 4，RRF 合并后 rerank 精排取 8）
两组唯一差异 = 有没有精排环节（多捞不影响旧路径排名，RRF 只按名次算分）。
A/B 在默认模型 v4-flash 上全量对比；其余四档模型只跑 B 组（新管线日常形态）。

结果：A 组存 eval_results5A.jsonl、B 组存 eval_results5B.jsonl，
Excel 分别写「评测结果.第五轮A.xlsx / 第五轮B.xlsx」，对比时按同一批题并排看。
判分用新细则（图内数据不要求答出，定位对图表+页码即算对）。
用法：cd v2.0 && python eval_round5.py（后台跑，约 30~45 分钟）
跑前提醒：CPU/网络密集任务，把电源模式切到「高性能」，结束后切回「平衡」。
"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import evaluate

NEW_BANK = r"C:\Users\17784\research-ai-assistant\科研文献助手人机交互资料\评测题库.xlsx"
OUT_DIR = r"C:\Users\17784\research-ai-assistant\科研文献助手人机交互资料"

model = evaluate.CHAT_MODELS["v4-flash"]   # 被评测模型：2.0 默认 Flash
judge = evaluate.CHAT_MODELS["v4-pro"]     # 判分模型：固定强模型

print("=== 第五轮 A 组：旧基线（无 rerank，与 1~4 轮一致）===")
evaluate.run(model, judge, 0, set(), 3, False,
             testbank=NEW_BANK, result_path="eval_results5A.jsonl",
             use_rerank=False)
evaluate.summarize(OUT_DIR + r"\评测结果.第五轮A.xlsx", judge,
                   result_path="eval_results5A.jsonl")

print("\n=== 第五轮 B 组：新管线（rerank 精排固定开）===")
evaluate.run(model, judge, 0, set(), 3, False,
             testbank=NEW_BANK, result_path="eval_results5B.jsonl",
             use_rerank=True)
evaluate.summarize(OUT_DIR + r"\评测结果.第五轮B.xlsx", judge,
                   result_path="eval_results5B.jsonl")

print("\n=== 第五轮 v4-flash A/B 对照完成 ===")
print("其余三档模型（v4-pro / glm / kimi）只跑 B 组，另行安排或手动执行。")
