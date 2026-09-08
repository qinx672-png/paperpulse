# -*- coding: utf-8 -*-
"""自查：模拟用户打开 8502 上传秦肖论文，抓取页面实际显示的路线图，判断新旧。"""
import time

import requests
from playwright.sync_api import sync_playwright

PDF = r"C:\Users\17784\Desktop\YRW和组会\毕业论文\1.秦肖_论文-0528.pdf"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1500, "height": 1200})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    # 服务器可能刚重启，连不上就等 1 秒重试，最多等 60 秒
    for _ in range(60):
        try:
            page.goto("http://localhost:8502", wait_until="networkidle")
            break
        except Exception:
            time.sleep(1)
    else:
        raise SystemExit("服务器 60 秒内没起来")
    # 上传论文（隐藏的 file input 也能直接塞文件）
    page.set_input_files('input[type="file"]', PDF)
    # 等预处理跑完（全部有缓存，应该很快）
    page.wait_for_selector('text=可以提问了', timeout=360000)
    page.wait_for_timeout(3000)
    # 旧「在线编辑」按钮还在不在（判断页面代码新旧）
    has_edit = page.get_by_text("在线编辑").count()
    # 找右栏的路线图大图（Streamlit 把 PNG 挂在 /media/ 下）
    src = page.evaluate("""() => {
        const imgs = [...document.querySelectorAll('img')];
        const el = imgs.find(i => i.src.includes('/media/') && i.naturalWidth > 500);
        return el ? el.src : 'not found';
    }""")
    print("旧编辑按钮数量:", has_edit)
    print("路线图地址:", src)
    if src.startswith("http"):
        r = requests.get(src, timeout=60)
        with open("_live_roadmap.png", "wb") as f:
            f.write(r.content)
        print("已保存 _live_roadmap.png, 状态", r.status_code, "大小", len(r.content))
    page.screenshot(path="_live_full.png", full_page=True)
    print("JS 报错:", errors or "无")
    browser.close()
