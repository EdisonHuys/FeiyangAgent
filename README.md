# 飞扬流派：多周期市场预测 Desktop GUI 智能体

[![Python Version](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows-lightgrey.svg)]()

**FeiyangAgent** 是一个基于加密货币市场数据的自动化智能体（Agent）预测与交易诊断系统。

系统能够自动拉取币安等交易所多维度时间周期的数据，在本地实时计算技术指标（MA、EMA、Bollinger Bands、ADX、OBV、VWAP）与斐波那契结构位，结合 **市场情绪面分析**（恐惧贪婪指数、资金费率、宏观经济日历、加密新闻），将经过清洗压缩后的轻量级结构化载荷（JSON Payload）提供给大语言模型（LLM）。LLM 严格遵循 8 步链式推理（CoT）分析师"飞扬"防守型右侧交易逻辑，经过 **双次共识验证** 后输出结构化交易信号与诊断报告，同时配合内置的 **🎯 AI 智能狙击交易引擎** 自动执行模拟盘/实盘交易，并推送至 Telegram、Server酱或 Bark 等通知渠道。

为了提升交互体验，系统提供基于 `pywebview` + `React` + `TradingView` 打造的原生跨平台桌面客户端（macOS / Windows）。

---

## 🎨 核心特性

- **跨平台桌面客户端（Native GUI）**：采用 `pywebview` 封装原生窗口（macOS Cocoa / Windows EdgeChromium WebView2），内置系统 Dock 栏/任务栏图标与响应式界面，无需浏览器交互。
- **交互式 K 线图表**：集成 TradingView 高性能 K 线图，支持实时缩放拖拽与多指标动态叠加（MA5/10/30、EMA21/55、布林带、ADX、OBV、VWAP）。
- **🧠 8 步链式推理（CoT）诊断引擎**：
  - Step 0: 宏观环境扫描（恐惧贪婪指数 + 资金费率 + 经济日历）
  - Step 1: 市场结构判定（趋势/震荡/突破）
  - Step 2: 方向确认（多周期共振）
  - Step 3: 防追单检测（RSI 超买超卖 + 偏离度）
  - Step 4: 汇合因子确认（≥3 个独立因子）
  - Step 5: 动量/量价验证
  - Step 6: 风险回报比计算（R:R ≥ 2.0 硬性门槛）
  - Step 7: 综合评分与信号输出
- **🤝 双次 LLM 共识机制**：同一 payload 调用 LLM 两次，仅当方向一致时才输出交易信号，方向不一致则强制 wait，大幅降低幻觉信号。
- **📰 市场情绪面分析（Sentiment Engine）**：
  - Fear & Greed Index（alternative.me 实时数据）
  - 资金费率监控（ccxt 实时抓取，极端费率预警）
  - 宏观经济日历（FOMC/CPI/NFP/PPI  proximity 检测，事件前自动收紧风控）
  - 加密新闻头条（CryptoPanic + CryptoCompare 双源聚合）
  - 综合风险评估 → 自动输出 trading_bias（aggressive/normal/cautious/stand_aside）
- **⏱️ 动态自适应 AI 诊断轮询**：
  - 基于 ATR 波动率自动调频：高波动（ATR > 1.8x 均值）→ 5 分钟，低波动（ATR < 0.6x）→ 30 分钟，正常 → 配置值（默认 15 分钟）。
  - 配合 10 秒级高频价格/止盈止损实时监控，兼顾插针吃单敏捷度与大模型研判质量。
