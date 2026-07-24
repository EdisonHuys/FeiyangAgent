# 飞扬流派：多周期市场预测 Desktop GUI 智能体

[![Python Version](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-macOS-lightgrey.svg)](https://apple.com)

**FeiyangAgent** 是一个基于加密货币市场数据的自动化智能体（Agent）预测与交易诊断系统。

系统能够自动拉取币安等交易所多维度时间周期的数据，在本地实时计算技术指标（MA、EMA、Bollinger Bands）与斐波那契结构位，并将经过清洗压缩后的轻量级结构化载荷（JSON Payload）提供给大语言模型（LLM）。LLM 严格遵循分析师“飞扬”防守型右侧交易逻辑，输出结构化交易信号与诊断报告，同时配合内置的 **🎯 AI 智能狙击交易引擎** 自动执行模拟盘/实盘交易，并推送至 Telegram、Server酱或 Bark 等通知渠道。

为了提升交互体验，系统提供基于 `pywebview` + `React` + `TradingView` 打造的原生 macOS 桌面客户端。

---

## 🎨 核心特性

- **桌面客户端（Native GUI）**：采用 `pywebview` 封装原生的 macOS Cocoa 窗口，内置系统 Dock 栏图标与响应式界面，无需浏览器交互。
- **交互式 K 线图表**：集成 TradingView 高性能 K 线图，支持实时缩放拖拽与多指标动态叠加（MA5/10/30、EMA55、布林带）。
- **⏱️ 15分钟敏捷 AI 诊断轮询**：
  - 支持在 UI 界面与 `config.yaml` 中灵活配置后台 AI 诊断频率（5分钟、10分钟、15分钟黄金波段、30分钟、60分钟）。
  - 配合 10 秒级高频价格/止盈止损实时监控，兼顾插针吃单敏捷度与大模型研判质量。
- **🎯 智能狙击控制台 (Sniper Engine)**：
  - **模拟盘与实盘双模式**：支持模拟盘仿真推演及 CCXT 实盘合约（Binance / OKX / Bybit）真实下单。
  - **即时吃单与智能反向平仓**：信号触发时若币价已落入埋伏区间，瞬间执行市价吃单成单；若已有反向持仓，自动执行市价平仓并启动新方向建仓。
  - **自选币种自动撤单防护**：自选列表中删除币种时，系统自动撤销该币种挂单区内所有未成交的旧埋伏单。
  - **智能风控与动态杠杆**：依据 LLM 诊断置信度（Confidence Score）智能匹配 35x~70x 杠杆，自动管理仓位价值与保证金比例。
  - **10U 微型资金适配 (10U Micro-Capital Auto-Protector)**：专门针对 $10~$20U 小资金账户，自动优化调整名义价值，突破交易所最小交易限制。
  - **双保险自动风控与推损保本**：达到 TP1 自动平仓 50% 锁定收益，并**自动上移防守止损线至建仓成本价**（锁定无风险持仓）；触及止损自动触发市价平仓双保险。
- **可视化配置管理**：内置配置面板，可直接在 GUI 界面中动态配置 OpenAI / DeepSeek / Gemini API Key、自定义 Base URL、扫描频次、实盘 API Key / Secret 及推送策略，即时生效。
- **多周期共振诊断**：覆盖 月线(1M)、周线(1W)、日线(1D)、4小时(4h)、1小时(1h) 多维周期，智能判断低多/高空关键防守位与支撑阻力。
- **📈 历史回测实验室 (Walk-Forward Backtester)**：用真实历史 K 线逐根回放完整生产链路（指标 → LLM 诊断 → 狙击引擎模拟成交），在不花一分钱本金的前提下验证策略期望值。回测与生产共用同一套限价成交、TP1 半仓保本、双保险止损、手续费/滑点与杠杆安全帽逻辑。支持 GUI 面板与 CLI 两种运行方式。
- **🛡️ 机构级风控体系**：
  - **杠杆安全帽**：按止损距离自动降级杠杆，保证止损永远先于交易所强平触发，风控预算真实有效。
  - **日内回撤熔断**：当日实现亏损超过阈值（默认 6%，可配置）自动停止开新单并撤销全部挂单，次日复位。
  - **挂单过期机制**：超过有效期（默认 24h）未成交的挂单自动撤销。
  - **全成本建模**：手续费、滑点、资金费（8 小时 Funding）全部计入 PnL 与胜率统计。
  - **实盘交易所侧保护单**：实盘成交后自动补挂 reduceOnly 止损单，App 崩溃/断网也有保护。
- **🔔 消息推送分层控制**：支持独立开启/关闭 **交易履约推送**（建仓成单、TP1止盈推保本、TP2平仓、SL止损）与 **诊断信号生成推送**，拒绝频繁信息骚扰。

---

## 📂 项目结构

```text
FeiyangAgent/
├── backend/                  # Python 后端核心逻辑
│   ├── app.py                # FastAPI 服务端（API 接口、K线数据、设置管理、盯盘服务）
│   ├── data_fetcher.py       # CCXT 交易所数据拉取与多周期处理
│   ├── indicators.py         # 技术指标与 Fibonacci 极值点位计算
│   ├── agent.py              # LLM Prompt 构造、双向低多高空推理与逻辑校验
│   ├── sniper_engine.py      # 🎯 AI 智能狙击交易引擎（模拟盘/实盘、反向平仓、即时吃单）
│   ├── backtest.py           # 📈 Walk-Forward 历史回测引擎（复用生产级狙击逻辑）
│   ├── trades.json           # 狙击交易记录与持仓状态持久化文件
│   └── notifier.py           # Telegram / Server酱 / Bark 消息推送
├── frontend/                 # 桌面客户端前端 (Vite + React)
│   ├── dist/                 # 编译打包后的前端静态资源
│   ├── src/
│   │   ├── components/
│   │   │   ├── KLineChart.jsx      # TradingView 图表组件
│   │   │   ├── SettingsPanel.jsx   # 密钥与扫描频次/推送策略配置面板
│   │   │   ├── BacktestPanel.jsx   # 📈 历史回测实验室面板
│   │   │   └── SniperDashboard.jsx # 🎯 智能狙击交易控制台面板
│   │   ├── App.jsx             # 主界面布局、盯盘日志与诊断卡片渲染
│   │   └── index.css           # 暗黑高质感 CSS 样式系统
│   ├── package.json
│   └── vite.config.js
├── assets/                   # 应用图标与静态资源 (app_icon.icns等)
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
运行后将弹出一体化原生桌面窗口，您可在“核心配置参数”面板中填入您的 LLM API Key 并开始行情诊断与狙击交易。

#### 选项 B：CLI 命令行调试运行（Dry-Run）

如果您仅想在终端测试数据获取、指标计算与 Payload 压缩：

```bash
python main.py --dry-run
```

#### 选项 C：CLI 历史回测（消耗真实 LLM 额度）

```bash
python main.py --backtest --symbol BTC/USDT --bt-days 14 --bt-step 4 --bt-calls 60
```

参数说明：`--bt-days` 回测天数（≤90）、`--bt-step` 每隔多少小时做一次 LLM 诊断（步长越大越省额度）、`--bt-calls` LLM 调用预算上限。也可以直接在 GUI 的「📈 历史回测」标签页中图形化运行并查看权益曲线。

---

## ⚙️ 配置文件说明 (`config.yaml`)

编辑 `config.yaml` 或直接在客户端界面中设置参数：

```yaml
# 交易对与交易所配置
symbol: "BTC/USDT"
exchange: "binance"

# 自选监控交易对列表
symbols:
  - "BTC/USDT"
  - "ETH/USDT"
  - "SOL/USDT"
  - "BNB/USDT"
  - "ZEC/USDT"

# 后台 AI 自动诊断扫描频率 (分钟)
scan_interval_minutes: 15

# 多周期分析范围
timeframes:
  - "1M"
  - "1W"
  - "1D"
  - "4h"
  - "1h"

# 极值回溯参数 (天数)
fibonacci:
  lookback_days: 14

# 大模型参数
llm:
  model: "gpt-4o"     # 支持 gpt-4o, deepseek-chat, gemini-1.5-pro 等
  temperature: 0.1
  max_tokens: 3000

# 消息推送策略配置
notifications:
  enabled: true       # 全局推送总开关
  notify_on_signal: false  # 开单诊断信号推送开关 (建议关闭以防频繁骚扰)
  notify_on_trade: true   # 开仓/平仓/止盈止损履约推送开关 (默认开启)
  channels:
    - "telegram"      # 支持 telegram, serverchan, bark
  telegram:
    chat_id: "YOUR_CHAT_ID"
```

> **提示**：API Key 等敏感秘钥建议在应用“核心配置参数”面板或“智能狙击控制台”面板中直接填写，系统会自动写入本地持久化文件中。

---

## 🎯 智能狙击引擎与实盘配置

系统内置的狙击交易引擎支持两种模式：

1. **模拟盘模式 (Paper Trading)**：使用初始虚拟资金（如 $10,000 USD）进行无风险实战推演，自动记录胜率、盈亏比与回撤曲线。支持一键重置资金且精准同步日内基准。
2. **实盘合约模式 (Live Contract)**：在狙击控制台配置 Binance / OKX / Bybit 的 API Key & Secret 即可开启动态杠杆合约自动交易。系统包含双保险紧急市价平仓机制与 10U 小资金微型仓位适配保障。

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

本项目输出的所有诊断报告与交易信号仅供技术研究与参考，**不构成任何投资建议或交易依据**。实盘交易存在极高风险，请严格控制资金风控。
