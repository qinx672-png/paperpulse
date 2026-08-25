# -*- coding: utf-8 -*-
"""先跑这个确认 Key 和模型是否可用：python test_api.py"""
from rag import get_embedding, chat

print("测试向量化模型...")
v = get_embedding("这是一个测试文本")
print("向量维度：", len(v), "（有数字就说明 embedding 通了）")

print("\n测试对话模型...")
print(chat([{"role": "user", "content": "你好，请用一句话介绍你自己"}]))
print("\n✅ 两项都通过，就可以运行 streamlit run app.py 了")
