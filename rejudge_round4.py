# -*- coding: utf-8 -*-
"""
第四轮作答重判（2026-09-02）：判分细则更新为「图表题不要求答出图内数据，
定位对图/表+给出图表说明就算对」（产品定位：助手不读图）。
作答不重跑，只把 eval_results4.jsonl 的 37 题用新细则重判，存 eval_results5.jsonl。
用法：cd v2.0 && python rejudge_round4.py（后台跑，约 8~12 分钟）
"""
import json
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import evaluate
from evaluate import judge, CHAT_MODELS

judge_model = CHAT_MODELS["v4-pro"]

recs = []
with open("eval_results4.jsonl", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                pass
print(f"读入 {len(recs)} 条作答记录，开始用新判分细则重判……", flush=True)

out = []
for i, r in enumerate(recs, 1):
    v = judge(r["question"], r["ref_points"], r["ref"], r["answer"], r["qtype"], judge_model)
    r["verdict"] = v or {"score": None, "reason": "判分解析失败"}
    out.append(r)
    print(f"  {i}/{len(recs)} 第{r['row']}行 {r['qtype']} → {r['verdict'].get('score')}", flush=True)

with open("eval_results5.jsonl", "w", encoding="utf-8") as f:
    for r in out:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

evaluate.summarize(
    r"C:\Users\17784\research-ai-assistant\科研文献助手人机交互资料\评测结果.第五轮.xlsx",
    judge_model, result_path="eval_results5.jsonl")
print("\n=== 重判完成 ===", flush=True)
