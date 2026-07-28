# FeiyangAgent 完整业务逻辑文档

> 生成时间: 2026-07-28 | 用于排查交易逻辑问题

---

## 一、系统架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│  Frontend (React + Vite)                                         │
│  - 交易诊断终端 / 智能狙击控制台 / 历史回测 / 策略Prompt / 配置  │
│  - 纯 HTTP 轮询，无 WebSocket                                    │
└────────────────────────────┬────────────────────────────────────┘
                             │ REST API
┌────────────────────────────▼────────────────────────────────────┐
│  Backend (FastAPI + uvicorn)                                     │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │  app.py      │  │  agent.py    │  │  sniper_engine.py     │  │
│  │  API路由     │  │  LLM分析     │  │  交易引擎(核心)       │  │
│  │  定时循环    │  │  共识机制    │  │  持仓监控/平仓        │  │
│  └──────┬───────┘  └──────┬───────┘  └───────────┬───────────┘  │
│         │                  │                      │              │
│  ┌──────▼──────────────────▼──────────────────────▼───────────┐  │
│  │  支撑模块: data_fetcher / indicators / sentiment / notifier │  │
│  └────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                             │ CCXT
┌────────────────────────────▼────────────────────────────────────┐
│  Exchange (Binance / OKX / Bybit) — 永续合约                     │
└─────────────────────────────────────────────────────────────────┘
```

**两个后台线程:**
- `fast_price_check_loop`: 每 **10秒** 拉取价格 → 触发引擎监控（成交/止损/止盈/挂单管理）
- `hourly_llm_monitor_loop`: 每 **15分钟**（动态调整5-30分钟）→ LLM诊断 → 产生信号 → 下单

---

## 二、数据采集层

### 2.1 K线数据 (`data_fetcher.py`)

- 多交易所容错: binance → okx → bybit → gate
- 代理自动检测: 探测本地 VPN 端口 [7890, 7897, 1087, 1080, 7891]
- `fetch_ohlcv(symbol, timeframe, limit)`: 返回 DataFrame [timestamp, open, high, low, close, volume]
- `fetch_all_timeframes`: ThreadPoolExecutor 并发拉取所有周期
- `fetch_latest_prices(symbols)`: 批量 fetch_tickers，失败回退到 1h K线最后收盘价
- 进程级单例缓存，避免重复实例化

### 2.2 技术指标 (`indicators.py`)

`calculate_indicators(df)` 计算:
- MA5, MA10, MA30
- EMA55 (核心趋势过滤), EMA21
- 布林带 (20, 2)
- RSI(14), ATR(14), ADX(14)
- KDJ(9,3,3)
- MACD(12,26,9): DIF, DEA, Hist
- OBV + OBV_EMA20
- VWAP (24bar滚动)

`detect_market_regime(df)`: 分类为 trending_up / trending_down / ranging / volatile

`calculate_support_resistance(df, lookback=50)`: 3-bar确认的摆动高低点，返回前3个阻力/支撑

`calculate_fibonacci_levels(df_1d, lookback=100)`: 日线斐波那契 (0.382, 0.5, 0.618, 0.786, 1.272, 1.618, 2.618)

`calculate_4h_fibonacci(df_4h, lookback=120)`: 4H斐波那契 (0.236, 0.382, 0.5, 0.618, 0.786)

`compute_key_levels_context`: 聚合所有斐波那契+摆动S/R，过滤±8%范围内，按距离排序取前12个

### 2.3 市场情绪 (`sentiment.py`)

- **恐惧贪婪指数**: api.alternative.me，含昨日对比和趋势方向
- **资金费率**: ccxt fetch_funding_rate，正值=多头拥挤
- **宏观事件**: 硬编码2026年 FOMC/CPI/NFP/PPI 日期，检查12h窗口
- **加密新闻**: CoinTelegraph + CoinDesk RSS
- **风险评分**: 综合以上四项 → risk_level (low/normal/elevated/extreme) + trading_bias (aggressive/normal/cautious/stand_aside)
- 5分钟缓存TTL

---

## 三、LLM 分析层 (`agent.py`)

### 3.1 系统提示词结构

评分标准 (满分12分):
| 维度 | +2 | +1 |
|------|----|----|
| 大周期方向 | 1W+1D同向 | 仅1D明确 |
| 关键位共振 | ≥2个 | 1个 |
| 动能确认 | ≥2项 | 1项 |
| 风险回报比 | R:R≥2.0 | R:R≥1.5 |
| 顺势适配 | — | +1 |
| 量价配合 | — | +1 |
| 结构清晰度 | — | +1 (误差<0.5ATR) |
| 震荡区间加分 | — | +1 (ADX<20+BB+KDJ) |

**硬性过滤 (任一不通过强制wait):**
- R:R < 1.5
- F&G ≥ 90 禁追多 / ≤ 10 禁追空
- 宏观事件 < 6h
- 4H ATR > 2.5倍均值
- 4H MA5乖离率 > ±3%

### 3.2 输出格式

LLM必须输出:
1. JSON信号块 (```json ... ```): symbol, signal_type, confidence_score, entry_zone, stop_loss, take_profit_targets, risk_reward_ratio, confluence_factors, core_reason
2. Markdown报告: 盘面诊断 → 交易计划 → 方向判定逻辑 → 评分明细表(含合计行) → 过滤项检查

### 3.3 解析流程 (`_parse_response`)

1. 正则提取 ```json``` 代码块 → json.loads (含JS注释清理+尾逗号修复)
2. 失败则用首尾花括号兜底
3. `_normalize_signal`: 类型规范化 + **min_confidence硬门槛** (分数不够强制wait)
4. **ScoreSync**: 用正则从报告表格中提取"合计 X/12"作为真实分数，覆盖JSON中的分数
5. **ConsistencyFix**: 如果signal=wait但分数够且几何有效 → 强制恢复为long/short
6. `_validate_signal`: 仅日志警告，不修改

### 3.4 双次共识机制 (`analyze_with_consensus`)

```
第一次调用 (temperature=0.1)
    │
    ├── 结果=wait → 直接返回，跳过第二次 (省token)
    │
    ▼
