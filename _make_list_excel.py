# -*- coding: utf-8 -*-
"""一次性脚本：把「AI产品经理投递清单」md 转成 Excel。
分 5 个 sheet：第一档 / 第二档 / 保底 / 第三档(27届备查) / 行动时间表。
链接做成可点击超链接，表头样式学术稳重，原 md 文件不动。
"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

OUT = r"C:\Users\17784\Desktop\2026-秦肖求职简历合集\AI产品经理投递清单-2026-09-07.xlsx"

# ---------- 样式 ----------
HEADER_FILL = PatternFill("solid", fgColor="D9E2F3")
HEADER_FONT = Font(bold=True, size=11, color="1F3864")
BODY_FONT = Font(size=10)
LINK_FONT = Font(size=10, color="0563C1", underline="single")
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical="top")
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)


def est_height(row_vals, widths):
    """按列宽估算换行行数 → 行高，避免文字被裁。"""
    lines = 1
    for v, w in zip(row_vals, widths):
        text = str(v) if v else ""
        n = 0
        for seg in text.split("\n"):
            # 中文按 2 个宽度计，容量 = 列宽*2（列宽单位≈1个英文字符）
            w_seg = sum(2 if ord(c) > 127 else 1 for c in seg)
            n += max(1, -(-w_seg // max(int(w * 2 * 0.92), 10)))
        lines = max(lines, n)
    return min(15 * lines + 6, 150)


def make_sheet(wb, title, headers, widths, rows, link_cols):
    ws = wb.create_sheet(title)
    # 表头
    for j, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=j, value=h)
        c.fill = HEADER_FILL
        c.font = HEADER_FONT
        c.alignment = CENTER
        c.border = BORDER
        ws.column_dimensions[get_column_letter(j)].width = widths[j - 1]
    ws.row_dimensions[1].height = 24
    ws.freeze_panes = "A2"
    # 数据
    for i, row in enumerate(rows, 2):
        for j, v in enumerate(row, 1):
            c = ws.cell(row=i, column=j, value=v)
            c.border = BORDER
            c.font = BODY_FONT
            c.alignment = WRAP
            if j in link_cols and isinstance(v, str) and v.startswith(("http", "mailto")):
                c.hyperlink = v
                c.font = LINK_FONT
        ws.row_dimensions[i].height = est_height(row, widths)
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(rows) + 1}"
    return ws


wb = Workbook()
wb.remove(wb.active)  # 去掉默认空 sheet

# ---------- Sheet1 第一档 ----------
make_sheet(
    wb, "🟢 第一档-明确可投",
    ["优先级", "公司 / 岗位", "届别结论", "关键要点", "截止时间", "投递链接", "备注"],
    [6, 28, 11, 48, 20, 38, 40],
    [
        ["1", "腾讯 2027 校园招聘 · 产品类（含 AI 产品）", "26届可投",
         "毕业时间窗口 2026.1.1–2027.12.31（2026.6 毕业符合）；滚动招聘无统一硬截止",
         "约 10 月中旬收尾", "https://join.qq.com",
         "上传 4 个作品集当「个人 AI 作品/项目」；今晚优先投"],
        ["2", "百川智能「源点计划」应届岗 · AI 产品方向", "26届可投",
         "面向 2025–2027 届；公告岗位专业不限；终面王小川亲面",
         "2026-09-01 至 2026-12-01", "mailto:campus@baichuan-inc.com",
         "邮件主题：源点计划＋姓名＋职位方向；计划 9-07 刚发布，正热"],
        ["3", "科大讯飞 2027 秋招 · AI 产品经理", "26届可投",
         "窗口 2025.6.1–2027.8.31（全网最宽）；不限专业、不限本硕、不要求经验",
         "招满即关", "https://iflytek.zhiye.com", "合肥/北京/上海等多城"],
        ["4", "小红书 RPT 产品培训生（AI × 社区）", "26届可投",
         "26 届 + 毕业两年内 + 专业不限；双岗轮训 + 资深 Leader 一对一",
         "第三方标 9-15，本周官网确认", "https://campus.xiaohongshu.com",
         "注意另有 27 届版「产品经理培训生（AI 方向）」，投前核实届别"],
        ["5", "DeepSeek AI 产品经理（社招）", "不限届别",
         "JD 原文「经验不限、学历不限、学生可投」；看重完整交付经验 → 4 个作品集对口",
         "滚动，未查到确切数据", "mailto:talent@deepseek.com",
         "主题：姓名-岗位-联系方式；北京/杭州 25-50k·16 薪；避开 Agent Harness 方向（要求 2 年+）"],
        ["6", "中国平安「金橙人才计划」产品类", "26届可投",
         "窗口 2026.1.1–2027.12.31；6000+ Offer，产品岗偏金融科技/AI 应用",
         "简章标 2026-12-31", "https://campus.pingan.com", "成员公司口径不一，以官网为准"],
        ["7", "华为 2026 届应届生通道", "26届可投",
         "国内本硕窗口 2026.1.1–2026.12.31；AI 产品经理岗未查到确切岗位数据",
         "销售类截至 2026-12-31", "https://career.huawei.com",
         "注册后按「产品类 + AI」实时核实"],
        ["8", "字节跳动 · 社招「人才系统 AI 产品经理-管理研究院」", "不限届别",
         "本科及以上、不限专业，有产品实习/项目经验者优先（非硬性）；官方口径「看潜力」",
         "滚动，未查到确切数据",
         "https://jobs.bytedance.com/experienced/position/7597663577026447669/detail",
         "用 RAG 作品集补实习缺口；官网再搜「AI 产品」筛经验不限"],
        ["9", "快手「青锋计划」社招专项", "26届可投",
         "面向 3 年以内职场新人（0 经验应届在范围内）；产品/算法/运营/市场；1 对 1 带教",
         "9 月是否在招未查到确切数据", "官网核实", "需上快手社招官网核实当前状态"],
    ],
    link_cols={6},
)

# ---------- Sheet2 第二档 ----------
make_sheet(
    wb, "🟡 第二档-需核实",
    ["公司 / 岗位", "届别", "关键要点", "投递链接", "备注"],
    [30, 12, 46, 42, 30],
    [
        ["亚信科技 · AI 产品经理（应届，南京）", "26届",
         "JD 与你的 RAG/Agent 项目最匹配；要求会用 Dify/Coze 类平台；专业「优先」非硬性",
         "https://m.yingjiesheng.com/job-007-970-604.html", "是否仍开放需核实"],
        ["智元创新 · 产品经理（2026 校招）", "26届",
         "不限专业；要求深度体验 ChatGPT/Claude、会 AI Coding 工具（Claude Code/Cursor）——技能全对口",
         "https://www.shushuqiuzhi.com/position/457882", ""],
        ["乐其集团 · AI Agent 交付产品经理（校招 5 人）", "26届",
         "需理解 Agent 架构（Planning/Memory/Tool/MCP/RAG）",
         "https://www.wondercv.com/xiaozhao/leqee-2026-spring-ai-agent-10542-243500/",
         "是否仍开放需核实"],
        ["4399 · AI 产品经理（26 届补录，广州）", "26届",
         "补录通道是否仍在进行需官网核实",
         "https://sfi.cuhk.edu.cn/zh-hans/node/9353", "原公告链接"],
        ["MiniMax · 大模型产品经理", "26届",
         "2026-04-22 公告明确列「产品类：大模型产品经理」",
         "https://cs.hust.edu.cn/info/1404/5706.htm", "是否仍开放需核实"],
        ["智谱 AI · 智谱星 2026 校招-产品经理", "26届",
         "毕业窗口 2025.9–2026.8；去年 9 月启动、招满即关",
         "https://campus.niuqizp.com/job-vsU5LCC5N.html", "现状需核实"],
        ["月之暗面 · 增长团队管培生（350/天）", "已毕业可投",
         "「本科/研究生在读、应届毕业生或职业早期人士」；低门槛入口",
         "https://app.mokahr.com/recommendation-recruitment/moonshot/148508", "实习岗"],
        ["零一万物 · AI Agent 产品经理 / AI 产品经理（国际）", "社招不限届别",
         "官方 JD 未展示年限门槛",
         "https://01ai.jobs.feishu.cn/index/position/list", "经验要求未查到确切数据"],
        ["秘塔科技 · 产品经理（CEO 直带）", "社招",
         "要求「1 年或更多 C 端产品经验」但明确「不需要高 DAU 产品经验」",
         "https://mp.weixin.qq.com/s/f8DfpA8WguZUqe36-hYVJw", "可邮件问作品集能否折算"],
        ["腾讯（社招）· 元宝-AI 产品经理（生态合作方向）", "社招",
         "唯一未写死年限的官方岗；「有 AI 产品策划经验（C 端 AI APP 优先）」",
         "https://careers.tencent.com/m/jobdesc.html?postId=2021054127102132224",
         "用作品集佐证"],
        ["腾讯（校招）· 青云计划", "26届",
         "截止 2026-09-28；以技术岗为主，非科班难度大",
         "https://www.jiemian.com/article/14770307.html", "备选"],
        ["百度 / 网易 / 京东 · 社招「经验不限」AI 产品岗", "社招",
         "JD 隐含「AI/计算机专业优先 + 产品落地经验」，零经验偏弱，作补充投递",
         "百度 https://m.liepin.com/job/1984545847.shtml\n网易 https://m.liepin.com/job/1983124019.shtml\n京东 https://www.liepin.com/job/1978211023.shtml",
         "均需官网核实最新状态"],
    ],
    link_cols={4},
)

# ---------- Sheet3 保底 ----------
make_sheet(
    wb, "🟢 保底-中小厂",
    ["公司 / 岗位", "城市 / 薪资", "关键要点", "投递链接"],
    [30, 14, 44, 42],
    [
        ["湖南小算科技 · AI 产品经理", "北京 20-40K", "RPA/出海工具，不设经验门槛",
         "https://baiquan.qqherczp.com/job/335692202.html"],
        ["智学星途 · AI 教育产品经理", "上海", "专业不限，清北复校友团队；与你主攻 AI 教育方向对口",
         "https://www.zhaopin.com/jobdetail/CCL1523806640J40859900314.htm"],
        ["天津小橙集团 · AI 产品经理", "北京", "AI 养老方向，硕博应届优先",
         "https://career.nankai.edu.cn/correcruit/content/id/116180.html"],
        ["顿杨智能控制 · AI 产品经理", "上海 15-20K", "硕士，经验不限",
         "http://xuejob.com/index/job/detail/id/13037.html"],
        ["山东众阳健康 · AI 产品经理", "济南", "医疗大模型/RAG，专业卡得较紧",
         "https://m.miyi.scpzhrcw.com/job/362884472.html"],
        ["青岛利诚达 · AIGC 产品经理", "青岛", "接受无经验转行",
         "https://www.zhaopin.com/jobdetail/CC466908380J40923124112.htm"],
    ],
    link_cols={4},
)

# ---------- Sheet4 第三档 ----------
make_sheet(
    wb, "🔴 第三档-27届限定",
    ["公司", "岗位", "毕业窗口（26 届不可投）", "链接（备查）"],
    [12, 40, 26, 50],
    [
        ["字节", "2027 校招 AI 产品经理（豆包/抖音等）", "2026.9–2027.8",
         "https://edu.cnr.cn/gc/20260804/t20260804_527746913.shtml"],
        ["阿里", "2027 校招 AI 产品 / AI Agent 产品经理", "2026.11–2027.10",
         "https://career.nankai.edu.cn/correcruit/content/id/116689.html"],
        ["百度", "2027 校招 AI 产品经理（招 42 人）+ P-STAR", "2026.9.1–2027.8.31",
         "https://m.nj.bendibao.com/job/182568.shtm"],
        ["美团", "2027 校招 AI 产品经理", "2026.11–2027.10",
         "https://www.cqrb.cn/caijingzonghe/2026-08-18/2753377_pc.html"],
        ["快手", "2027 秋招产品类", "2026.11–2027.10",
         "https://job.lzu.edu.cn/html/22/article/2026/90999.html"],
        ["网易", "2027 校招大模型/游戏 AI 产品", "2026.9–2027.8",
         "https://www.163.com/dy/article/L4Q04Q480511A6N9.html"],
        ["京东", "2027 JD STAR / TET 产品方向", "2026.10.1–2027.9.30",
         "https://jy.xmu.edu.cn/campus/view/id/1001864"],
        ["小米", "2027 全球校招产品类", "2026.9.1–2027.8.31",
         "https://ahu.ahbys.com/job2.html?jid=12360&cid=18748"],
        ["拼多多", "产品管培生", "2026.9–2027.8（提前批 10-24 截止）",
         "https://www.shixiseng.com/intern/inn_vc4tkjblrtxu"],
        ["B 站", "2027 秋招产品运营类", "2026.9.1–2027.8.31（网申 8/3–10/3）",
         "https://finance.sina.cn/2026-08-03/detail-inikzzhm2659348.d.html"],
        ["小红书", "27 届产品经理培训生 / REDstar", "2026.9–2027.8",
         "https://campus.niuqizp.com/job-vwy5a5anM.html"],
        ["滴滴", "2027 校招产品类", "2026.9–2027.8",
         "http://www.offcn.com/gqzp/2026/0813/252376.html"],
        ["华为", "2027 届（国内本硕须 2027 年内毕业）", "2027 年内毕业",
         "https://www.sohu.com/a/1064668791_121124209"],
        ["荣耀", "2027", "2026.10.1–2027.9.30（网申至 2026-09-30）",
         "https://job.hdu.edu.cn/campus/view?id=9539"],
        ["米哈游", "2027", "2026.9–2027.8",
         "https://www.cqbys.com/campus/view/id/731165#1"],
        ["月之暗面", "「穿越计划」27 届顶尖人才（16 人）", "27 届",
         "https://www.36kr.com/newsflashes/3750554834403849"],
        ["阶跃星辰", "StepStar 语音大模型产品经理", "2026.9–2027.8",
         "https://ds.ncss.cn/student/jobs/2C3s9YaBgYdaTeL3E5JSEV/detail.html"],
        ["昆仑万维", "27 届产品经理", "截止 2026-11-28",
         "https://www.nowcoder.com/jobs/detail/464220"],
        ["它石智航", "TARS 机器人产品经理", "27 届",
         "https://www.cqbys.com/job/view/id/1409425"],
    ],
    link_cols={4},
)

# ---------- Sheet5 行动时间表 + 重要提醒 ----------
ws = wb.create_sheet("📋 行动时间表")
ws.column_dimensions["A"].width = 16
ws.column_dimensions["B"].width = 100

rows = [
    ("⏰ 时间", "行动"),
    ("今天（9-07 晚）", "① 查邮箱/短信：腾讯产培生是否过初筛（9.5–9.8 是 AI 实战作业期，此事不查会误大事）\n② 投腾讯 2027 校招产品岗（join.qq.com）\n③ 邮件投百川「源点计划」（今天刚发布）"),
    ("本周", "科大讯飞 → 小红书 RPT → 平安金橙 → DeepSeek → 字节社招 → 华为核实"),
    ("保底批次", "亚信科技、智元创新、乐其、4399、智学星途（AI 教育）等中小厂"),
    ("下周", "核实 MiniMax / 智谱星 / 快手青锋，启动第二批投递"),
    ("", ""),
    ("⚠️ 重要提醒", ""),
    ("1", "腾讯 AI 产培生已于 2026-09-04 12:00 截止。若投过且过初筛，正处 9.5–9.8 AI 实战作业期；没投过则面试材料里别写「已投递产培生」"),
    ("2", "待核实项（第三方信息只作线索，投前官网确认）：小红书 RPT 秋季批截止日；快手青锋计划 9 月是否在招；华为 26 届通道内是否有 AI 产品岗；MiniMax / 智谱星 26 届批次现状；各社招岗最新在招状态"),
    ("3", "已截止备查：腾讯 AI 产培生（9-04）；百度 26 春招 AI 产品（5-16）；金山办公云杉计划（2025 秋）；滴滴 26 春招产品（8-12）"),
    ("4", "警惕：个别「经验不限」实为培训+就业推荐项目（近屿智能类）或实际要求 5 年（邓白氏类），投前看清 JD"),
]
for i, (a, b) in enumerate(rows, 1):
    ca = ws.cell(row=i, column=1, value=a)
    cb = ws.cell(row=i, column=2, value=b)
    for c in (ca, cb):
        c.border = BORDER
        c.font = BODY_FONT
        c.alignment = WRAP
    if i == 1 or a.startswith("⚠️"):
        for c in (ca, cb):
            c.fill = HEADER_FILL
            c.font = HEADER_FONT
    ws.row_dimensions[i].height = est_height([a, b], [16, 100])
ws.freeze_panes = "A2"

note = ws.cell(row=len(rows) + 2, column=1,
               value="* 本表由 Claude Code 四路并行搜索整理（2026-09-07 晚）。招聘信息随时变动，投递前以官网为准。")
note.font = Font(size=9, italic=True, color="808080")

wb.save(OUT)
print("已生成：", OUT)

# 校验：重新打开数一下
from openpyxl import load_workbook
wb2 = load_workbook(OUT)
for ws2 in wb2.worksheets:
    print(f"  sheet「{ws2.title}」：{ws2.max_row} 行 x {ws2.max_column} 列")
