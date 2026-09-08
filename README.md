# PaperPulse · Your Research Trace Weaver

🔬 **科研文献 AI 助手** | 让文献精读从 2 小时缩短到 15 分钟

[![Streamlit App](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://paperpulse.streamlit.app)

---

## ✨ 核心功能

### 1. 📄 智能解析
- 支持 PDF 文献自动解析（PyMuPDF + PaddleOCR 兜底）
- 结构感知切块（Elsevier 编号风格 + Nature 光秃风格双兼容）

### 2. 🔍 混合检索
- **bge-m3** 中文学术语义向量
- **BM25** 关键词检索
- **RRF 融合**（Reciprocal Rank Fusion）
- 召回准确率：84.1%（细节题 94.6% / 图表题 100%）

### 3. 🧠 八字段精读档案
自动生成论文核心要点：
- 研究意义
- 领域问题
- 拟解决问题
- 研究内容
- 主要结果
- 结论
- 创新点
- 不足与展望

### 4. 🗺️ 研究路线图
- 实体-关系流程图（样品/方法/参数/结果/结论）
- 自研 SVG/PNG 渲染引擎（支持化学式上下标、双语混排）

### 5. 🕸️ 知识图谱
跨论文关系网络可视化：
- 引用关系（代码确定性匹配）
- 同研究对象/方法/结论关系（LLM 判断 + evidence）
- pyvis 交互式力导向布局

### 6. 💬 问答溯源
- 每个回答带原文页码引用
- 100% 可溯源，杜绝 AI 幻觉

---

## 🎯 五轮评测体系

| 题型 | 题数 | 准确率 | 说明 |
|------|------|--------|------|
| 细节题 | 15 | 94.6% | 人名/数据/方法细节 |
| 图表题 | 12 | 100% | 图表信息提取 |
| 归纳题 | 10 | 69.5% | 全文总结归纳（行业短板） |

**四模型横向对比**：DeepSeek-V4-Pro / Kimi / 豆包 / GLM-4

**A/B 对照实验**：rerank 配置优化使细节题准确率提升 8.2%

---

## 🚀 快速开始

### 本地运行

```bash
# 克隆仓库
git clone https://github.com/qinx672-png/research-ai-assistant.git
cd research-ai-assistant/v2.0

# 安装依赖
pip install -r requirements.txt

# 配置 API Key（在 config.py 中）
# 设置 DeepSeek API Key

# 启动应用
streamlit run app.py --server.port 8502
```

访问：http://localhost:8502

### 在线体验

🌐 **Streamlit Cloud**：https://paperpulse.streamlit.app

🤗 **Hugging Face**：https://huggingface.co/spaces/qinxiao/paperpulse

---

## 🛠️ 技术栈

| 层 | 技术 |
|----|------|
| **前端** | Streamlit |
| **解析** | PyMuPDF + PaddleOCR |
| **向量** | bge-m3 + Chroma |
| **检索** | BM25 + 向量混合（RRF 融合） |
| **生成** | DeepSeek-V4-Pro |
| **渲染** | 自研 SVG/PNG 引擎 |
| **图谱** | pyvis (vis.js) |

---

## 📊 项目结构

```
v2.0/
├── app.py                      # Streamlit 主界面
├── preanswer.py               # 八字段精读档案生成
├── roadmap.py                 # 研究路线图生成
├── knowledge_graph.py         # 跨论文知识图谱
├── structured_parser.py       # PDF 智能解析
├── vector_store.py            # bge-m3 + Chroma
├── retriever.py               # BM25 + 向量混合检索
├── qa.py                      # 问答 + 溯源
├── evaluate.py                # 评测体系
├── config.py                  # 配置文件
└── requirements.txt           # 依赖列表
```

---

## 🎓 WorkBuddy 专家包

PaperPulse 已上线**腾讯 WorkBuddy 开放平台**，以专家包形式提供：

🔗 **文献精读助手**：在 WorkBuddy 中搜索 "PaperPulse" 或 "文献精读"

---

## 📝 引用

如果 PaperPulse 对你的研究有帮助，欢迎引用：

```bibtex
@software{paperpulse2026,
  author = {Qin, Xiao},
  title = {PaperPulse: Your Research Trace Weaver},
  year = {2026},
  url = {https://github.com/qinx672-png/research-ai-assistant}
}
```

---

## 📧 联系方式

- **作者**：秦肖
- **邮箱**：qx2024@sjtu.edu.cn
- **机构**：上海交通大学 海洋学院

---

## 📄 许可证

MIT License

---

⭐ 如果觉得有用，请给个 Star！