第二次调用 (temperature=0.2)
    │
    ├── 方向一致 → 取高分报告, confidence = (conf1+conf2)//2, 同步修改报告合计行
    │
    ├── 方向不一致但max≥7 → 取高分方, confidence = max-1 (罚1分), 同步修改合计行
    │
    └── 方向不一致且都<7 → 强制wait, confidence=0
```

---

## 四、信号分发 (`app.py` → `process_signal_evaluation`)

### 4.1 15分钟定时诊断循环

```python
每轮:
  1. 加载YAML配置 + .env
  2. 门控: (通知开启 OR 狙击开启) AND api_key存在
  3. 获取全局market_context (一次，所有币共享)
  4. ThreadPoolExecutor(max_workers=8) 并发诊断所有symbols
  5. 动态间隔: ATR_ratio>1.8→5min, <0.6→30min, 否则→15min
```

### 4.2 信号评估与推送

```python
process_signal_evaluation(symbol, current_price, json_signal, report, source_tag):
  1. 保存报告到 last_reports.json
  2. 更新引擎价格 (触发一次监控tick)
  3. 解析信号字段
  4. 对比上次信号状态 (last_signals.json):
     - 全新方向 → 推送
     - SL/TP被突破 → 推送重置
     - 价格首次进入吃单区间 → 推送
     - 吃单区间偏移>1% → 推送
  5. 调用 sniper_engine.process_new_signal() → 实际下单
  6. 推送通知 (如果should_push且通知开启)
  7. 持久化信号状态
