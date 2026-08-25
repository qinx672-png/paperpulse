# -*- coding: utf-8 -*-
"""
配置文件：填好 API Key 后即可运行。

推荐用「硅基流动 SiliconFlow」：一个 Key 同时支持向量化(embedding)和对话(chat)，
国内直连、有免费模型，最适合做 demo。
注册：https://siliconflow.cn  →  控制台 → API 密钥 → 新建密钥
"""

# ① 把你自己的 API Key 填到这里（替换下面这一整串占位符）
API_KEY = "在这里填你的 SiliconFlow API Key"

# ② 接口地址（硅基流动，OpenAI 兼容格式，一般不用改）
BASE_URL = "https://api.siliconflow.cn/v1"

# ③ 模型选择（如果模型报错，去官网「模型广场」确认已开通）
EMBEDDING_MODEL = "BAAI/bge-m3"              # 向量化模型：负责"找到相关内容"
CHAT_MODEL = "deepseek-ai/DeepSeek-V3"       # 对话模型：负责"写答案"
# 免费替代（效果稍弱但零成本）：CHAT_MODEL = "Qwen/Qwen2.5-7B-Instruct"
