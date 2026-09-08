# -*- coding: utf-8 -*-
"""
重生成 6 篇评测论文的预答案（2026-09-02，修复 caption 提取后）。
caption 提取逻辑改了（图注多句吸收），旧缓存里的 captions 还是残缺版，
必须 --force 重跑。路径硬编码，避免 Git Bash 传中文参数乱码。
"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from preanswer import generate_preanswers

PDFS = [
    r"C:\Users\17784\research-ai-assistant\科研文献助手人机交互资料\题库论文PDF\P1_Huang2017_NatComm_土壤湿度碳矿化.pdf",
    r"C:\Users\17784\research-ai-assistant\科研文献助手人机交互资料\题库论文PDF\P2_MAPAN_红树林叶片同位素.pdf",
    r"C:\Users\17784\research-ai-assistant\科研文献助手人机交互资料\题库论文PDF\P3_NatComm_红树林SOC全球.pdf",
    r"C:\Users\17784\research-ai-assistant\科研文献助手人机交互资料\题库论文PDF\P4_MEE2014_SPOM酸处理.pdf",
    r"C:\Users\17784\Desktop\YRW和组会\文献精读笔记\YRW-潮汐影响氧化还原敏感元素的释放.pdf",
    r"C:\Users\17784\Desktop\YRW和组会\文献精读笔记\The hidden influence of terrestrial groundwater on salt marsh function and resilience.pdf",
]

for p in PDFS:
    print(f"=== 重生成：{p} ===", flush=True)
    try:
        generate_preanswers(p, force=True)
    except Exception as e:
        print(f"失败：{e}", flush=True)
print("=== 全部完成 ===", flush=True)