```

---

## 五、交易引擎核心 (`sniper_engine.py`)

### 5.1 信号接收门控 (`process_new_signal`)

按顺序检查，任一不通过返回None:

1. **模式检查**: mode="off" → 拒绝
2. **熔断器**: 当日已实现亏损超过阈值(默认5%) → 拒绝
3. **低流动性时段**: UTC 20-23点 → 拒绝
4. **市场偏向**: trading_bias="stand_aside" → 拒绝
5. **相关性检查**: BTC/ETH互斥, DOGE/HYPE互斥 (同方向已有持仓则拒绝)
6. **信号类型+置信度**: 必须是long/short且 conf >= min_confidence
7. **最大持仓数**: 当前活跃单(pending+filled+tp1_hit) >= max_active_trades → 拒绝

### 5.2 已有持仓处理

| 已有状态 | 新信号方向 | 处理 |
|----------|-----------|------|
| pending | 任意 | 撤旧挂单(含交易所条件委托清理) → 允许新信号替换 |
| filled/tp1_hit | 同向 | 跳过(不重复开仓) |
| filled/tp1_hit | 反向 | **反转**: 市价平旧仓 → 计算PnL → 开新反向仓 |

**反转失败处理**: `_try_live_close`返回False → 中止反转，保留原仓位

### 5.3 信号几何验证

- Long: 必须 SL < entry_min ≤ entry_max < TP1
- Short: 必须 SL > entry_max ≥ entry_min > TP1
- 不满足 → 拒绝

### 5.4 仓位与保证金计算 (`calculate_trade_params`)

```
杠杆确定 (smart模式):
  conf≥9 → max_leverage
  conf≥8 → min + (max-min)*0.65
  conf≥7 → min + (max-min)*0.35
  conf<7 → min_leverage

每单资金占用模式 (margin_mode):
  1. 智能控仓 (smart, 默认):
     - conf_risk_mult = 0.8 + (min(conf,10) - 7) * 0.133  (限制在 [0.6, 1.3])
     - risk_amount = balance × (risk_pct/100) × conf_risk_mult
     - sl_distance = |entry - SL| / entry (下限0.01)
     - pos_value = risk_amount / sl_distance
     - margin = pos_value / leverage
     - 若 margin > 25% balance → 截断至 25% balance
  2. 账户资金占比 (account_percent):
     - margin = balance × (margin_percent / 100)  (默认 5%)
     - pos_value = margin × leverage
  3. 固定保证金金额 (fixed_amount):
     - margin = fixed_margin_amount  (默认 20 USDT)
     - pos_value = margin × leverage

安全限制 (防交易所拒单):
  - pos_value < $21 → 自动放大到 $21 (通过交易所最小名义价值限制)
  - BTC: pos_value ≥ 0.0011 × price
  - ETH类: pos_value ≥ 0.011 × price
```

### 5.5 自适应风控

```
胜率调整:
  ≥60% → 100%基础风险
  40-60% → 80%
  25-40% → 50%
  <25% → 30%

连亏冷却:
  2连亏 → 0.7x
  3连亏 → 0.5x
  4+连亏 → 0.3x
  任何TP → 重置
```

### 5.6 挂单结构 (3档阶梯)

```
Tranche 1: 40% 仓位 @ entry_min (激进边缘)
Tranche 2: 35% 仓位 @ midpoint  (核心)
Tranche 3: 25% 仓位 @ entry_max (深度回调)

planned_entry = 加权平均 = 0.40×min + 0.35×mid + 0.25×max
```

### 5.7 即时成交 vs 挂单

- **即时成交条件**:
  - Long: 当前价 ≤ entry_max 且 > SL
  - Short: 当前价 ≥ entry_min 且 < SL
- 即时成交: status="filled", entry=当前价, 收taker费
- 否则: status="pending", entry=planned_entry, 挂限价单

### 5.8 实盘下单

```python
1. set_leverage (失败仅警告)
2. 计算amount = pos_val / planned_entry
3. 强制 ≥ 交易所最小量
4. amount_to_precision + price_to_precision
5. 🧹 防御性清理: 撤销该币所有遗留条件委托
6. 下单参数:
   - Binance: positionSide + stopLoss(仅SL，不挂TP!)
   - OKX: positionSide + slTriggerPrice
   - Bybit: positionSide + stopLoss
