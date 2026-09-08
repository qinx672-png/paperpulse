# -*- coding: utf-8 -*-
"""
修复后重测两轮（2026-09-02）：
第1轮：旧题库（修复前备份）→ 判分标准不变，和基线数字可比，证明修复有效
第2轮：新题库（要点式答案）→ 最终成绩单

路径全部硬编码在文件里，避开 Git Bash 传中文参数乱码的问题。
用法：cd v2.0 && python eval_rounds.py（后台跑，每轮约 15~25 分钟）
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import evaluate   # 导入评测脚本，复用 run/summarize

OLD_BANK = r"C:\Users\17784\research-ai-assistant\科研文献助手人机交互资料\评测题库-修复前备份-2.0.xlsx"
NEW_BANK = r"C:\Users\17784\research-ai-assistant\科研文献助手人机交互资料\评测题库.xlsx"
OLD_OUT = r"C:\Users\17784\research-ai-assistant\科研文献助手人机交互资料\评测结果.旧题库对比.xlsx"
NEW_OUT = r"C:\Users\17784\research-ai-assistant\科研文献助手人机交互资料\评测结果.xlsx"

model = evaluate.CHAT_MODELS["v4-flash"]   # 被评测模型：2.0 默认 Flash
judge = evaluate.CHAT_MODELS["v4-pro"]     # 判分模型：固定强模型

print("=== 第 1 轮：旧题库（修复前后对比）===")
evaluate.run(model, judge, 0, set(), 3, False,
             testbank=OLD_BANK, result_path="eval_results_oldbank.jsonl")
evaluate.summarize(OLD_OUT, judge, result_path="eval_results_oldbank.jsonl")

print("\n=== 第 2 轮：新题库（最终成绩单）===")
evaluate.run(model, judge, 0, set(), 3, False,
             testbank=NEW_BANK, result_path="eval_results.jsonl")
evaluate.summarize(NEW_OUT, judge, result_path="eval_results.jsonl")

print("\n=== 两轮全部完成 ===")
