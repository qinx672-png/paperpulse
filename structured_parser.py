# -*- coding: utf-8 -*-
"""
2.0 结构化解析器：把「流水账文字」变成「带章节标签的段落」。

三步：
1. 用 PyMuPDF 按页抽取文本，逐「行」扫描（标题常和正文挤在同一个块里）
2. 识别章节标题（编号格式为主，常见章节名为辅）→ 建立章节树
3. 段落按「所属章节 + 页码」打标签，超长段落按句拆 → 输出切块

产出（后续步骤全部吃这个）：
- title:     论文标题（按最大字号识别）
- sections:  目录结构（用于常驻提示词）
- chunks:    切块列表（text 带章节前缀，用于向量化入库）
- captions:  图表说明文字（用于图表清单）
"""
import os
import re
from dataclasses import dataclass, field

import pymupdf

from console_utils import safe_print

# 无编号的常见章节名（英文论文高频出现）。
# Elsevier 风格带编号（1. Introduction），Nature 风格光秃秃（Results/Methods），
# 所以两种都支持：编号靠 HEADING_RE，光秃秃的靠这里的「整行精确匹配」。
KNOWN_SECTIONS = {
    "abstract", "highlights", "keywords", "graphical abstract",
    "introduction", "main", "methods", "materials and methods",
    "results", "results and discussion", "discussion",
    "conclusion", "conclusions",
    "references", "bibliography", "literature cited",
    "acknowledgements", "acknowledgments", "credit authorship contribution statement",
    "data availability", "data availability statement", "supplementary material",
    "supplementary materials", "appendix", "author contributions", "conflict of interest",
    "code availability", "competing interests",
    "declaration of competing interest", "funding",
}

# 编号标题：形如 "1. Introduction" / "2.1. Study area" / "4.2.1. ..."
# 要求：数字编号 + 点 + 大写字母开头，且整行较短（标题不会太长）
# 第一位限制 1~2 位数字：避免把参考文献条目 "2015. Spatial variations..." 误当标题
HEADING_RE = re.compile(r'^\s*(\d{1,2}(?:\.\d+){0,2})\.\s+([A-Z][^\n]{0,90})$')

# 图表标题：形如 "Fig. 3." / "Figure 5" / "Table 2"（行首开头）
CAPTION_RE = re.compile(r'^\s*(Fig(?:ure)?\.?\s*\d+|Table\s*\d+)', re.IGNORECASE)

# 期刊页眉特征（这些行不是论文标题，要过滤掉）
JOURNAL_BANNER_WORDS = (
    "science of the total environment", "available online", "all rights reserved",
    "journal homepage", "contents lists", "elsevier",
    "nature water", "nature portfolio", "nature communications", "doi.org",
    "article",   # 2026-09-02：页眉页脚常是 "ARTICLE" / "Article"，不进图注
)

# 纯页码 / 页眉行：整行只有数字或太短
NOISE_RE = re.compile(r'^\s*\d{1,4}\s*$')


@dataclass
class ParsedDoc:
    """一篇论文的解析结果。"""
    title: str = ""
    pages: int = 0
    sections: list = field(default_factory=list)   # [{full, level, page}]
    chunks: list = field(default_factory=list)     # [{text, section, page}]
    captions: list = field(default_factory=list)   # [{id, text, page}]


def _is_heading(line: str) -> bool:
    """判断一行是不是章节标题（编号格式优先）。"""
    line = line.strip()
    if len(line) > 120:
        return False
    # 常见章节名（无编号）
    if line.lower() in KNOWN_SECTIONS:
        return True
    # 编号标题：数字.数字. 开头 + 大写字母
    return bool(HEADING_RE.match(line))


def _is_caption(line: str) -> bool:
    return bool(CAPTION_RE.match(line.strip())) and len(line.strip()) <= 300


# 标题续行不能以这些词开头（否则更像正文短句，如 "The samples were analyzed."）
_TITLE_TAIL_STOPWORDS = {"the", "a", "an", "in", "this", "these", "that", "those", "our",
                         "we", "they", "it", "its", "no", "all", "each", "both", "one",
                         "two", "three", "more", "less", "high", "low", "with", "for",
                         "as", "at", "to", "of", "and", "by", "on", "from", "is", "are",
                         "was", "were", "but", "when", "while", "which", "where", "using"}


def _is_title_tail(line: str) -> bool:
    """判断一行是不是「被换行截断的标题」的续行（如 "River estuary."）。
    条件：短、非标题、非 caption、无特殊字符、首词不像句子开头、词数少。"""
    text = line.strip()
    if (len(text) >= 60 or _is_heading(text) or _is_caption(text)
            or any(c in text for c in "(=FigTable")):
        return False
    words = text.split()
    if not words or words[0].rstrip(".,;:").lower() in _TITLE_TAIL_STOPWORDS:
        return False
    if text.endswith("."):        # 带句号的续行要更短更保守
        return len(text) < 40 and len(words) <= 4
    return len(words) <= 4


