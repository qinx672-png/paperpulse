# -*- coding: utf-8 -*-
"""
研究路线图 draw.io 导出（2026-09-02 新增，配合「答辩级路线图」需求）

为什么做这一版：
- 旧版 roadmap.py 手写 SVG/PNG，排版死板、想微调只能改代码；答辩场景常要改字改位置
- draw.io 是学术圈画流程图的默认工具（导师/期刊编辑都认），导出 .drawio 源文件后
  可以自由拖拽编辑；同时用 draw.io CLI 无头导出 PNG/SVG，网页里照样能直接看图
- 技术路线：内容层不变（LLM 五层 JSON + 方法原文检索防编造），渲染层换成
  「代码模板确定性生成 draw.io XML」——固定五层结构，代码只负责填内容，
  不靠 LLM 写 XML（参考 GitHub 上 drawio-skill 的做法：LLM 写 XML 容易烂，
  代码模板才稳定可靠）

样式（基金申请/博士答辩 PPT 风格）：
- 白底、学术深蓝 #1F4E79 边框、层名黑体深蓝、正文宋体
- 层名在框内顶部居中（沿用旧版布局习惯），正交连线 + 实心箭头
- 字号沿用旧版 pt 体系，draw.io 里换算成 px：px = pt × 4/3（12pt = 16px）
- 布局坐标直接复用 roadmap._layout：SVG/PNG/draw.io 三种格式同一套排版，
  三张图长得一样，只是 draw.io 版可编辑

用法：
    from drawio_gen import export_drawio
    paths = export_drawio(roadmap)   # → {"drawio": ..., "png": ..., "svg": ...}
"""
import html
import os
import subprocess

# 2026-09-07：路线图 3.0 改为实体-关系流程图后，旧五层 draw.io 导出器随之作废。
# _layout 已从 roadmap.py 移除，这里不再导入；roadmap_drawio_xml 保留函数壳并给出明确报错。
from roadmap import ROADMAP_DIR, _short

# draw.io 便携版位置：tools/ 目录下第一层子目录里的 draw.io.exe
TOOLS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")

_DRAWIO_EXE = None   # 找到一次就记住，不用每次重新扫目录


def find_drawio():
    """找 tools 目录下的 draw.io.exe（便携版）。找不到返回 None。"""
    global _DRAWIO_EXE
    if _DRAWIO_EXE:
        return _DRAWIO_EXE
    if not os.path.isdir(TOOLS_DIR):
        return None
    direct = os.path.join(TOOLS_DIR, "draw.io.exe")   # 解压后直接躺在 tools 下
    if os.path.exists(direct):
        _DRAWIO_EXE = direct
        return direct
    for name in sorted(os.listdir(TOOLS_DIR), reverse=True):   # 版本新者优先
        for exe in (os.path.join(TOOLS_DIR, name, "draw.io.exe"),
                    os.path.join(TOOLS_DIR, name, "drawio", "draw.io.exe")):
            if os.path.exists(exe):
                _DRAWIO_EXE = exe
                return exe
    return None


# ---------- 样式常量（低饱和学术风，配色取自用户参考模板「可编辑Sci技术路线.pptx」） ----------
# 模板主色：白底 + 浅青绿 #DCE9E5 / 米色 #F3EEDF 填充 + 蓝灰 #44546A 细框（2026-09-02 换色）
_C_DEEP = "#44546A"     # 蓝灰：层框边框、层名文字（模板主题深色，比旧深蓝柔和）
_C_ENTRY = "#EFF3F1"    # 分析条目小框底色（微青灰，呼应模板浅青绿）
_C_ENTRY_STROKE = "#A8B0B8"
_C_EDGE = "#44546A"     # 连线（与边框同色系）
_FONT_BODY = "SimSun"   # 正文：宋体
_FONT_HEAD = "SimHei"   # 层名/标题：黑体

