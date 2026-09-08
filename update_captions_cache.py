# -*- coding: utf-8 -*-
"""
只更新 6 篇评测论文缓存里的 captions（2026-09-02，Table 2 借行修复后）。
8 字段是 LLM 生成的、与 caption 无关，不用重跑；只重新解析 PDF 换 captions。
用法：cd v2.0 && python update_captions_cache.py
"""
import json
import os
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from structured_parser import parse_pdf

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preanswers")

PDFS = [
    r"C:\Users\17784\research-ai-assistant\科研文献助手人机交互资料\题库论文PDF\P1_Huang2017_NatComm_土壤湿度碳矿化.pdf",
    r"C:\Users\17784\research-ai-assistant\科研文献助手人机交互资料\题库论文PDF\P2_MAPAN_红树林叶片同位素.pdf",
    r"C:\Users\17784\research-ai-assistant\科研文献助手人机交互资料\题库论文PDF\P3_NatComm_红树林SOC全球.pdf",
    r"C:\Users\17784\research-ai-assistant\科研文献助手人机交互资料\题库论文PDF\P4_MEE2014_SPOM酸处理.pdf",
    r"C:\Users\17784\Desktop\YRW和组会\文献精读笔记\YRW-潮汐影响氧化还原敏感元素的释放.pdf",
    r"C:\Users\17784\Desktop\YRW和组会\文献精读笔记\The hidden influence of terrestrial groundwater on salt marsh function and resilience.pdf",
]

for pdf in PDFS:
    paper = os.path.basename(pdf)
    cache = os.path.join(CACHE_DIR, paper + ".json")
    try:
        parsed = parse_pdf(pdf)
        with open(cache, "r", encoding="utf-8") as f:
            profile = json.load(f)
        old = len(profile.get("captions", []))
        profile["captions"] = parsed.captions
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
        print(f"{paper}: captions {old} → {len(parsed.captions)}", flush=True)
    except Exception as e:
        print(f"{paper} 失败：{e}", flush=True)
print("=== captions 缓存更新完成 ===", flush=True)