def _split_long(text: str, max_len: int = 700) -> list:
    """超长段落按句拆成多个不超过 max_len 的块。"""
    if len(text) <= max_len:
        return [text]
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z(])', text)
    pieces, buf = [], ""
    for s in sentences:
        if len(buf) + len(s) > max_len and buf:
            pieces.append(buf.strip())
            buf = s
        else:
            buf += " " + s
    if buf.strip():
        pieces.append(buf.strip())
    return pieces


def _extract_blocks_pymupdf(doc):
    """把 PDF 每页抽成「页 → 块 → 行」三层结构，每行是 (文字, 字号)。
    扫描版 PDF（没有文字层）这里会得到空列表——这是 OCR 兜底的触发条件。"""
    pages_blocks = []
    for page in doc:
        page_blocks = []
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:            # 跳过图片等非文本块
                continue
            raw_lines = []
            for ln in block["lines"]:
                if not ln.get("spans"):
                    continue
                text = "".join(s["text"] for s in ln["spans"]).strip()
                text = re.sub(r"\s+", " ", text)
                if not text or NOISE_RE.match(text) or len(text) < 3:
                    continue
                size = max(s["size"] for s in ln["spans"])
                raw_lines.append((text, size))
            if raw_lines:
                page_blocks.append(raw_lines)
        pages_blocks.append(page_blocks)
    return pages_blocks


