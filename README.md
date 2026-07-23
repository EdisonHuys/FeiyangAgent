# 飞扬流派：多周期市场预测 Desktop GUI 智能体

[![Python Version](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-macOS-lightgrey.svg)](https://apple.com)

**FeiyangAgent** 是一个基于加密货币市场数据的自动化智能体（Agent）预测与交易诊断系统。

系统能够自动拉取币安多维度时间周期的数据，在本地实时计算技术指标（MA、EMA、Bollinger Bands）与斐波那契结构位，并将经过清洗压缩后的轻量级结构化载荷（JSON Payload）提供给大语言模型（LLM）。LLM 严格遵循分析师“飞扬”防守型右侧交易逻辑，输出结构化交易信号与诊断报告，同时支持推送至 Telegram、Server酱或 Bark 等通知渠道。

为了提升交互体验，系统提供基于 `pywebview` + `React` + `TradingView` 打造的原生 macOS 桌面客户端。

---

## 🎨 核心特性

- **桌面客户端（Native GUI）**：采用 `pywebview` 封装原生的 macOS Cocoa 窗口，内置系统 Dock 栏图标与响应式界面，无需浏览器交互。
- **交互式 K 线图表**：集成 TradingView 高性能 K 线图，支持实时缩放拖拽与多指标动态叠加（MA5/10/30、EMA55、布林带）。
- **可视化配置管理**：内置配置面板，可直接在 GUI 界面中动态配置 OpenAI / DeepSeek / Gemini API Key、自定义 Base URL、模型名称及推送渠道 Token，即时生效。
- **多周期共振诊断**：覆盖 月线(1M)、周线(1W)、日线(1D)、4小时(4h)、1小时(1h) 多维周期，智能判断关键防守位与支撑阻力。
- **多渠道消息推送**：行情诊断完成后，自动将防守思路与分析报告推送至 Telegram 机器人、Server酱或 Bark。

---

## 📂 项目结构

```text
FeiyangAgent/
├── backend/                  # Python 后端核心逻辑
│   ├── app.py                # FastAPI 服务端（API 接口、K线数据、设置管理）
│   ├── data_fetcher.py       # CCXT 交易所数据拉取与多周期处理
│   ├── indicators.py         # 技术指标与 Fibonacci 极值点位计算
│   ├── agent.py              # LLM Prompt 构造、推理查询与交易逻辑校验
│   └── notifier.py           # Telegram / Server酱 / Bark 消息推送
├── frontend/                 # 桌面客户端前端 (Vite + React)
│   ├── dist/                 # 编译打包后的前端静态资源
│   ├── src/
│   │   ├── components/
│   │   │   ├── KLineChart.jsx    # TradingView 图表组件
│   │   │   └── SettingsPanel.jsx # 密钥与参数配置面板
│   │   ├── App.jsx           # 主界面布局与诊断卡片渲染
│   │   └── index.css         # 暗黑高质感 CSS 样式系统
│   ├── package.json
│   └── vite.config.js
├── main.py                   # 统一启动入口（支持 CLI、Daemon、--gui 模式）
├── build_app.py              # PyInstaller 一键编译与 macOS .app 打包脚本
├── requirements.txt          # Python 依赖清单
├── config.yaml.example       # 参数配置模版文件
├── config.yaml               # 本地持久化参数配置（已在 .gitignore 排除敏感信息）
├── FeiyangAgent.spec         # PyInstaller 打包配置文件
└── README.md                 # 项目使用说明
```

---

## 🛠️ 环境要求

- **操作系统**：macOS (x86_64 / Apple Silicon)
- **Python**：3.9+
- **Node.js**：18+（仅在修改前端或重新构建前端时需要）

---

## 🚀 快速开始

### 1. 克隆仓库与准备配置

```bash
git clone https://github.com/YourUsername/FeiyangAgent.git
cd FeiyangAgent

# 从模板创建本地配置文件
cp config.yaml.example config.yaml
```

### 2. 创建并激活 Python 虚拟环境

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
pip install pandas-ta --no-deps
```

### 4. 运行应用

#### 选项 A：启动 GUI 桌面应用（推荐）

```bash
python main.py --gui
```
运行后将弹出一体化原生桌面窗口，您可在“设置”面板中填入您的 LLM API Key 并开始行情诊断。

#### 选项 B：CLI 命令行调试运行（Dry-Run）

如果您仅想在终端测试数据获取、指标计算与 Payload 压缩：

```bash
python main.py --dry-run
```

---

## ⚙️ 配置文件说明 (`config.yaml`)

编辑 `config.yaml` 或直接在客户端界面中设置参数：

```yaml
# 交易对与交易所配置
symbol: "BTC/USDT"
exchange: "binance"

# 多周期分析范围
timeframes:
  - "1M"
  - "1W"
  - "1D"
  - "4h"
  - "1h"

# 极值回溯参数
fibonacci:
  lookback_days: 100

# 大模型参数
llm:
  model: "gpt-4o"     # 支持 gpt-4o, deepseek-chat, gemini-1.5-pro 等
  temperature: 0.1
  max_tokens: 3000

# 消息推送
notifications:
  enabled: true
  channels:
    - "telegram"      # 支持 telegram, serverchan, bark
  telegram:
    chat_id: "YOUR_CHAT_ID"
```

> **提示**：API Key 等敏感秘钥建议存入同目录下的 `.env` 文件中或直接在应用设置面板中填写，系统会自动写入本地加密环境。

---

## 📦 打包为 macOS 独立应用 (`.app`)

若需将项目编译为无需终端、直接双击运行的 `.app` 应用程序：

1. **构建前端静态资源**：
   ```bash
   cd frontend
   npm install
   npm run build
   cd ..
   ```

2. **执行打包脚本**：
   ```bash
   python build_app.py
   ```

3. 打包完成后，新生成的应用位于：
   `dist/FeiyangAgent.app`

4. 将 `FeiyangAgent.app` 拖入系统的 `Applications`（应用程序）文件夹即可像普通 Mac 软件一样直接双击运行！

---

## 📄 开源协议

本项目基于 [MIT License](LICENSE) 开源协议。

---

## ⚠️ 免责声明

本系统输出的所有诊断报告与交易信号仅供技术研究与参考，**不构成任何投资建议或交易依据**。市场有风险，投资需谨慎。