- **🎯 智能狙击控制台 (Sniper Engine)**：
  - **模拟盘与实盘双模式**：支持模拟盘仿真推演及 CCXT 实盘合约（Binance / OKX / Bybit）真实下单。
  - **即时吃单与智能反向平仓**：信号触发时若币价已落入埋伏区间，瞬间执行市价吃单成单；若已有反向持仓，自动执行市价平仓并启动新方向建仓。
  - **阶梯挂单**：3 档分布建仓（40% @ entry_min + 35% @ midpoint + 25% @ entry_max），加权平均成本优化成交均价。
  - **自选币种自动撤单防护**：自选列表中删除币种时，系统自动撤销该币种挂单区内所有未成交的旧埋伏单。
  - **智能风控与动态杠杆**：依据 LLM 诊断置信度（Confidence Score）智能匹配 20x~50x 杠杆，自动管理仓位价值与保证金比例（上限 25%）。
  - **胜率自适应仓位管理**：追踪近 10 单胜率，动态调整风险乘数（≥60% → 1x，40-60% → 0.8x，25-40% → 0.5x，<25% → 0.3x）。
  - **连亏冷却机制**：2 连 SL → 仓位 ×0.7，3 连 → ×0.5，4+ 连 → ×0.3，任何 TP 重置冷却。
  - **10U 微型资金适配 (10U Micro-Capital Auto-Protector)**：专门针对 $10~$20U 小资金账户，自动优化调整名义价值，突破交易所最小交易限制。
  - **双保险自动风控与推损保本**：达到 TP1 自动平仓 50% 锁定收益，并**自动上移防守止损线至建仓成本价**（锁定无风险持仓）；触及止损自动触发市价平仓双保险。
  - **防针扫机制**：止损触发加入 0.15% wick 容差，避免插针误触发；追踪止盈激活阈值 12%，锁定比 55%。
  - **时间止损**：持仓超过 72 小时未触及 TP1 自动平仓，避免资金长期被占用。
  - **相关性仓位管控**：BTC/ETH 同组、DOGE/HYPE 同组，同组同方向禁止叠加仓位。
  - **低流动性时段过滤**：UTC 20:00-23:00（亚洲死寂区）自动暂停开新仓。
  - **成交量异动检测**：4H 成交量 > 2.5x 20 期均量触发 spike 信号，附带方向注入 LLM payload。
- **可视化配置管理**：内置配置面板，可直接在 GUI 界面中动态配置 OpenAI / DeepSeek / GLM API Key、自定义 Base URL、扫描频次、实盘 API Key / Secret 及推送策略，即时生效。
- **多周期共振诊断**：覆盖 月线(1M)、周线(1W)、日线(1D)、4小时(4h)、1小时(1h) 多维周期，智能判断低多/高空关键防守位与支撑阻力。
- **📈 历史回测实验室 (Walk-Forward Backtester)**：用真实历史 K 线逐根回放完整生产链路（指标 → LLM 诊断 → 狙击引擎模拟成交），在不花一分钱本金的前提下验证策略期望值。回测与生产共用同一套限价成交、TP1 半仓保本、双保险止损、手续费/滑点与杠杆安全帽逻辑。支持 GUI 面板与 CLI 两种运行方式。
- **🛡️ 机构级风控体系**：
  - **杠杆安全帽**：按止损距离自动降级杠杆，保证止损永远先于交易所强平触发，风控预算真实有效。
  - **日内回撤熔断**：当日实现亏损超过阈值（默认 5%，可配置）自动停止开新单并撤销全部挂单，次日复位。
  - **挂单过期机制**：超过有效期（默认 24h）未成交的挂单自动撤销。
  - **全成本建模**：手续费、滑点、资金费（8 小时 Funding）全部计入 PnL 与胜率统计。
  - **实盘交易所侧保护单**：实盘成交后自动补挂 reduceOnly 止损单，App 崩溃/断网也有保护。
  - **7 条绝对禁令规则**：防追单、防逆势、汇合因子不足禁止开仓、R:R < 2.0 禁止、宏观事件窗口禁止、极端波动禁止、极端情绪禁止。
- **🔔 消息推送分层控制**：支持独立开启/关闭 **交易履约推送**（建仓成单、TP1止盈推保本、TP2平仓、SL止损）与 **诊断信号生成推送**，拒绝频繁信息骚扰。

---

## 📂 项目结构

```text
FeiyangAgent/
├── backend/                  # Python 后端核心逻辑
│   ├── app.py                # FastAPI 服务端（API 接口、K线数据、设置管理、盯盘服务）
│   ├── data_fetcher.py       # CCXT 交易所数据拉取与多周期处理
│   ├── indicators.py         # 技术指标（MA/EMA/BB/ADX/OBV/VWAP）、Fibonacci、市场结构检测
│   ├── agent.py              # LLM 8步CoT Prompt、双次共识、信号归一化与逻辑校验
│   ├── sentiment.py          # 📰 市场情绪面分析（恐惧贪婪/资金费率/宏观日历/新闻）
│   ├── sniper_engine.py      # 🎯 AI 智能狙击交易引擎（模拟盘/实盘、自适应风控）
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
├── assets/                   # 应用图标与静态资源 (app_icon.icns / icon.ico)
├── main.py                   # 统一启动入口（跨平台路径检测、CLI、--gui 模式）
├── build_app.py              # 跨平台打包脚本（macOS .app / Windows .exe / Linux）
├── requirements.txt          # Python 依赖清单
├── config.yaml.example       # 参数配置模版文件
├── config.yaml               # 本地持久化参数配置（已在 .gitignore 排除敏感信息）
└── README.md                 # 项目使用说明
```