def _scan(pages_blocks, title_hint=""):
    """核心扫描：逐行识别章节标题/caption/正文，给段落打「章节+页码」标签。
    输入既可以是 PyMuPDF 抽的块（带字号），也可以是 OCR 文字拼的块（没有字号）。
    title_hint：OCR 场景没有字号，第 1 页大字识别不出来，用这个兜底当标题。"""
    out = ParsedDoc(pages=len(pages_blocks))

    current_section = "Front matter"   # 还没遇到第一个标题之前的段落
    big_font_lines = []                # 第 1 页的大字号行（用来找论文标题）

    for pno, blocks in enumerate(pages_blocks):
        # 页级平铺：本页所有行按阅读顺序排成一列，每项 (块号, 是否块首行, 文字, 字号)。
        # 2026-09-02 第四轮：双栏 PDF 的图注常被切成左右两个文本块，
        # 吸收循环只在单块内扫会丢掉右栏的统计说明句——平铺后可以跨块续扫。
        flat = [(bi, li == 0, text, size)
                for bi, raw_lines in enumerate(blocks)
                for li, (text, size) in enumerate(raw_lines)]
        pending_title = False   # 上一块以「没结尾的标题行」收尾，等下一块首行补全
        body_buf = []           # 攒正文行，攒到标题或 caption 就收一段
        i, n = 0, len(flat)
        while i < n:
            bi, is_first, text, size = flat[i]

            # 块切换：段落随块边界收尾（保持原「一块一段」的切块习惯）
            if i > 0 and bi != flat[i - 1][0] and body_buf:
                _add_body(out, current_section, pno, " ".join(body_buf))
                body_buf = []

            # 块首行先看是不是上个块标题的续行（如 "River estuary"）
            if is_first:
                if pending_title and _is_title_tail(text):
                    out.sections[-1]["full"] += " " + text
                    current_section = out.sections[-1]["full"]
                    pending_title = False
                    i += 1
                    continue
                pending_title = False

            # 第 1 页大字号行 → 论文标题候选
            if pno == 0 and size >= 12:
                low = text.lower()
                if not any(w in low for w in JOURNAL_BANNER_WORDS):
                    big_font_lines.append(text)

            # 1) 章节标题行（参考文献区没有章节，跳过编号检测）
            if current_section != "References" and _is_heading(text):
                if body_buf:
                    _add_body(out, current_section, pno, " ".join(body_buf))
                    body_buf = []
                # 同行内：标题被换行截断时（如 "…the Yellow" / "River estuary"），把下一短行接回来
                if i + 1 < n and _is_title_tail(flat[i + 1][2]):
                    text = f"{text} {flat[i + 1][2]}"
                    i += 1
                current_section = text
                m = HEADING_RE.match(text)
                level = m.group(1).count(".") + 1 if m else 1
                out.sections.append({"full": current_section, "level": level, "page": pno + 1})
                # 标题行是块内最后一行且句尾没句号 → 下一块首行可能是标题续行
                is_last_in_block = (i + 1 >= n) or flat[i + 1][0] != bi
                pending_title = is_last_in_block and not text.rstrip().endswith(".")
                i += 1
                continue

            # 2) caption 行：编号后必须带说明文字。
            #    Fig 类要求「段落开头」（块首行或上一行以句号结尾）——
            #    正文里 "(Fig. 5)" 被换行顶到行首时上一行是逗号/介词，靠这个排除；
            #    Table 类放宽：表注常跟在表格行/小标题后，上一行未必有句号。
            # 块首行算段落开头（原逻辑 i==0 是块内首行；平铺后用 is_first，2026-09-02 修复）
            prev_ok = is_first or flat[i - 1][2].rstrip().endswith((".", "!", "?"))
            m_cap = CAPTION_RE.match(text)
            rest = CAPTION_RE.sub("", text).strip() if m_cap else ""
            # 编号后紧跟右括号/逗号说明是正文引用（如 "Table 3), and thus..."），不是真图注；
            # 编号后紧跟「a)」「2a)」这类面板编号也是正文引用（如 "Fig. 2a). At 25 days, ..."）
            # （2026-09-02 第四轮：跨块吸收后这类伪图注会把整段正文吸进来，必须拦住）
            if m_cap and (rest.startswith((")", "]", ",", ";"))
                    or re.match(r"^[a-z0-9]{1,3}\)", rest)):
                m_cap = None
            is_table = bool(m_cap and m_cap.group(1).lower().startswith("table"))
            # 表注换行：编号单独一行（"Table 1"），说明文字在下一行 → 先借下一行判断。
            # 2026-09-02 修复：下一行以「(」结尾也要借（说明文字后紧跟公式换行，
            # 如 "…concentration gradient ("），否则整个表格 caption 丢失
            if is_table and len(rest) < 8 and i + 1 < n:
                nxt_t = flat[i + 1][2]
                if len(nxt_t) < 100 and not any(c in nxt_t for c in "=∂"):
                    rest = nxt_t
            if m_cap and (prev_ok or is_table) and len(rest) >= 8:
                if body_buf:
                    _add_body(out, current_section, pno, " ".join(body_buf))
                    body_buf = []
                # 图注常有多句：标题句 + 「(a)…(b)…(c)…均值±标准误、* P<0.05」等统计说明句。
                # 2026-09-02 第四轮两处增强：
                # ① 跨块续扫——双栏 PDF 的图注右栏续行能吸到；
                # ② 表格数据行——Table 标题后跟的数值行（浓度/范围）一并吸收，
                #    「浓度是多少」这类题靠 caption 清单就能答，不再只能报表号。
                cap_lines = [text]
                j = i + 1
                sentence_ends = text.rstrip().count(".")
                while j < n and j - i < 40:
                    nxt = flat[j][2]
                    if _is_heading(nxt) or _is_caption(nxt):
                        break
                    # 期刊页眉/DOI 行（如 "Article https://doi.org/…"）不进图注
                    if any(w in nxt.lower() for w in JOURNAL_BANNER_WORDS):
                        break
                    # 已吸收完至少一句，下一行又长又是大写开头 → 更像正文段落，停
                    if sentence_ends >= 1 and len(nxt) > 100 and nxt[0].isupper():
                        break
                    cap_lines.append(nxt)
                    sentence_ends += nxt.rstrip().count(".")
                    j += 1
                out.captions.append({"id": m_cap.group(1), "text": " ".join(cap_lines), "page": pno + 1})
                i = j
                continue

            # 3) 正文行
            body_buf.append(text)
            i += 1

        if body_buf:
            _add_body(out, current_section, pno, " ".join(body_buf))

    # 第 1 页若含「摘 要/摘要/Abstract」等标志，说明首页是摘要页——
    # 大字行会把摘要正文误收进标题（实测秦肖论文 title 变成了「摘 要+摘要第一句」），
    # 整页作废。学位论文标题在封面页，导出 PDF 时常被删掉，这时 title 置空，
    # 由界面让用户手动填写。
    if any(("摘 要" in t) or ("摘要" in t) or re.match(r"^abstract\b", t, re.I)
           for t in big_font_lines):
        big_font_lines = []
    out.title = " ".join(big_font_lines[:3]) or title_hint or ""
    return out


def parse_pdf(pdf_path: str) -> ParsedDoc:
    """解析 PDF（要求有文字层）。扫描版请走 parse_pdf_smart。"""
    doc = pymupdf.open(pdf_path)
    return _scan(_extract_blocks_pymupdf(doc))


