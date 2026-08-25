# 科研文献 AI 助手（RAG Demo）

零基础可跑通的「科研场景 AI 助手」最小产品：上传论文 PDF，即可对文献内容提问。

基于 RAG（检索增强生成）：先把论文切块、向量化，提问时检索最相关段落，再让大模型据此回答，做到"有据可查、不瞎编"。

## 一、环境准备

1. 确认 Python 3.9+：命令行运行 `python --version`（本机已装 3.12.3 ✓）
2. 安装依赖：
   ```
   pip install -r requirements.txt
   ```
   如果用 Anaconda：`conda install streamlit pypdf numpy requests`

## 二、申请 API Key（硅基流动）

1. 打开 https://siliconflow.cn 注册登录
2. 控制台 → API 密钥 → 新建密钥，复制
3. 打开「模型广场」，确认已开通：
   - 向量化模型：`BAAI/bge-m3`
   - 对话模型：`deepseek-ai/DeepSeek-V3`（或免费的 `Qwen/Qwen2.5-7B-Instruct`）
4. 把 Key 填进 `config.py` 的 `API_KEY`

## 三、运行

先测 Key 是否可用：
```
python test_api.py
```

再启动界面：
```
streamlit run app.py
```

浏览器会自动打开 http://localhost:8501 ，上传几篇论文 PDF，开始提问。

## 四、常见问题

| 报错 | 原因 |
|---|---|
| 401 / 认证失败 | API Key 填错或没填 |
| model not found | 去模型广场确认该模型已开通 |
| PDF 提取不到文字 | 扫描版图片 PDF，需先 OCR，本 demo 暂不支持 |
| 想改切块大小/检索数量 | 调 `rag.py` 里的 `chunk_size` / `top_k` |

## 五、项目结构

- `config.py`    配置（填 Key）
- `rag.py`       核心逻辑（解析 → 切块 → 向量化 → 检索 → 生成）
- `app.py`       Streamlit 界面
- `test_api.py`  连通性自检
