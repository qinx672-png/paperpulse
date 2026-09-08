# -*- coding: utf-8 -*-
"""
2.0 配置文件：双供应商混合方案（2026-09-06 用户拍板）。

背景：硅基流动欠费，用户决定：
  对话模型  → 火山方舟（账户有余额，chat 是消耗大头）
  embedding → 硅基流动 bge-m3（保住评测第 1~4 轮检索基线，且价格可忽略）
  OCR 兜底  → 硅基流动 PaddleOCR-VL（免费模型，方舟没有对应物）
两家的接口都是 OpenAI 兼容格式，调用代码只换地址、密钥、模型名。

密钥说明：都存本地 txt（不进代码库，作品集仓库是公开的）：
  ark_api_key.txt —— 方舟密钥 ark-xxx（从即梦项目复制，同账号通用）
  sf_api_key.txt  —— 硅基密钥 sk-xxx（从 1.0 config.py 迁移）
"""
import os

_DIR = os.path.dirname(os.path.abspath(__file__))


def _read_key(name):
    """从环境变量或文件读取 API Key"""
    # 优先从环境变量读取（用于 Streamlit Cloud）
    env_name = name.replace(".txt", "").upper()
    key = os.environ.get(env_name)
    if key:
        return key

    # 本地开发从文件读取
    key_path = os.path.join(_DIR, name)
    if os.path.exists(key_path):
        with open(key_path, encoding="utf-8") as f:
            return f.read().strip()

    # 兜底：返回空字符串，避免崩溃
    return ""


# ---------- 火山方舟：对话模型 ----------
ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
ARK_API_KEY = _read_key("ark_api_key.txt")

# ---------- 硅基流动：embedding + OCR ----------
SF_BASE_URL = "https://api.siliconflow.cn/v1"
SF_API_KEY = _read_key("sf_api_key.txt")

# 兼容旧引用（llm.py 等默认走方舟）
BASE_URL = ARK_BASE_URL
API_KEY = ARK_API_KEY

# 向量化模型：保持 bge-m3 不换（评测 1~4 轮基线，换模型检索质量会变）
EMBEDDING_MODEL = "BAAI/bge-m3"

# 2.0 默认对话模型：DeepSeek-V4-Flash GA 版（"快而省"，方舟已开通）
CHAT_MODEL = "deepseek-v4-flash-ga-260731"

# 评测用模型池：一键切换（2026-09-06 按用户要求换成国内外主流头部模型）。
# 现实约束：GPT / Gemini / Claude 官方 API 都需要海外信用卡（用户没有），
# 国内无正规直连渠道，因此海外头部进不了评测池——只覆盖国内头部五厂：
#   DeepSeek（双档） / 字节豆包 / 智谱 / 月之暗面 Kimi。
# 前 4 档走方舟；kimi 走硅基流动（方舟控制台已搜不到 Kimi 开通入口，且硅基上
# Kimi 产品线大幅缩水：K3 未上架、K2.5/Thinking/Instruct 已停用，仅存
# K2.7-Code 和 Pro 版 K2.6。评测档用 Pro/moonshotai/Kimi-K2.6 通用旗舰）。
# llm.py 按「模型名是否含 /」自动路由供应商：硅基命名带厂商前缀，方舟是纯模型名。
# 评测第 1~4 轮 chat 在硅基流动上跑，第五轮换方舟（同模型不同托管方），
# 结果与历史四轮对比时要注明「生成侧供应商切换」，不能直接混着比。
# 2026-09-06 B 方案：不干预思考模式，各家默认形态实测（=用户真实体验）。
CHAT_MODELS = {
    "v4-flash": "deepseek-v4-flash-ga-260731",   # DeepSeek 性价比档（方舟）
    "v4-pro": "deepseek-v4-pro-ga-260813",       # DeepSeek 旗舰（方舟）
    "glm": "glm-5-2-260617",                     # 智谱旗舰（方舟）
    "kimi": "Pro/moonshotai/Kimi-K2.6",          # 月之暗面 Kimi（硅基）
}

# 向量化批量参数（1.0 实测：逐块串行 11 页约 8 分钟；批量 3 块/请求 4.45 秒）
EMBED_BATCH_SIZE = 16

# 混合检索参数
TOP_K = 8        # 向量召回块数（1.0 是 3，2.0 加大，保证主要+辅助都在）
BM25_K = 4       # BM25 关键词召回块数（兜底精确术语）

# ---------- Rerank 精排（2026-09-06 用户拍板：固定为管线一环）----------
# 工作位置：RRF 融合之后、生成之前。先多捞（12 块）再精排取前 8——
# 精排的价值就在于把 RRF 漏掉的「第 9~12 名好块」捞进前排。
# 选型：Qwen3-Reranker-4B（¥0.14/百万 token，每次提问不到 1 厘钱）——
# 中文榜 CMTEB-R 75.94 最强性价比档；bge-reranker-v2-m3 中文低 3.78 分；
# 8B 贵一倍仅边缘提升（候选池才十几个块，4B 已接近饱和）。走硅基流动。
# 多捞不影响无 rerank 路径的排名（RRF 只按名次算分），所以对照实验 A 组
# 关掉 rerank 后与评测 1~4 轮基线完全一致，两组唯一差异 = 有没有精排环节。
RERANK_MODEL = "Qwen/Qwen3-Reranker-4B"
RERANK_FETCH_K = 12      # rerank 前候选池大小：向量路从 8 多捞到 12
RERANK_ENABLED = False   # 2026-09-07 第五轮 A/B 实测后关掉：细节型 3 题全变差（-17/-50/-40，
                         # 合计 -107 分）、归纳/图表零收益 → 语义精排把「含精确数值的 BM25 好块」
                         # 挤出前 8。证据见评测结果.第五轮A/B.xlsx。代码和开关都保留，
                         # 想再验证改回 True 即可；评测脚本显式传 use_rerank 不受此影响。

# ---------- OCR 兜底（步骤 8，给扫描版 PDF 用）----------
# 走硅基流动的 PaddleOCR-VL（免费模型，方舟没有对应物）。
# 实测对比：DeepSeek-OCR 在密集双栏页面上会「脑补」页面上没有的文字（已知缺陷），
# PaddleOCR-VL 是百度飞桨专为 OCR 训练的视觉模型，逐页准确、速度相近，用它做默认。
OCR_MODEL = "PaddlePaddle/PaddleOCR-VL-1.5"
OCR_DPI = 200                             # 页面转图片的分辨率（越高越清楚，越慢越贵）