def parse_pdf_ocr(pdf_path, page_texts):
    """OCR 版解析：把每页 OCR 文字重走同一套扫描逻辑。
    OCR 文字没有字号，统一给 10——章节/图注判断不靠字号，不受影响；
    只有「第 1 页大字找标题」失效，改用 title_hint 兜底。
    关键细节：按 OCR 的空行把文字拆成「段落块」——PaddleOCR 的空行就是段落边界，
    这样每块首行天然是「段落开头」，Fig 类图注的段落开头检查才能通过。"""
    pages_blocks = []
    for text in page_texts:
        page_blocks, cur = [], []
        for raw in text.splitlines():
            ln = raw.strip()
            ln = re.sub(r'^#{1,6}\s+', '', ln)   # 去掉 markdown 标题符号（"# Results" → "Results"）
            ln = ln.replace("**", "")            # 去掉 markdown 加粗符号
            ln = re.sub(r"\s+", " ", ln).strip()
            if not ln:                           # 空行 = 段落边界 → 收掉当前块
                if cur:
                    page_blocks.append(cur)
                    cur = []
                continue
            if len(ln) >= 3 and not NOISE_RE.match(ln):
                cur.append((ln, 10))
        if cur:
            page_blocks.append(cur)
        pages_blocks.append(page_blocks)
    # 标题兜底：第 1 页第一条非页眉文字（OCR 场景论文标题一般就在最开头）
    hint = ""
    if pages_blocks and pages_blocks[0]:
        for block in pages_blocks[0]:
            for t, _s in block:
                if not any(w in t.lower() for w in JOURNAL_BANNER_WORDS):
                    hint = t
                    break
            if hint:
                break
    return _scan(pages_blocks, title_hint=hint)


def parse_pdf_smart(pdf_path, ocr_force=False):
    """统一入口：有文字层直接解析；解析不出正文块 → 自动走 OCR 兜底（带缓存）。
    所有下游步骤（向量化/预答案/问答）都用这个入口，扫描版和文字版一视同仁。
    ocr_force=True 强制重新 OCR（比如怀疑缓存内容不对时）。"""
    parsed = parse_pdf(pdf_path)
    if parsed.chunks:
        return parsed
    from ocr_fallback import ocr_pdf_cached   # 懒加载，避免模块循环依赖
    safe_print(f"{os.path.basename(pdf_path)} 没解析出正文块，疑似扫描版 PDF，走 OCR 兜底…")
    page_texts = ocr_pdf_cached(pdf_path, force=ocr_force)
    parsed = parse_pdf_ocr(pdf_path, page_texts)
    safe_print(f"  OCR 解析完成：{len(parsed.sections)} 个章节、{len(parsed.chunks)} 块")
    return parsed


def _add_body(out: ParsedDoc, section: str, pno: int, text: str):
    """正文段落：按章节+页码打标签，超长按句拆，过短跳过（公式残渣/页眉）。"""
    for piece in _split_long(text):
        piece = piece.strip()
        # 去掉段落里重复的章节标题（PDF 文字层偶尔把标题行在正文开头再印一遍）
        if section and (piece.startswith(section + " ") or piece.startswith(section + ".")):
            piece = piece[len(section):].lstrip(" .")
        if len(piece) < 40:
            continue
        low = piece.lower()
        if any(w in low for w in JOURNAL_BANNER_WORDS):   # 过滤期刊页眉行
            continue
        out.chunks.append({
            "text": f"{section}: {piece}",   # 携带章节前缀
            "section": section,
            "page": pno + 1,
        })


def _demo(p: ParsedDoc):
    """命令行自测：打印解析结果概览。"""
    print(f"论文标题: {p.title}")
    print(f"总页数: {p.pages}\n")
    print("章节树:")
    for s in p.sections:
        indent = "  " * (s["level"] - 1)
        print(f"  {indent}{s['full']}  (p{s['page']})")
    print(f"\n切块总数: {len(p.chunks)}")
    for c in p.chunks[:5]:
        print(f"  [p{c['page']}] {c['text'][:90]}...")
    print(f"\n图表 caption 数: {len(p.captions)}")
    for c in p.captions:
        print(f"  {c['id']} (p{c['page']}): {c['text'][:80]}")


if __name__ == "__main__":
    import io
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    # 用法：python structured_parser.py "<PDF>" [--smart] [--force]
    # --smart = 扫描版自动走 OCR 兜底；--force = 强制重新 OCR（覆盖旧缓存）
    args = [a for a in sys.argv[1:] if a not in ("--smart", "--force")]
    pdf = args[0] if args else r"C:\Users\17784\Desktop\YRW和组会\文献精读笔记\YRW-潮汐影响氧化还原敏感元素的释放.pdf"
    parsed = (parse_pdf_smart(pdf, ocr_force="--force" in sys.argv)
              if "--smart" in sys.argv else parse_pdf(pdf))
    _demo(parsed)