7. 主尝试(对冲模式) → 失败回退(单向模式)
8. 全部失败 → 标记cancelled + 抛异常
```

**重要**: 不附带takeProfit参数，因为附带TP会在交易所侧全平仓位，覆盖本地50%部分平仓策略。

---

## 六、持仓监控 (每10秒tick)

### 6.1 交易所仓位同步 (实盘)

- 60秒缓存TTL
- 从交易所拉取实际仓位覆盖本地数据: 杠杆、保证金、名义价值、开仓价、PnL、标记价格
- 开仓价变化>0.2% → 重置追踪止损状态
- **自愈**: 本地标记closed但交易所仍有仓位 → 恢复为filled
- **外部平仓检测**: 本地active但交易所无仓位 → 标记closed
- **外部仓位**: 交易所有但本地无 → 显示为"external"(不管理)

### 6.2 挂单管理 (status=pending)

| 检查 | 条件 | 动作 |
|------|------|------|
| TTL过期 | 挂单超过24h | 撤单+清理条件委托 |
| SL失效 | 价格穿越SL | 撤单(结构破坏) |
| 限价成交(Long) | K线低点 ≤ planned_entry | 标记filled, 收maker费 |
| 限价成交(Short) | K线高点 ≥ planned_entry | 标记filled, 收maker费 |
| 交易所同步 | fetch_order="closed" | 标记filled, 挂保护止损 |
| 交易所同步 | fetch_order="canceled" | 标记cancelled, 清理 |

**注意**: 成交时检查max_active_trades，超限则继续排队

### 6.3 持仓退出逻辑 (status=filled/tp1_hit)

#### 时间止损
```
条件: status="filled" 且 tp1未触发 且 持仓>72h
动作: 以当前价市价平仓
实盘: _try_live_close必须成功才标记closed，失败下tick重试
```

#### 止损触发 (双保险)
```
触发条件 (任一):
  1. 价格触发: Long低点≤SL / Short高点≥SL
  2. PnL触发: 浮亏率 ≥ 80% (max_trade_loss_percent)

执行:
  1. _try_live_close → 失败则continue(下tick重试)
  2. 滑点模型: Long出场价×(1-0.05%), Short×(1+0.05%)
  3. 亏损 = margin × (exec_price-entry)/entry × leverage × rem_ratio - taker_fee
  4. status = "closed_sl"
```

#### TP1 部分平仓 (50%)
```
触发: Long高点≥TP1 / Short低点≤TP1 (且tp1_partial_closed=False)

执行:
  1. _try_live_close 平50% → 必须成功
  2. tp1_partial_closed = True
  3. status = "tp1_hit"
  4. 止损上移至保本: stop_loss = actual_entry
  5. 🛡️ 交易所侧重挂保护性止损 (closePosition=True)
  6. PnL = (TP1-entry)/entry × lev × 0.5 × margin - taker_fee
```

#### 终极止盈 (全平)
```
触发: Long高点≥max(TPs) / Short低点≤min(TPs)

执行:
  1. _try_live_close 平剩余(50%或100%)
  2. status = "closed_tp"
  3. PnL = (TP-entry)/entry × lev × rem_factor × margin - taker_fee
```

### 6.4 动态阶梯追踪止损 (`_update_trailing_stop_loss`)

```
参数:
  激活阈值: 浮盈 ≥ 12%
  最小峰值步进: 3% (防频繁更新)
  最大利润回退配置: max_profit_drawdown_percent (默认 30%)

分段动态锁定比例 (Tiered LOCK_RATIO):
  - Peak PnL < 30%: LOCK_RATIO = 50% (保护本金与微利)
  - 30% ≤ Peak PnL < 100%: LOCK_RATIO = 60%
  - Peak PnL ≥ 100% (爆发单): LOCK_RATIO = 100% - max_profit_drawdown_percent (默认 70%, 即顶住最大 30% 利润回吐)

计算:
  lock_in_pct = peak_pnl × LOCK_RATIO
  price_move = lock_in_pct / 100 / leverage
  Long: new_sl = entry × (1 + price_move)
  Short: new_sl = entry × (1 - price_move)

棘轮机制: 只向有利方向移动，永不恶化