# 旧版 pt 字号 → draw.io px（12pt = 16px，按 4/3 换算）
# 2026-09-02 随 roadmap.py 上调一档：内容已精简为短语，框变小，字号放大才平衡
_P_TITLE = 21    # 16pt 论文标题
_P_LAYER = 19    # 14pt 层名
_P_BODY = 17     # 12.5pt 正文（科学问题/讨论）
_P_OBJ = 16      # 12pt 研究目的条目
_P_NAME = 16     # 12pt 样品名
_P_MT = 16       # 12pt 分析条目「样品 · 方法」行
_P_PA = 15       # 11.5pt 参数行
_P_PU = 15       # 11pt 分析目的行
_P_SMALL = 15    # 11pt 采集方式

_NEXT_ID = 0   # 自增 id，保证一次导出的所有 cell id 不重复


def _next_id():
    global _NEXT_ID
    _NEXT_ID += 1
    return f"rm{_NEXT_ID}"


def _rect(x, y, w, h, fill, stroke, stroke_w=1.5):
    """一个纯色矩形框（无文字）。返回 (cell id, xml)。"""
    cid = _next_id()
    style = (f"rounded=0;whiteSpace=wrap;html=1;fillColor={fill};"
             f"strokeColor={stroke};strokeWidth={stroke_w};fontFamily={_FONT_BODY};")
    xml = (f'<mxCell id="{cid}" value="" style="{style}" vertex="1" parent="1">'
           f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/>'
           f'</mxCell>')
    return cid, xml


def _text(x, y, w, h, value_html, align="center", valign="top",
          font=_FONT_BODY, size=_P_BODY, color="#000000"):
    """一个文字 cell。value_html 里可用 <b>/<br>/<span style> 等 HTML 标签，
    这里统一做 XML 转义（draw.io 存文件时就是这么转义的）。返回 xml。"""
    style = (f"text;html=1;align={align};verticalAlign={valign};"
             f"fontFamily={font};fontSize={size};fontColor={color};")
    value = html.escape(value_html, quote=True)
    return (f'<mxCell id="{_next_id()}" value="{value}" style="{style}" '
            f'vertex="1" parent="1">'
            f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/>'
            f'</mxCell>')


def _edge(src, tgt, straight=False, exit_x=0.5, exit_y=1.0,
          entry_x=0.5, entry_y=0.0):
    """两个框之间的箭头。straight=True 画直线（样品→分析的横箭头），
    否则用 draw.io 正交路由（自动拐直角弯，学术图最常用的画法）。"""
    style = ("edgeStyle=none;" if straight else "edgeStyle=orthogonalEdgeStyle;")
    style += (f"rounded=0;html=1;endArrow=block;endFill=1;"
              f"strokeColor={_C_EDGE};strokeWidth=1.5;"
              f"exitX={exit_x:.3f};exitY={exit_y:.3f};"
              f"entryX={entry_x:.3f};entryY={entry_y:.3f};")
    return (f'<mxCell id="{_next_id()}" style="{style}" edge="1" '
            f'parent="1" source="{src}" target="{tgt}">'
            f'<mxGeometry relative="1" as="geometry"/></mxCell>')


def _box_title(box, title):
    """框内顶部居中的层名（黑体、深蓝、加粗）。"""
    return _text(box["x"], box["y"] + 2, box["w"], 24,
                 f"<b>{title}</b>", font=_FONT_HEAD, size=_P_LAYER, color=_C_DEEP)


