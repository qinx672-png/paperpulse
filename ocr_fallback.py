# -*- coding: utf-8 -*-
"""
步骤 8：OCR 兜底（给扫描版 PDF 用）

背景：老论文/扫描件是「图片」，没有文字层，PyMuPDF 抽不出字，前面的解析全白搭。
方案：把每页渲染成图片，调 DeepSeek-OCR（和对话模型走同一个 SiliconFlow 接口）
      把图片读成文字，再交回 structured_parser 用同一套章节识别逻辑扫描。

OCR 结果按论文名缓存（ocr_cache/<论文名>.json），同一篇只识别一次——
注意：OCR 文字只取决于 PDF 文件本身，所以换解析算法「强制重新预处理」
不会重新 OCR；除非 PDF 内容变了，才需要手动删缓存。
"""
import base64
import json
import os
import time

import pymupdf

from config import SF_API_KEY, SF_BASE_URL, OCR_MODEL, OCR_DPI
from console_utils import safe_print
from llm import post_chat

OCR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ocr_cache")

# DeepSeek-OCR 官方模板的「Free OCR」模式：按原排版逐行转写、不做版面重排。
# 注意：社区实测 grounding 模式在密集文档上更容易触发「脑补/重复」，
# Free OCR 更稳；逐行输出正好喂给我们的逐行章节扫描器。模板要原样保留英文。
_OCR_PROMPT = "<image>\nFree OCR."

# 其他视觉模型（PaddleOCR-VL / Qwen-VL）不用 DeepSeek 专用模板，用自然语言指令
_OCR_PROMPT_GENERIC = (
    "请完整识别这张学术论文页面的所有文字，按原排版逐行输出，保留段落和换行，"
    "图表编号和图注也要输出。不要添加任何解释。"
)


def ocr_page(page, model=None) -> str:
    """一页 → 渲染成 PNG 图片 → OCR 模型 → 该页文字。model 不传用配置里的默认。"""
    pix = page.get_pixmap(dpi=OCR_DPI)
    b64 = base64.b64encode(pix.tobytes("png")).decode()
    m = model or OCR_MODEL
    prompt = _OCR_PROMPT if "ocr" in m.lower() else _OCR_PROMPT_GENERIC
    payload = {
        "model": m,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": _OCR_PROMPT},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }],
        # 注意：输入（图片）也占 token，输入+max_tokens 不能超过模型上限 8192，
        # 实测 max_tokens=8192 会报 400。3072 ≈ 1.2 万字符，一页论文足够。
        "max_tokens": 3072,
        # 关键参数：OCR 必须低温。默认高温会让模型「脑补」页面上没有的文字
        # （实测第 2 页整页被编成心脏病论文）。0.1 + 0.95 是社区验证过的组合。
        "temperature": 0.1,
        "top_p": 0.95,
    }
    # 混合方案（2026-09-06）：OCR 走硅基流动（PaddleOCR-VL 免费模型，方舟没有）
    data = post_chat(payload, base_url=SF_BASE_URL, api_key=SF_API_KEY)
    return data["choices"][0]["message"]["content"]


def ocr_pdf_cached(pdf_path, force=False, model=None) -> list:
    """整篇扫描 PDF → 每页文字列表（带缓存，同一篇同一模型只 OCR 一次）。"""
    paper = os.path.basename(pdf_path)
    m = model or OCR_MODEL
    os.makedirs(OCR_DIR, exist_ok=True)
    # 缓存文件名带模型名：换 OCR 模型时各自的缓存互不干扰
    cache = os.path.join(OCR_DIR, f"{paper}.{m.replace('/', '_')}.json")
    if os.path.exists(cache) and not force:
        with open(cache, encoding="utf-8") as f:
            return json.load(f)["pages"]

    doc = pymupdf.open(pdf_path)
    pages = []
    t0 = time.time()
    for i, page in enumerate(doc, 1):
        safe_print(f"  OCR 识别第 {i}/{doc.page_count} 页…")
        pages.append(ocr_page(page, model=m))
    safe_print(f"✅ OCR 完成（{m}）：{len(pages)} 页，共用 {time.time() - t0:.0f} 秒")
    with open(cache, "w", encoding="utf-8") as f:
        json.dump({"paper": paper, "model": m, "pages": pages,
                   "ocr_at": time.strftime("%Y-%m-%d %H:%M")}, f, ensure_ascii=False)
    return pages


if __name__ == "__main__":
    import io
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    if "--render" in sys.argv:
        # 把 PDF 每页渲染成 PNG，用于人工检查「喂给 OCR 的图片」到底长什么样
        # 用法：python ocr_fallback.py --render "<PDF>" [页数]
        src = sys.argv[sys.argv.index("--render") + 1]
        n = int(sys.argv[-1]) if sys.argv[-1].isdigit() else 2
        doc = pymupdf.open(src)
        for i, page in enumerate(doc[:n], 1):
            dst = os.path.splitext(src)[0] + f"-render-p{i}.jpg"
            page.get_pixmap(dpi=100).save(dst)
            safe_print(f"已保存：{dst}")
        raise SystemExit

    if "--make-scanned" in sys.argv:
        # 造一个「扫描版」测试 PDF：把原 PDF 前几页渲染成图片重新打包（没有文字层），
        # 用来验证 OCR 兜底是否真的能触发。
        # 用法：python ocr_fallback.py --make-scanned "<原PDF>" [页数]
        src = sys.argv[sys.argv.index("--make-scanned") + 1]
        n = int(sys.argv[-1]) if sys.argv[-1].isdigit() else 3
        doc = pymupdf.open(src)
        out = pymupdf.open()
        for page in doc[:n]:
            pix = page.get_pixmap(dpi=150)
            # 图片像素 → PDF 页面尺寸（点）：px * 72 / dpi
            new_page = out.new_page(width=pix.width * 72 / 150, height=pix.height * 72 / 150)
            new_page.insert_image(new_page.rect, pixmap=pix)
        dst = os.path.splitext(src)[0] + f"-扫描版测试{n}页.pdf"
        out.save(dst)
        out.close()
        safe_print(f"已生成扫描版测试文件：{dst}")
        raise SystemExit

    # 用法：python ocr_fallback.py "<扫描版PDF>" [--force] [--model 模型ID]
    args = [a for a in sys.argv[1:] if a != "--force"]
    model = None
    if "--model" in args:
        model = args[args.index("--model") + 1]
        args = [a for a in args if a not in ("--model", model)]
    pages = ocr_pdf_cached(args[0], force="--force" in sys.argv, model=model)
    for i, t in enumerate(pages, 1):
        safe_print(f"\n===== 第 {i} 页（前 300 字）=====")
        safe_print(t[:300])