---

## 🛠️ 环境要求

- **操作系统**：macOS (x86_64 / Apple Silicon) 或 Windows 10/11 (x64)
- **Python**：3.9+
- **Node.js**：18+（仅在修改前端或重新构建前端时需要）
- **Windows 额外要求**：Microsoft Edge WebView2 Runtime（Win10/11 通常已预装）

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
source venv/bin/activate        # macOS/Linux
# venv\Scripts\activate         # Windows
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
运行后将弹出一体化原生桌面窗口，您可在"核心配置参数"面板中填入您的 LLM API Key 并开始行情诊断与狙击交易。

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
  - "ZEC/USDT"
  - "DOGE/USDT"
  - "ZAMA/USDT"
  - "HYPE/USDT"

# 后台 AI 自动诊断扫描频率 (分钟，实际会根据ATR波动率动态调整)
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
  lookback_days: 100

# 大模型参数
llm:
  model: "glm-5.2"          # 推荐 GLM-5.2 或 deepseek-v4f
  temperature: 0.1
  max_tokens: 4096
  consensus_enabled: true    # 双次共识验证（强烈建议开启）

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

> **提示**：API Key 等敏感秘钥建议在应用"核心配置参数"面板或"智能狙击控制台"面板中直接填写，系统会自动写入本地持久化文件中。

---

## 🎯 智能狙击引擎与实盘配置

系统内置的狙击交易引擎支持两种模式：

1. **模拟盘模式 (Paper Trading)**：使用初始虚拟资金（如 $10,000 USD）进行无风险实战推演，自动记录胜率、盈亏比与回撤曲线。支持一键重置资金且精准同步日内基准。
2. **实盘合约模式 (Live Contract)**：在狙击控制台配置 Binance / OKX / Bybit 的 API Key & Secret 即可开启动态杠杆合约自动交易。系统包含双保险紧急市价平仓机制与 10U 小资金微型仓位适配保障。

### 风控参数一览

| 参数 | 默认值 | 说明 |
|------|--------|------|
| 杠杆范围 | 20x ~ 50x | 按止损距离自动降级 |
| 保证金上限 | 25% | 单笔最大占用余额比例 |
| 日内回撤熔断 | 5% | 触发后停止开新单至次日 |
| R:R 门槛 | ≥ 2.0 | 低于此值强制 wait |
| 防针扫容差 | 0.15% | SL 触发 wick 容差 |
| 时间止损 | 72h | 未触 TP1 超时自动平仓 |
| 追踪止盈激活 | 12% | 浮盈达此比例启动追踪 |
| 追踪锁定比 | 55% | 回撤此比例触发止盈 |
| 低流动性时段 | UTC 20-23 | 暂停开新仓 |

---

## 📦 打包为桌面应用

### macOS (.app)

```bash
# 方式一：自动检测平台
python build_app.py

# 方式二：指定 macOS
python build_app.py --macos

# 跳过前端构建（已有 frontend/dist 时加速）
python build_app.py --macos --skip-frontend
```

打包完成后，应用位于 `dist/FeiyangAgent.app`，拖入 Applications 文件夹即可使用。

### Windows (.exe)

```bash
# 在 Windows 机器上执行
python build_app.py --win
```

打包完成后，应用位于 `dist/FeiyangAgent/FeiyangAgent.exe`，将整个 `dist/FeiyangAgent/` 文件夹打包为 zip 分发即可。

> **注意**：Windows 需要 Microsoft Edge WebView2 Runtime。Windows 10/11 通常已预装。如缺失，请从 https://developer.microsoft.com/en-us/microsoft-edge/webview2/ 下载安装。

### 手动构建前端（如需）

```bash
cd frontend
npm install
npm run build
cd ..
```

---

## 📄 开源协议

本项目基于 [MIT License](LICENSE) 开源协议。

---

## ⚠️ 免责声明

本项目输出的所有诊断报告与交易信号仅供技术研究与参考，**不构成任何投资建议或交易依据**。实盘交易存在极高风险，请严格控制资金风控。