def roadmap_drawio_xml(roadmap):
    """旧五层答辩版路线图的 draw.io 导出器。路线图 3.0（2026-09-07 用户拍板）已改为
    实体-关系流程图，此导出随之作废；要可编辑矢量图请下载新版 SVG。"""
    raise RuntimeError("旧五层 draw.io 导出已随路线图 3.0 下线（2026-09-07），请改用 SVG 下载")
    global _NEXT_ID
    _NEXT_ID = 0
    B, W, H = _layout(roadmap)
    cells, edges = [], []

    def box_rect(b):
        """布局里的一个框 → 矩形 cell（边框用学术深蓝）。"""
        cid, xml = _rect(b["x"], b["y"], b["w"], b["h"], b["fill"], _C_DEEP)
        cells.append(xml)
        return cid

    def box_body(b, lines, align="center", size=_P_BODY, x0=12, w0=24):
        """框的内容文字：一个 cell，多行用 <br> 连接，顶对齐。"""
        cells.append(_text(b["x"] + x0, b["y"] + 32, b["w"] - w0, b["h"] - 38,
                           "<br>".join(lines), align=align, size=size))

    # 顶部论文标题（黑体 16pt，居中）
    title = _short(roadmap.get("title", "") or "研究路线图", 46)
    cells.append(_text(0, 2, W, 30, f"<b>{title}</b>",
                       font=_FONT_HEAD, size=_P_TITLE))

    # ① 科学问题（浅灰横条，正文居中）
    q = B["q"]
    qid = box_rect(q)
    cells.append(_box_title(q, "科学问题"))
    box_body(q, q["lines"])

    # ② 研究目的（白框，条目左对齐）
    o = B["obj"]
    oid = box_rect(o)
    cells.append(_box_title(o, "研究目的"))
    box_body(o, o["lines"], align="left", size=_P_OBJ, x0=24, w0=48)

    # ③ 样品获取（左框：样品名加粗 + 采集方式灰色小字）
    lb = B["left"]
    lid = box_rect(lb)
    cells.append(_box_title(lb, "样品获取"))
    parts = []
    for e in lb["entries"]:
        name_html = "<br>".join(e["name"])
        how_html = "<br>".join(e["how"])
        parts.append(f"<b>▪ {name_html}</b><br>"
                     f'<span style="font-size:{_P_SMALL}px;color:#555555">'
                     f"{how_html}</span><br>")
    cells.append(_text(lb["x"] + 14, lb["y"] + 32, lb["w"] - 28, lb["h"] - 38,
                       "".join(parts), align="left", size=_P_NAME))

    # ③ 样品分析（右框：每条一个浅灰小框，方法行粗 / 参数 / 目的灰）
    rb = B["right"]
    rid = box_rect(rb)
    cells.append(_box_title(rb, "样品分析（方法 · 参数 · 目的）"))
    ty = rb["y"] + 38   # 与 _layout 的条目循环起点一致（_BODY_Y=40 再减 2）
    for e in rb["entries"]:
        # 2026-09-02 自适应：条目小框宽用 e["w"]（按文字实测收窄），不再吃满整栏
        _, ex = _rect(rb["x"] + 12, ty - 13, e["w"], e["h"] + 13,
                      _C_ENTRY, _C_ENTRY_STROKE, stroke_w=1)
        cells.append(ex)
        inner = (f"<b>{'<br>'.join(e['mt'])}</b><br>"
                 f'<span style="font-size:{_P_PA}px">{"<br>".join(e["pa"])}</span><br>'
                 f'<span style="font-size:{_P_PU}px;color:#555555">'
                 f'{"<br>".join(e["pu"])}</span>')
        cells.append(_text(rb["x"] + 24, ty - 11, e["w"] - 24, e["h"] + 22,
                           inner, align="left", size=_P_MT))
        ty += e["h"] + 14

    # ④ 数据分析与讨论（浅灰横条，正文居中）
    d = B["disc"]
    did = box_rect(d)
    cells.append(_box_title(d, "数据分析与讨论"))
    box_body(d, d["lines"])

    # ---------- 连线 ----------
    edges.append(_edge(qid, oid))                       # ①→②
    edges.append(_edge(oid, lid))                       # ②→③ 左栏（正交分叉）
    edges.append(_edge(oid, rid))                       # ②→③ 右栏
    # 左→右横箭头（样品 → 分析）。
    # 修复 2026-09-02：原来画在第一个样品条目中部，右框同一高度是条目小框，
    # 箭头会戳进框里；改到标题条中缝（y+14，标题文字居中两侧是空的）。
    if lb["entries"]:
        hy = lb["y"] + 14
        edges.append(_edge(lid, rid, straight=True,
                           exit_x=1.0, exit_y=(hy - lb["y"]) / lb["h"],
                           entry_x=0.0, entry_y=(hy - rb["y"]) / rb["h"]))
    # ③→④ 汇合（两栏各出一条，略错开进讨论框顶）
    edges.append(_edge(lid, did, entry_x=0.45))
    edges.append(_edge(rid, did, entry_x=0.55))

    return "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<mxfile host="app.diagrams.net" agent="research-ai-assistant" version="24.7.7">',
        '  <diagram name="研究路线图" id="roadmap-1">',
        f'    <mxGraphModel dx="0" dy="0" grid="1" gridSize="10" guides="1" '
        f'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
        f'pageWidth="{W}" pageHeight="{H}" math="0" shadow="0" background="#FFFFFF">',
        '      <root>',
        '        <mxCell id="0"/>',
        '        <mxCell id="1" parent="0"/>',
    ] + ["        " + c for c in cells]
      + ["        " + e for e in edges]
      + [
        '      </root>',
        '    </mxGraphModel>',
        '  </diagram>',
        '</mxfile>',
    ])