交易所更新 (防止委托堆积):
  1. 检查 enable_exchange_sl 开关 (若关闭则仅依靠本地软件监控)
  2. 强制撤销该币种上一张记录的止损单及所有历史 STOP/trigger 条件委托
  3. 重挂新 STOP_MARKET (closePosition=True)
```

### 6.5 资金费模型 (模拟盘)

```
每8小时收取: funding_fee = pos_val × rem_ratio × 0.0001 × epochs
从pnl_usd和paper_account_balance中扣除
```

---

## 七、交易所交互

### 7.1 保护性止损 (`_place_live_protective_sl`)

```
目的: App崩溃/休眠/断网时交易所侧仍有保护

开关门控:
  - 读取 enable_exchange_sl 参数 (默认 True)。若关闭，跳过交易所侧挂单。

Binance 智能多模式兼容 (修复 -4061 错误):
  1. 尝试使用 positionSide: "LONG"/"SHORT" 下单 (适配双向持仓/对冲模式)
  2. 若遇到 -4061 错误 (position side mismatch)，自动降级重试 positionSide: "BOTH" (适配单向持仓模式)
  3. 若仍失败，自动降级去除 positionSide 参数尝试下单

其他交易所: triggerPrice + reduceOnly + 最小量校验

失败防线: 捕获异常并发送紧急推送通知用户手动设置
```

### 7.2 条件委托清理 (`_cancel_all_conditional_orders_for_symbol`)

```
识别条件:
  - type含STOP/TAKE_PROFIT/TRAILING
  - reduceOnly=true
  - stopPrice/triggerPrice非空非0
  - status含STOP/PENDING

调用时机:
  - 平仓成功后
  - 撤单后
  - 新下单前(防御性)
  - TP1部分平仓后(然后重挂)
```

### 7.3 市价平仓 (`_execute_live_market_close`)

```
1. amount_to_precision
2. 如果 < 最小量 → 从交易所拉取实际仓位量回退
3. 尝试1: 对冲模式 (positionSide + reduceOnly)
4. 尝试2: 单向模式 (仅reduceOnly)
5. 返回order_id或None
```

### 7.4 平仓失败处理 (`_try_live_close`)

```
成功:
  - 清除失败告警标记
  - 撤销保护性止损
  - 撤销所有条件委托
  - 返回True → 允许标记closed

失败:
  - 设置 live_fail_alerted_{tag} (每个trade+leg只告警一次)
  - 发送紧急推送
  - 返回False → 保持当前状态，下tick重试
```

---

## 八、熔断器

```
触发: 当日已实现亏损 ≥ day_start_balance × daily_max_loss_percent%
动作:
  - 撤销所有当前模式的pending挂单
  - 拒绝所有新信号
  - 发送通知(每模式每天一次)
