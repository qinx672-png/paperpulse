# -*- coding: utf-8 -*-
"""临时自查脚本：把 roadmaps/ 里所有 v2 缓存渲染成 PNG，人工检查布局是否重叠。"""
import glob
import json
import os

from roadmap import roadmap_png

os.chdir(os.path.dirname(os.path.abspath(__file__)))
for cache in glob.glob(os.path.join("roadmaps", "*.v2.json")):
    with open(cache, encoding="utf-8") as f:
        r = json.load(f)
    stem = os.path.basename(cache).replace(".v2.json", "")
    out = "_check_new_" + stem + ".png"
    with open(out, "wb") as f:
        f.write(roadmap_png(r))
    print("OK", out, len(r.get("samples", [])), "样品,", len(r.get("analyses", [])), "分析项")
