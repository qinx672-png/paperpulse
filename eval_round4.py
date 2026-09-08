# -*- coding: utf-8 -*-
"""
第四轮评测（2026-09-02，修图表型第三轮暴露的三处根因后）：
①caption 跨块吸收（双栏 PDF 图注右栏续行不再丢）
②表格数据行吸收（「浓度是多少」靠 caption 清单就能答）
③图表题带正文检索兜底（「哪种元素通量最高」靠正文讨论句答）
新题库重跑 37 题，结果存 eval_results4.jsonl，与第三轮（eval_results3.jsonl）对比。
用法：cd v2.0 && python eval_round4.py（后台跑，约 15~25 分钟）
"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import evaluate

NEW_BANK = r"C:\Users\17784\research-ai-assistant\科研文献助手人机交互资料\评测题库.xlsx"
NEW_OUT = r"C:\Users\17784\research-ai-assistant\科研文献助手人机交互资料\评测结果.第四轮.xlsx"

model = evaluate.CHAT_MODELS["v4-flash"]   # 被评测模型：2.0 默认 Flash
judge = evaluate.CHAT_MODELS["v4-pro"]     # 判分模型：固定强模型

print("=== 第四轮：新题库（图表型三处根因修复后）===")
evaluate.run(model, judge, 0, set(), 3, False,
             testbank=NEW_BANK, result_path="eval_results4.jsonl")
evaluate.summarize(NEW_OUT, judge, result_path="eval_results4.jsonl")
print("\n=== 第四轮完成 ===")