def export_drawio(roadmap, out_dir=None, exe=None):
    """生成 .drawio 源文件，并尝试用 draw.io CLI 导出 PNG（2 倍清晰度）和 SVG。
    返回 {"drawio": 路径, "png": 路径或 None, "svg": 路径或 None}。
    draw.io 没装/导出失败时只有 .drawio 文件，调用方应降级显示旧版 PNG。"""
    out_dir = out_dir or ROADMAP_DIR
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, roadmap["paper"])
    src = base + ".drawio"
    with open(src, "w", encoding="utf-8") as f:
        f.write(roadmap_drawio_xml(roadmap))
    result = {"drawio": src, "png": None, "svg": None}

    exe = exe or find_drawio()
    if not exe:
        print("⚠️ 没找到 draw.io.exe（tools 目录），只生成了 .drawio 源文件")
        return result
    # draw.io CLI：-x 无头导出；-f 格式；-s 缩放（PNG 2 倍更清晰）；-o 输出
    for fmt, scale in (("png", 2), ("svg", 1)):
        out = f"{base}.{fmt}"
        try:
            r = subprocess.run(
                [exe, "-x", "-f", fmt, "-s", str(scale), "-o", out, src],
                capture_output=True, text=True, timeout=300,
                encoding="utf-8", errors="replace")
            if r.returncode == 0 and os.path.exists(out):
                result[fmt] = out
            else:
                print(f"⚠️ draw.io 导出 {fmt} 失败：{r.stderr[:200]}")
        except Exception as e:
            print(f"⚠️ draw.io 导出 {fmt} 异常：{e}")
    return result


if __name__ == "__main__":
    # 自测：挑 roadmaps 目录里最新的一个缓存，导出三种格式
    import json
    if not os.path.isdir(ROADMAP_DIR):
        print("roadmaps 目录不存在")
    else:
        files = sorted(
            (f for f in os.listdir(ROADMAP_DIR) if f.endswith(".v2.json")),
            key=lambda f: os.path.getmtime(os.path.join(ROADMAP_DIR, f)),
            reverse=True)
        if not files:
            print("roadmaps 目录里没有 .v2.json 缓存，先跑一次 generate_roadmap")
        else:
            with open(os.path.join(ROADMAP_DIR, files[0]),
                      encoding="utf-8") as f:
                r = json.load(f)
            print("测试论文：", r.get("paper"))
            paths = export_drawio(r)
            for k, v in paths.items():
                print(k, "→", v)