重置: 每日午夜自动重置 / 手动reset-breaker
```

---

## 九、费用模型

| 事件 | 费率类型 | 计算基础 |
|------|---------|---------|
| 即时市价成交 | Taker 0.05% | 全额pos_val |
| 限价挂单成交 | Maker 0.02% | 全额pos_val |
| TP1部分平仓 | Taker | pos_val × 50% |
| 终极止盈 | Taker | pos_val × rem_factor |
| 止损触发 | Taker | pos_val × rem_ratio |
| 时间止损 | Taker | 全额pos_val |
| 手动平仓 | Taker | pos_val × rem_factor |
| 反转平仓 | Taker | pos_val × rem_ratio |

**滑点**: 仅在市价出场(SL/手动/时间止损/反转)时应用 0.05%

---

## 十、通知系统 (`notifier.py`)

- 支持: Telegram / Server酱(微信) / Bark(iOS)
- 每次通知同时写入本地 `latest_report.md`
- Telegram: 先尝试Markdown格式，失败回退纯文本
- 触发点: 建仓/成交/TP1/TP/SL/时间止损/反转/追踪止损/熔断/平仓失败

---

## 十一、前端轮询频率

| 数据 | 间隔 | 端点 |
|------|------|------|
| K线行情 | 60s | GET /api/market |
| 诊断报告 | 15s | GET /api/reports/latest |
| 运行日志 | 5s | GET /api/monitor-logs |
| 狙击面板+持仓 | 5s | GET /api/sniper/dashboard + trades |
| 情绪面板 | 60s | GET /api/sentiment |
| 手动诊断 | 2s轮询 | GET /api/analyze/status/{id} |

---

## 十二、状态持久化

- `trades.json`: 所有交易记录 + 配置 (原子写入: tmp→fsync→replace)
- `last_reports.json`: 每币最新诊断报告
- `last_signals.json`: 每币最新信号状态(用于推送去重)
- `config.yaml`: 系统配置(符号/交易所/LLM/通知)
- `.env`: 敏感密钥(API key, bot token)
- `feiyang_prompt.txt`: 自定义系统提示词(可选)

---

## 十三、关键安全机制

1. **原子状态持久化**: 防崩溃损坏 (tmp+fsync+replace)
2. **JSON损坏恢复**: 自动备份+重新初始化
3. **平仓失败重试**: 永不标记closed除非交易所确认
4. **自愈机制**: 交易所仍有仓位但本地closed → 恢复
5. **墓碑系统**: 手动平仓后90秒内不显示幻象仓位
6. **追踪止损棘轮与阶梯锁利**: 只向有利方向移动，浮盈≥100%时自动封锁 70% 最高收益（顶住最大 30% 利润回吐）
7. **开仓价变化检测**: 重置追踪止损避免陈旧计算
8. **最小名义价值保护**: 自动放大到$21避免交易所拒单
9. **双模式与 API 兼容下单**: 修复 Binance -4061 错误，实现对冲模式与单向持仓模式自动降级重试
10. **单次告警与挂单防堆积**: 每个失败 trade+leg 只推送一次告警；重挂止损前强制清空旧条件委托，保证交易所侧单币种永不堆积多余挂单

---

## 十四、完整交易生命周期

```
[15分钟诊断] → LLM分析 → 共识 → 信号(long/short/wait)
                                        │
                                        ▼
                              process_signal_evaluation
                                        │
                    ┌───────────────────┼───────────────────┐
                    ▼                   ▼                   ▼
              保存报告           推送通知            引擎下单
                                                        │
                    ┌───────────────────────────────────┤
                    ▼                                   ▼
              即时成交(market)                    挂单(limit/pending)
                    │                                   │
                    ▼                                   ▼ [10秒监控]
              status=filled                    价格触及→filled
                    │                          TTL过期→cancelled
                    │                          SL穿越→cancelled
                    ▼
        ┌─────[持仓监控]─────┐
        │                    │
        ▼                    ▼
   [追踪止损]          [退出触发]
   浮盈≥12%激活        │    │    │
   锁定55%峰值         ▼    ▼    ▼
        │            SL   TP1  时间止损
        │            触发  50%  >72h
        │             │    │    │
        │             ▼    ▼    ▼
        │          closed  tp1_hit  closed
        │          _sl     │       (市价)
        │                  ▼
        │            剩余50%继续
        │            SL移至保本
        │                  │
        │                  ▼
        │            终极TP触发
        │                  │
        │                  ▼
        │            closed_tp
        │
        └──→ 持续上移SL直到退出
```

---

## 十五、已知设计要点与注意事项

1. **不附带TP参数**: 初始订单只挂SL不挂TP，因为交易所侧TP会全平仓位，覆盖本地50%分批止盈策略
2. **closePosition=True**: Binance保护性止损使用此参数，不需要指定数量，避免最小量拒单
3. **consensus评分同步**: 共识层调整分数后用正则改写报告中的"合计"行，保持显示一致
4. **ScoreSync**: 报告表格中的"合计 X/12"是真实分数来源，覆盖JSON中的confidence_score
5. **min_confidence双重来源**: agent.py从trades.json读取(默认7)，sniper config也有同名字段
6. **动态扫描间隔**: 波动大→5分钟(捕捉机会)，波动小→30分钟(省token)
7. **相关性互斥**: BTC/ETH同组, DOGE/HYPE同组，同方向不重复开
8. **低流动性过滤**: UTC 20-23点(亚洲死寂时段)不开仓
