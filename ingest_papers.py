# -*- coding: utf-8 -*-
"""
步骤 2：4 篇库外论文入库脚本（解析 → 向量化 → 8 字段预答案）。
每篇走一遍 build_index + generate_preanswers，与之前 YRW/Nature Water 入库流程一致。
"""
import io
import os
import sys
import traceback

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from vector_store import build_index
from preanswer import generate_preanswers

PDF_DIR = r"C:\Users\17784\research-ai-assistant\科研文献助手人机交互资料\题库论文PDF"
PAPERS = [
    "P1_Huang2017_NatComm_土壤湿度碳矿化.pdf",
    "P2_MAPAN_红树林叶片同位素.pdf",
    "P3_NatComm_红树林SOC全球.pdf",
]

for name in PAPERS:
    pdf = os.path.join(PDF_DIR, name)
    print(f"\n{'=' * 60}\n入库：{name}\n{'=' * 60}", flush=True)
    try:
        parsed = build_index(pdf)
        print(f"解析完成：{len(parsed.chunks)} 块、{len(parsed.sections)} 章节、{len(parsed.captions)} 图表", flush=True)
        generate_preanswers(pdf)
    except Exception:
        print(f"❌ {name} 失败：", flush=True)
        traceback.print_exc()

print("\n✅ 3 篇论文入库流程全部结束", flush=True)
