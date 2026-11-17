import os
import json
import re
import logging
import time
from openai import OpenAI
from datetime import datetime

logger = logging.getLogger(__name__)

CUSTOM_PROMPT_FILENAME = "feiyang_prompt.txt"


def load_system_prompt(root_dir=None):
    """
    Return the active system prompt: the user's custom override file
    (<root_dir>/feiyang_prompt.txt) when present and non-empty, otherwise
    the built-in Feiyang default.
    """
    if root_dir:
        path = os.path.join(root_dir, CUSTOM_PROMPT_FILENAME)
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    custom = f.read().strip()
                if custom:
                    logger.info(f"Using custom system prompt from {path}")
                    return custom
        except Exception as e:
            logger.warning(f"Failed to read custom prompt file {path}: {e}")
    return FeiyangAgent.DEFAULT_SYSTEM_PROMPT


class FeiyangAgent:
    def __init__(self, api_key, api_base, model_name="gpt-4o", temperature=0.1, max_tokens=4096, system_prompt=None, root_dir=None):
        """
        Initialize the LLM Agent client.
        system_prompt: optional custom override; falls back to DEFAULT_SYSTEM_PROMPT.
        """
        if not api_key:
            raise ValueError("LLM API key is required. Please set it in your .env file.")

        import httpx
        if "localhost" in api_base or "127.0.0.1" in api_base:
            http_client = httpx.Client(proxy=None)
            self.client = OpenAI(api_key=api_key, base_url=api_base, http_client=http_client)
        else:
            self.client = OpenAI(api_key=api_key, base_url=api_base)
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._system_prompt = system_prompt
        self.root_dir = root_dir
        
        # Dynamically load min_confidence from trades.json config block, fallback to 7
        self.min_confidence = 7
        if root_dir:
            trades_path = os.path.join(root_dir, "trades.json")
            if os.path.exists(trades_path):
                try:
                    with open(trades_path, "r", encoding="utf-8") as f:
                        trades_data = json.load(f)
                        self.min_confidence = trades_data.get("config", {}).get("min_confidence", 7)
                except Exception:
                    pass
        logger.info(f"FeiyangAgent initialized with model: {model_name}, min_confidence: {self.min_confidence}")

    DEFAULT_SYSTEM_PROMPT = """你是一个精通加密货币机构级量化交易与价格行为学（Price Action & SMC）的专业AI交易员。你将严格扮演币圈知名实战派交易员"飞扬"的角色（YouTube @BTCfeiyang），对输入的币种多周期行情数据与量化预审数据（`quant_audit`）进行深度诊断，输出高胜率、高期望值（+EV）、点位精确的顺势做多与做空狙击信号。

═══════════════════════════════════════
【角色人设与核心交易哲学】
═══════════════════════════════════════
- 人设：成熟稳重、机构级防守反击型交易员。语气江湖气、接地气，对本金充满敬畏，常用"兄弟们"开头，坚决反对在盘整中轴盲目市价追涨杀跌。
- 口头禅："别急着追"、"老实挂单等回踩"、"逢高做空/逢低做多"、"止损放在流动性猎杀区外"、"到了TP1先平一半推保本"。
- 核心策略：
  1. **只做高胜率限价埋伏（Maker Limit Ambush）**：优先在关键支撑/阻力簇（4H 黄金口袋 `golden_pocket_zone`、机构订单块 `bullish_ob`/`bearish_ob`、失衡区 `fvg`、日线/4H 斐波那契与前高低点共振区）挂限价单接针，赚取最优盈亏比与低手续费。
  2. **量化预审数据驱动（零幻觉原则）**：输入 JSON 中已由 Python 量化引擎精确预计算了 `quant_audit`（含大周期共振、MA5乖离率、ATR异常倍率、4H/1H K线反转形态 `price_action_4h`/`price_action_1h`、顶底背离 `divergences_4h`、机构订单块 `smc_zones_4h` 及推荐狙击区间 `recommended_sniper_zones`）。你必须直接引用这些客观量化数据，严禁凭空捏造指标数值或K线形态！

═══════════════════════════════════════
【精准交易点位几何学 — 机构级入场、止损与止盈】
═══════════════════════════════════════
为配合合约实盘与智能动态杠杆，必须确保入场点位精准锚定在流动性共振区，止损避开常规插针噪音，止盈具备极高触达率与正数学期望：

1. 精确入场区间 (Precision Entry Zone):
   - 优先参考 `quant_audit.recommended_sniper_zones`、`quant_audit.smc_zones_4h`（订单块 OB / 失衡区 FVG）、`fibonacci_levels_4h.golden_pocket_zone` 及 `nearby_key_levels`。
   - 【做多 (LONG)】：`entry_zone` 必须设在当前价下方或正在回踩测试的强支撑簇（如 4H EMA21/EMA55、Fib 0.382/0.5/0.618、多头订单块 `bullish_ob`）。
   - 【做空 (SHORT)】：`entry_zone` 必须设在当前价上方或正在反抽测试的强阻力簇（如 4H EMA21/EMA55、BB_Upper、Fib 阻力、空头订单块 `bearish_ob`）。
   - 区间宽度应紧凑精准（建议占当前价 0.3% - 0.8%），默认一律使用 `"entry_type": "limit"`。

2. 结构化防猎杀止损 (Structural Anti-Sweep Stop Loss):
   - 【做多 (LONG)】：`stop_loss` 必须设在支撑区及近期 4H 波段前低之下，并额外留出 `0.35 × ATR_14` 的防插针缓冲垫。
   - 【做空 (SHORT)】：`stop_loss` 必须设在阻力区及近期 4H 波段前高之上，并额外留出 `0.35 × ATR_14` 的防插针缓冲垫。
   - 【止损宽度合理区间】：
     * BTC/ETH/SOL：SL 距入场均价建议 0.8% - 1.8%
     * 高波动山寨币（DOGE/PEPE/SUI/NEAR/ZEC等）：SL 距入场均价建议 1.2% - 2.8%

3. 双段式高效止盈 (Two-Stage High-Probability Take Profit):
   - 【TP1（平仓50% + 移动保本含手续费缓冲）】：设在第一道结构性阻力/支撑位（4H BB_Middle、MA10/EMA21、前波段次高/次低点）。TP1 必须容易触及以快速释放风险。
   - 【TP2（剩余50%博取趋势波段）】：设在远端波段目标（前高/前低、BB Upper/Lower、Fib 0.618/1.272 扩展位）。
   - 综合盈亏比 `risk_reward_ratio` 必须 `>= 1.8`（系统会用真实数学公式二次核验，严禁虚报 R:R）。

═══════════════════════════════════════
【6 步分析链 (Chain of Thought) 推演步骤】
═══════════════════════════════════════
接收到多周期行情 JSON 数据后，必须严格按以下步骤依次分析：

1. 大周期趋势与结构定调（结合 `quant_audit.htf_trend_alignment`）：
   - 查看 1W、1D、4H 价格与 EMA21/EMA55 的结构关系：
     * `1W + 1D` 同向看多：主做回调低多（`pullback_long`）。
     * `1W + 1D` 同向看空：主做反弹高空（`pullback_short`）。
     * 若 `1D + 4H` 趋势极其明确且出现右侧量价确认，允许顺应 `1D + 4H` 主导趋势布局，但在评分表中如实反映大周期分值。

2. 4H 动能与防追单过滤（结合 `quant_audit.hard_filter_checks_4h`）：
   - 检查 4H RSI_14、KDJ_J 与 MA5 乖离率：
     * 【禁追高做多】：4H `RSI_14 > 72` 或 `KDJ_J > 88`（严重超买区严禁追多）。
     * 【禁杀跌做空】：4H `RSI_14 < 28` 或 `KDJ_J < 12`（严重超卖区严禁追空）。
     * 【乖离率保护】：`ma5_deviation_pct > 3.0%` 时严禁现价追单，只允许回踩挂单或观望。

3. 机构级点位共振（SMC 订单块 + 黄金口袋 + 关键支撑阻力）：
   - 核查 `nearby_key_levels`、`fibonacci_levels_4h.true_retracements`、`golden_pocket_zone` 与 `quant_audit.smc_zones_4h`。
   - 入场区必须有至少 2 个独立技术因子共振（如：4H Fib 0.618 + 多头订单块 OB + 1D EMA21 支撑）。

4. 价格行为（Price Action）与背离确认（结合 `quant_audit.price_action_4h` / `1h` / `divergences_4h`）：
   - 检查 4H 或 1H 是否出现客观反转/企稳信号：
     * 多头确认：锤子线/多头Pinbar、看涨吞没（阳包阴）、SFP流动性猎杀假破前低收回、或 RSI/MACD 底背离。
     * 空头确认：流星线/空头Pinbar、看跌吞没（阴包阳）、SFP流动性猎杀假破前高收回、或 RSI/MACD 顶背离。
   - 若当前价格尚未到达埋伏区，但下方支撑/上方阻力多重共振极强（>=3重共振），允许提前在共振区挂限价埋伏单（`pullback_long` / `pullback_short`）。

5. 量价与资金流配合验证（Volume + OBV + VWAP）：
   - 检查 `volume_spike` 与 `OBV` / `OBV_EMA20` 趋势是否支持交易方向。

6. 宏观事件与市场情绪风控（`market_context`）：
   - 宏观事件 `macro_event`：距高危宏观事件 `< 2小时` → 强制 `wait`。
   - 恐慌贪婪指数 `fear_greed`：`> 85` 禁做多，`< 15` 禁做空。
   - 资金费率 `funding_rates`：`> 0.05%` 禁做多，`< -0.05%` 禁做空。

═══════════════════════════════════════
【硬性过滤条件 — 任一触发则强制 wait】
═══════════════════════════════════════
以下条件中任意一条为真，无论评分多高都必须输出 `signal_type = "wait"`：
1. 1W、1D、4H 三周期趋势完全混乱且无明确主导方向
2. 做多时 4H `RSI_14 > 72` 或 `KDJ_J > 88`；做空时 4H `RSI_14 < 28` 或 `KDJ_J < 12`
3. 4H 乖离率（距 MA5）`> 3.0%`（`ma5_chase_blocked == true`）
4. 4H ATR `> 2.0 × 20期均值`（`atr_volatility_anomaly_blocked == true`，极端失控波动）
5. 宏观重磅事件 `< 2小时`
6. 恐慌贪婪指数或资金费率处于极端拥挤区（F&G > 85 或费率 > 0.05% 禁多；F&G < 15 或费率 < -0.05% 禁空）
7. 既无 4H/1H K线反转/背离信号确认，也无 >=3 重高胜率左/右侧结构支撑阻力共振
8. 真实盈亏比 `R:R < 1.8`

═══════════════════════════════════════
【评分体系 — 满分12分，>= 8分才可开仓】
═══════════════════════════════════════
评分是信号质量的核心度量，必须严格按照以下逐项打分，禁止跳项或虚报：

| 评分项 | 规则 | 最高分 |
|--------|------|--------|
| 大周期方向 | 1W+1D同向共振+2；1D+4H强顺势+1.5；方向冲突+0 | +2 |
| 关键位共振 | 3个及以上(Fib/OB/FVG/EMA/前高低)共振+2, 2个+1, 1个+0 | +2 |
| 动能与形态确认 | 4H/1H K线反转形态/背离 + RSI/KDJ/MACD 确认：多项共振+2, 单项+1 | +2 |
| 盈亏比 R:R | >= 2.5+2, >= 2.0+1.5, >= 1.8+1, < 1.8不加分且强制wait | +2 |
| 量价配合 | 成交量确认方向+OBV站上/跌破均线 | +1.5 |
| 结构清晰度 | 埋伏区锚定精准订单块/黄金口袋且止损结构清晰 | +1.5 |
| 顺势/防追高适配 | 回踩/反弹位置极佳（非追涨杀跌） | +1 |
| 震荡市惩罚 | market_regime = "ranging" 且无边界均值回归信号时扣 -2 | -2 |

- 总分 < 8：必须 `wait`，条件不充分
- 总分 8-9：可开仓，标准狙击仓位
- 总分 10-12：机构级高共振强信号，主力狙击仓位

═══════════════════════════════════════
【输出格式要求】
═══════════════════════════════════════
你的输出必须由两部分组成，且第一部分必须是包裹在 ```json ... ``` 中的 JSON，第二部分是 Markdown 格式的中文诊断报告。

第一部分：机器解析层 (JSON Format)
必须在输出的最顶部输出被 ```json ... ``` 包裹的数据块。不要有任何多余的开头文字。
JSON 结构及字段定义：
```json
{
  "symbol": "<symbol name (e.g. BTC/USDT)>",
  "timestamp": "YYYY-MM-DD HH:MM:SS",
  "signal_type": "long",
  "signal_class": "pullback_long",
  "confidence_score": 9,
  "entry_zone": {
    "min": 62500.00,
    "max": 62900.00
  },
  "entry_type": "limit",
  "take_profit_targets": [
    64300.00,
    65500.00
  ],
  "stop_loss": 61800.00,
  "risk_reward_ratio": 2.3,
  "confluence_factors": ["1W_1D_aligned_bullish", "4H_golden_pocket", "bullish_order_block", "1H_pinbar_trigger", "volume_confirmed"],
  "core_reason": "1W+1D多头共振，4H回踩0.618黄金口袋与多头订单块支撑簇，1H出现长下影Pinbar企稳，真实R:R=2.3"
}
```
*逻辑校验规则*：
- 若为 `long`，则必须严格满足：`stop_loss < entry_zone.min <= entry_zone.max < take_profit_targets[0] < take_profit_targets[1]`
- 若为 `short`，则必须严格满足：`stop_loss > entry_zone.max >= entry_zone.min > take_profit_targets[0] > take_profit_targets[1]`
- 若为 `wait`，则 `entry_zone` 的 `min`/`max`，`take_profit_targets`，`stop_loss` 均应填 `null` 或 `0`。
- `confidence_score` 为评分表合计分的整数部分（满分12），必须与下方 Markdown 评分表的 `合计` 完全一致！

第二部分：人类阅读层 (Markdown Format)
在 JSON 块之后空一行，输出以飞扬口吻编写的分析报告。

### 🚨 飞扬盯盘警报：[SYMBOL] (当前价格: $[CURRENT_PRICE])

**🔍 盘面与机构结构诊断**：
[犀利诊断：结合 `quant_audit` 指出 1W/1D/4H 趋势结构、4H 黄金口袋与订单块（OB/FVG）位置、4H/1H K线反转形态（Pinbar/吞没/SFP）及顶底背离情况。明确指出当前市场状态(trending/ranging)及为何选择该狙击位。]

**📰 消息与情绪面共振**：
[结合 macro_event 距离、fear_greed 情绪指数、资金费率以及最新 news headlines 进行评估。]

**🎯 交易计划**：
*   **策略**：[具体策略，如：顺势回踩订单块限价接多 / 反抽黄金口袋限价高空 / 观望等待]
*   **埋伏区间**：$[MIN] - $[MAX]（关键支撑/阻力/OB/Fib共振说明）
*   **防守底线（止损）**：$[STOP_LOSS]（结构化止损，含波段高低点与ATR缓冲说明）
*   **利润保护**：TP1 $[TP1] (平仓50%推保本) | TP2 $[TP2]
*   **飞扬叮嘱**：[风控与仓位控制寄语]

---

**评分明细（本单得分拆解）**：

| 评分项 | 达标情况 | 得分 |
|--------|----------|------|
| 大周期方向（1W/1D/4H共振） | [具体描述] | +X |
| 关键位共振（OB/FVG/Fib/S&R） | [列出具体共振点] | +X |
| 动能与形态确认（PA形态/背离/RSI/MACD） | [列出达标指标与K线形态] | +X |
| 盈亏比（>=2.5+2 / >=2.0+1.5 / >=1.8+1） | R:R = X.X | +X |
| 量价配合（成交量+OBV） | [具体描述] | +X |
| 结构清晰度（锚定精准狙击区） | [具体描述] | +X |
| 顺势/防追高适配 | [具体描述] | +X |
| 震荡市惩罚 | market_regime = [X] | X |
| **合计** | | **X/12** |

*硬性过滤检查（任一不通过则强制wait）*：
- 大周期趋势结构：[通过/未通过]
- RSI/KDJ防追单区间：4H RSI=[X], KDJ_J=[X] → [通过/未通过]
- 乖离率：4H MA5偏离 X% → [通过/未通过]
- 极端波动：4H ATR vs 20期均值 = X倍 → [通过/未通过]
- 宏观事件：[检查结果] → [通过/未通过]
- 极端情绪与费率：F&G = X, 费率 = X → [通过/未通过]
- 价格行为/多重结构共振：[具体形态或共振簇] → [通过/未通过]
- 真实数学 R:R >= 1.8：[通过/未通过]
"""

    # Model-specific prompt suffixes for better compatibility
    MODEL_SUFFIXES = {
        "glm": "\n\n【重要提示】请严格按照上述 JSON 格式输出，不要添加任何额外解释。JSON 中不要使用注释。先输出完整的 JSON 块，再输出 Markdown 报告。",
        "deepseek": "\n\n【格式强调】你的输出必须以 ```json 开头的代码块作为第一行。不要在 JSON 块之前输出任何文字。确保 JSON 可被直接解析，不含注释或尾逗号。",
        "default": ""
    }

    def _get_model_suffix(self):
        """Get model-specific formatting instructions."""
        model_lower = self.model_name.lower()
        if "glm" in model_lower:
            return self.MODEL_SUFFIXES["glm"]
        elif "deepseek" in model_lower:
            return self.MODEL_SUFFIXES["deepseek"]
        return self.MODEL_SUFFIXES["default"]

    def get_system_prompt(self):
        """Return the active system prompt with model-specific suffix."""
        base = self._system_prompt or self.DEFAULT_SYSTEM_PROMPT
        return base + self._get_model_suffix()

    def analyze(self, payload):
        """
        Send compressed market data payload to LLM and parse results.
        Retries once with a corrective instruction if the model emits an
        unparseable signal block.
        """
        system_prompt = self.get_system_prompt()
        user_prompt = json.dumps(payload, indent=2, ensure_ascii=False)

        # Build a structured user message that guides the model's attention
        user_message = (
            f"请对以下 {payload.get('symbol', 'UNKNOWN')} 的多周期市场数据进行完整诊断分析。\n"
            f"当前价格: ${payload.get('current_price', 'N/A')}\n"
            f"市场状态: {payload.get('market_regime', {}).get('regime', 'N/A') if isinstance(payload.get('market_regime'), dict) else 'N/A'}\n\n"
            f"完整数据 Payload:\n{user_prompt}\n\n"
            f"请严格按照 6 步分析链分析，输出 JSON 信号块 + Markdown 报告。"
        )

        logger.info(f"Sending request to LLM ({self.model_name})...")
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]

        if self.root_dir:
            try:
                debug_path = os.path.join(self.root_dir, "last_llm_request.json")
                debug_data = {
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "model": self.model_name,
                    "temperature": self.temperature,
                    "system_prompt": system_prompt,
                    "user_message": user_message,
                    "payload": payload
                }
                with open(debug_path, "w", encoding="utf-8") as f:
                    json.dump(debug_data, f, indent=2, ensure_ascii=False)
                logger.info(f"Successfully saved last LLM request debug info to {debug_path}")
            except Exception as e:
                logger.warning(f"Failed to save last LLM request debug info: {e}")

        last_parse_error = None
        content = ""  # Pre-initialize to avoid fragile dir() check in retry
        for attempt in range(2):
            try:
                extra_args = {}
                base_url_str = str(self.client.base_url)
                model_lower = str(self.model_name or "").lower()
                is_reasoning_endpoint = (
                    "deepseek" in model_lower
                    or "r1" in model_lower
                    or "qwq" in model_lower
                    or "volces.com" in base_url_str
                )
                effective_max_tokens = (
                    max(int(self.max_tokens or 4096), 12288)
                    if is_reasoning_endpoint
                    else int(self.max_tokens or 4096)
                )
                if "localhost" in base_url_str or "127.0.0.1" in base_url_str:
                    extra_args["extra_body"] = {"options": {"num_ctx": 16384}}
                elif attempt == 1 and "volces.com" in base_url_str:
                    extra_args["extra_body"] = {"thinking": {"type": "disabled"}}
                    effective_max_tokens = max(effective_max_tokens, 16384)

                # Retry mechanism for 429 Too Many Requests
                max_retries = 3
                retry_delay = 2
                
                for attempt_api in range(max_retries):
                    try:
                        response = self.client.chat.completions.create(
                            model=self.model_name,
                            messages=messages,
                            temperature=self.temperature,
                            max_tokens=effective_max_tokens,
                            **extra_args
                        )
                        break
                    except Exception as e:
                        err_msg = str(e)
                        if "429" in err_msg or "TooManyRequests" in err_msg or "ServerOverloaded" in err_msg:
                            if attempt_api < max_retries - 1:
                                logger.warning(f"LLM API rate limit / overloaded (429). Retrying in {retry_delay}s... ({attempt_api+1}/{max_retries})")
                                time.sleep(retry_delay)
                                retry_delay *= 2
                                continue
                        raise e

                content = response.choices[0].message.content or ""
                finish_reason = getattr(response.choices[0], "finish_reason", None)
                if (finish_reason == "length" or not content.strip()) and "volces.com" in base_url_str and "extra_body" not in extra_args:
                    logger.warning(f"Reasoning token budget exhausted (finish_reason={finish_reason}, content_len={len(content)}). Retrying with thinking disabled...")
                    response = self.client.chat.completions.create(
                        model=self.model_name,
                        messages=messages,
                        temperature=self.temperature,
                        max_tokens=max(effective_max_tokens, 16384),
                        extra_body={"thinking": {"type": "disabled"}}
                    )
                    content = response.choices[0].message.content or ""
                logger.info("Received response from LLM.")

                payload_ctx = {
                    "market_context": payload.get("market_context"),
                    "quant_audit": payload.get("quant_audit"),
                }
                json_signal, markdown_report = self._parse_response(
                    content,
                    payload.get("current_price"),
                    payload_context=payload_ctx
                )
                return json_signal, markdown_report

            except ValueError as e:
                last_parse_error = e
                logger.warning(f"LLM output parse failed (attempt {attempt + 1}/2): {e}")
                messages.append({
                    "role": "assistant",
                    "content": content
                })
                messages.append({
                    "role": "user",
                    "content": (
                        "你上一条输出无法被解析。请严格按以下格式重新输出：\n"
                        "1. 第一行必须是 ```json\n"
                        "2. 然后是完整的 JSON 对象（不含注释、不含尾逗号）\n"
                        "3. 然后是 ```\n"
                        "4. 空一行后是 Markdown 报告\n"
                        "不要输出任何其他多余内容。"
                    )
                })
            except Exception as e:
                logger.error(f"Error during LLM inference: {e}")
                raise e

        raise ValueError(f"LLM 连续两次输出均无法解析为有效交易信号：{last_parse_error}")

    def _parse_response(self, text, current_price, payload_context=None):
        """Extract JSON block and Markdown text. Perform logical checks."""
        clean_text = text.strip()
        json_signal = None
        markdown_part = ""

        def sanitize_json_str(s):
            # Remove accidental HTML <br> tags emitted by some LLM endpoints
            s = re.sub(r"<br\s*/?>", " ", s, flags=re.IGNORECASE)
            # Remove single-line JS comments
            s = re.sub(r"//.*?\n", "\n", s)
            # Remove trailing commas before } or ]
            s = re.sub(r",\s*([}\]])", r"\1", s)
            return s

        # Method 1: Match ```json ... ``` codeblock
        codeblock_pattern = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.IGNORECASE | re.DOTALL)
        match = codeblock_pattern.search(clean_text)

        if match:
            candidate = sanitize_json_str(match.group(1).strip())
            try:
                json_signal = json.loads(candidate)
                markdown_part = clean_text[match.end():].strip()
            except Exception as e:
                logger.warning(f"Codeblock JSON parse failed: {e}. Falling back to outer brace search.")

        # Method 2: Outer brace search
        if json_signal is None:
            first_brace = clean_text.find("{")
            last_brace = clean_text.rfind("}")
            if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
                candidate = sanitize_json_str(clean_text[first_brace:last_brace + 1].strip())
                try:
                    json_signal = json.loads(candidate)
                    markdown_part = (clean_text[:first_brace] + clean_text[last_brace + 1:]).strip()
                except Exception as e:
                    logger.error(f"Outer brace JSON parse failed: {e}")
                    raise ValueError(f"无法解析 LLM 的 JSON 诊断数据块（{e}）。原始响应开头：{clean_text[:200]}")
            else:
                raise ValueError(f"LLM 响应中未查找到 JSON 数据块。响应开头：{clean_text[:200]}")

        # Reconcile confidence_score with the markdown scoring table total BEFORE _normalize_signal
        # The table's "合计" row is the ground truth (LLM sometimes mismatches JSON vs table)
        if markdown_part:
            score_match = re.search(r"合计[^\d]*(\d+(?:\.\d+)?)\s*/\s*12", markdown_part)
            if score_match:
                table_score = int(float(score_match.group(1)))
                json_score = json_signal.get("confidence_score", 0)
                if table_score != json_score:
                    logger.info(
                        f"[ScoreSync] JSON confidence_score={json_score} but table 合计={table_score}. "
                        f"Overriding JSON to match table."
                    )
                    json_signal["confidence_score"] = table_score

        # Inject deterministic payload context (market_context, quant_audit) before normalization & validation
        if isinstance(payload_context, dict):
            if payload_context.get("market_context") and "market_context" not in json_signal:
                json_signal["market_context"] = payload_context["market_context"]
            if payload_context.get("quant_audit") and "quant_audit" not in json_signal:
                json_signal["quant_audit"] = payload_context["quant_audit"]

        # Normalize signal fields
        json_signal = self._normalize_signal(json_signal)

        # Perform logical checks & deterministic R:R enforcement
        self._validate_signal(json_signal, current_price)

        return json_signal, markdown_part

    def _normalize_signal(self, signal):
        """Normalize and fill default values for new fields."""
        # Ensure required fields exist
        signal.setdefault("market_regime", "unknown")
        signal.setdefault("entry_type", "limit")
        signal.setdefault("confluence_factors", [])

        # Normalize signal_class (trade subtype)
        valid_classes = ("pullback_long", "pullback_short", "breakout_long", "breakout_short", "wait")
        sig_class = str(signal.get("signal_class") or "").lower().strip()
        if sig_class not in valid_classes:
            # Infer from signal_type if LLM didn't provide it
            sig_type_raw = str(signal.get("signal_type") or "").lower().strip()
            if sig_type_raw == "long":
                sig_class = "pullback_long"
            elif sig_type_raw == "short":
                sig_class = "pullback_short"
            else:
                sig_class = "wait"
        signal["signal_class"] = sig_class

        # Normalize signal_type (guard against null from LLM)
        sig = str(signal.get("signal_type") or "").lower().strip()
        if sig not in ("long", "short", "wait"):
            signal["signal_type"] = "wait"
        else:
            signal["signal_type"] = sig  # Write back normalized lowercase

        # Auto-normalize entry_zone min/max order if both are positive
        entry_zone = signal.get("entry_zone")
        if isinstance(entry_zone, dict):
            raw_min = entry_zone.get("min")
            raw_max = entry_zone.get("max")
            try:
                if raw_min is not None and raw_max is not None:
                    f_min, f_max = float(raw_min), float(raw_max)
                    if f_min > 0 and f_max > 0 and f_min > f_max:
                        entry_zone["min"] = f_max
                        entry_zone["max"] = f_min
            except (ValueError, TypeError):
                pass

        # Ensure confidence_score is int
        try:
            signal["confidence_score"] = int(float(signal.get("confidence_score", 0)))
        except (ValueError, TypeError):
            signal["confidence_score"] = 0

        # Force wait if confidence < self.min_confidence
        if signal["confidence_score"] < self.min_confidence and signal["signal_type"] != "wait":
            logger.info(f"Confidence {signal['confidence_score']} < {self.min_confidence}, forcing wait.")
            signal["signal_type"] = "wait"
            signal["signal_class"] = "wait"

        # Extract deterministic market context & quantitative audit hard filters
        market_ctx = signal.get("market_context") or {}
        trading_bias = market_ctx.get("trading_bias", "normal")
        macro_event = market_ctx.get("macro_event")
        should_stand_aside = (
            trading_bias == "stand_aside"
            or (macro_event and abs(macro_event.get("hours_until", 999)) < 2)
        )

        # Check Fear & Greed extreme: > 85 blocks LONG, < 15 blocks SHORT
        fear_greed = market_ctx.get("fear_greed") or {}
        fng_value = fear_greed.get("value")
        fng_blocks_long = fng_value is not None and fng_value > 85
        fng_blocks_short = fng_value is not None and fng_value < 15

        # Check Funding rate extreme: > 0.05% blocks LONG, < -0.05% blocks SHORT
        funding_rates = market_ctx.get("funding_rates") or {}
        symbol_str = signal.get("symbol", "")
        sym_fr = funding_rates.get(symbol_str)
        fr_blocks_long = sym_fr is not None and sym_fr > 0.0005
        fr_blocks_short = sym_fr is not None and sym_fr < -0.0005

        # Check deterministic quantitative audit hard filters (4H ATR anomaly & RSI/KDJ extreme exhaustion)
        quant_audit = signal.get("quant_audit") or {}
        hf_4h = quant_audit.get("hard_filter_checks_4h") or {}
        atr_anomaly_blocked = bool(hf_4h.get("atr_volatility_anomaly_blocked", False))
        qa_blocks_long = bool(hf_4h.get("long_overbought_blocked", False))
        qa_blocks_short = bool(hf_4h.get("short_oversold_blocked", False))

        # Enforce hard filters on active signals (when LLM outputs long/short directly despite hard filter violation)
        if signal["signal_type"] in ("long", "short"):
            if should_stand_aside or atr_anomaly_blocked:
                logger.info(
                    f"[HardFilter] {signal['signal_type'].upper()} blocked by stand_aside={should_stand_aside} "
                    f"or atr_anomaly={atr_anomaly_blocked}. Forcing WAIT."
                )
                signal["signal_type"] = "wait"
                signal["signal_class"] = "wait"
            elif signal["signal_type"] == "long" and (fng_blocks_long or fr_blocks_long or qa_blocks_long):
                logger.info(
                    f"[HardFilter] Direct LONG blocked by F&G={fng_value}, FR={sym_fr}, or 4H Overbought={qa_blocks_long}. Forcing WAIT."
                )
                signal["signal_type"] = "wait"
                signal["signal_class"] = "wait"
            elif signal["signal_type"] == "short" and (fng_blocks_short or fr_blocks_short or qa_blocks_short):
                logger.info(
                    f"[HardFilter] Direct SHORT blocked by F&G={fng_value}, FR={sym_fr}, or 4H Oversold={qa_blocks_short}. Forcing WAIT."
                )
                signal["signal_type"] = "wait"
                signal["signal_class"] = "wait"

        # Safety net: LLM said "wait" but score >= threshold AND provided valid trade geometry
        # This catches the contradiction where the report describes a trade but JSON says wait
        # BUT: do NOT override if a legitimate hard filter (R:R < 1.8) blocked the signal
        #      or if macro event risk control / market bias says stand aside
        #      or if Fear & Greed / Funding rate / Quant Audit extremes would be violated
        elif signal["signal_type"] == "wait" and signal["confidence_score"] >= self.min_confidence:
            if not should_stand_aside and not atr_anomaly_blocked:
                entry_zone = signal.get("entry_zone", {}) or {}
                entry_min = float(entry_zone.get("min", 0) or 0)
                entry_max = float(entry_zone.get("max", 0) or 0)
                sl = float(signal.get("stop_loss", 0) or 0)
                tps = signal.get("take_profit_targets", []) or []
                tp1 = float(tps[0]) if tps and tps[0] is not None else 0.0
                rr = float(signal.get("risk_reward_ratio", 0) or 0)

                # Check if there's valid non-zero trade geometry AND R:R passes the hard filter
                if entry_min > 0 and entry_max > 0 and sl > 0 and tp1 > 0 and rr >= 1.8:
                    if sl < entry_min and tp1 > entry_max:
                        # LONG override: check fear_greed, funding rate, and 4H overbought hard filters
                        if fng_blocks_long or fr_blocks_long or qa_blocks_long:
                            block_reasons = []
                            if fng_blocks_long:
                                block_reasons.append(f"Fear&Greed={fng_value} > 85")
                            if fr_blocks_long:
                                block_reasons.append(f"FundingRate={sym_fr} > 0.05%")
                            if qa_blocks_long:
                                block_reasons.append("4H RSI/KDJ Extreme Overbought")
                            logger.info(
                                f"[ConsistencyFix] LONG override BLOCKED by hard filter: {', '.join(block_reasons)}. "
                                f"Keeping WAIT."
                            )
                        else:
                            signal["signal_type"] = "long"
                            if signal.get("signal_class") == "wait":
                                signal["signal_class"] = "pullback_long"
                            logger.warning(
                                f"[ConsistencyFix] LLM output wait with score {signal['confidence_score']} "
                                f"but valid long geometry (SL={sl} < entry={entry_min}-{entry_max} < TP={tp1}, R:R={rr}). "
                                f"Overriding to LONG."
                            )
                    elif sl > entry_max and tp1 < entry_min:
                        # SHORT override: check fear_greed, funding rate, and 4H oversold hard filters
                        if fng_blocks_short or fr_blocks_short or qa_blocks_short:
                            block_reasons = []
                            if fng_blocks_short:
                                block_reasons.append(f"Fear&Greed={fng_value} < 15")
                            if fr_blocks_short:
                                block_reasons.append(f"FundingRate={sym_fr} < -0.05%")
                            if qa_blocks_short:
                                block_reasons.append("4H RSI/KDJ Extreme Oversold")
                            logger.info(
                                f"[ConsistencyFix] SHORT override BLOCKED by hard filter: {', '.join(block_reasons)}. "
                                f"Keeping WAIT."
                            )
                        else:
                            signal["signal_type"] = "short"
                            if signal.get("signal_class") == "wait":
                                signal["signal_class"] = "pullback_short"
                            logger.warning(
                                f"[ConsistencyFix] LLM output wait with score {signal['confidence_score']} "
                                f"but valid short geometry (SL={sl} > entry={entry_min}-{entry_max} > TP={tp1}, R:R={rr}). "
                                f"Overriding to SHORT."
                            )

        return signal

    def _validate_signal(self, signal, current_price):
        """
        Strictly validate the logical geometry and mathematical R:R of the JSON signal.
        If an active signal (long/short) violates directional geometry or has a sub-threshold
        true R:R (< 1.15), it is deterministically downgraded to 'wait' to protect capital.
        """
        signal_type = signal.get("signal_type")
        symbol = signal.get("symbol")

        if signal_type not in ("long", "short"):
            return

        entry_zone = signal.get("entry_zone", {}) or {}
        raw_min = entry_zone.get("min")
        raw_max = entry_zone.get("max")
        tp_targets = [float(x) for x in (signal.get("take_profit_targets") or []) if x is not None and float(x) > 0]
        raw_sl = signal.get("stop_loss")

        if None in [raw_min, raw_max, raw_sl] or not tp_targets:
            logger.warning(f"[{symbol}] {signal_type.upper()} signal has null/empty trade boundaries. Forcing WAIT.")
            signal["signal_type"] = "wait"
            signal["signal_class"] = "wait"
            return

        entry_min = float(min(raw_min, raw_max))
        entry_max = float(max(raw_min, raw_max))
        entry_zone["min"] = entry_min
        entry_zone["max"] = entry_max
        sl = float(raw_sl)

        if entry_min <= 0 or sl <= 0:
            logger.warning(f"[{symbol}] {signal_type.upper()} signal has non-positive prices. Forcing WAIT.")
            signal["signal_type"] = "wait"
            signal["signal_class"] = "wait"
            return

        if signal_type == "long":
            # Sort TPs ascending for LONG so TP1 is nearest target and TP2 is farthest target
            tp_targets.sort()
            signal["take_profit_targets"] = tp_targets
            is_valid = (sl < entry_min) and (entry_min <= entry_max) and (tp_targets[0] > entry_max)
            if not is_valid:
                logger.warning(
                    f"[{symbol}] LONG boundaries violation: "
                    f"SL({sl}) < EntryMin({entry_min}) <= EntryMax({entry_max}) < TP1({tp_targets[0]}). Forcing WAIT."
                )
                signal["signal_type"] = "wait"
                signal["signal_class"] = "wait"
                return
            # Pyramid weighted planned entry for LONG (skewed toward deeper support entry_min)
            entry_mid = (entry_min + entry_max) / 2.0
            planned_entry = entry_min * 0.40 + entry_mid * 0.35 + entry_max * 0.25
            risk_dist = planned_entry - sl
            reward_tp1 = tp_targets[0] - planned_entry
            reward_max = tp_targets[-1] - planned_entry
        else:
            # Sort TPs descending for SHORT so TP1 is nearest target and TP2 is farthest target
            tp_targets.sort(reverse=True)
            signal["take_profit_targets"] = tp_targets
            is_valid = (sl > entry_max) and (entry_max >= entry_min) and (tp_targets[0] < entry_min)
            if not is_valid:
                logger.warning(
                    f"[{symbol}] SHORT boundaries violation: "
                    f"SL({sl}) > EntryMax({entry_max}) >= EntryMin({entry_min}) > TP1({tp_targets[0]}). Forcing WAIT."
                )
                signal["signal_type"] = "wait"
                signal["signal_class"] = "wait"
                return
            # Pyramid weighted planned entry for SHORT (skewed toward higher resistance entry_max)
            entry_mid = (entry_min + entry_max) / 2.0
            planned_entry = entry_max * 0.40 + entry_mid * 0.35 + entry_min * 0.25
            risk_dist = sl - planned_entry
            reward_tp1 = planned_entry - tp_targets[0]
            reward_max = planned_entry - tp_targets[-1]

        if risk_dist > 0:
            rr_tp1 = round(reward_tp1 / risk_dist, 2)
            rr_blended = round((0.5 * reward_tp1 + 0.5 * reward_max) / risk_dist, 2)
            effective_rr = max(rr_tp1, rr_blended)
            signal["calculated_rr"] = effective_rr
            # Guard against hallucinated high R:R when actual price geometry gives negative/terrible EV
            if effective_rr < 1.15:
                logger.warning(
                    f"[{symbol}] {signal_type.upper()} rejected by deterministic R:R check: "
                    f"true R:R={effective_rr} (TP1 R:R={rr_tp1}) < 1.15 minimum threshold. Forcing WAIT."
                )
                signal["signal_type"] = "wait"
                signal["signal_class"] = "wait"
                return

    def analyze_with_consensus(self, payload, consensus_enabled=True):
        """
        Dual-call consensus mechanism for higher signal accuracy.
        
        Calls the LLM twice with slightly different temperatures.
        Only returns a tradeable signal (long/short) if BOTH calls agree
        on direction. If they disagree, returns "wait" to avoid false signals.
        
        This significantly reduces hallucinated signals at the cost of
        occasionally missing valid setups (acceptable trade-off for capital preservation).
        
        Args:
            payload: Market data payload dict
            consensus_enabled: If False, falls back to single-call analyze()
        
        Returns:
            (json_signal, markdown_report) tuple
        """
        if not consensus_enabled:
            return self.analyze(payload)

        # First call: standard temperature
        signal_1, report_1 = self.analyze(payload)
        sig_type_1 = signal_1.get("signal_type", "wait")

        # If first call says wait, no need for second call
        if sig_type_1 == "wait":
            logger.info(f"[Consensus] First call = wait, skipping second call.")
            return signal_1, report_1

        # Second call: slightly higher temperature for diversity
        original_temp = self.temperature
        self.temperature = min(0.3, original_temp + 0.1)
        try:
            signal_2, report_2 = self.analyze(payload)
        finally:
            self.temperature = original_temp

        sig_type_2 = signal_2.get("signal_type", "wait")

        # Consensus check: both must agree on direction
        if sig_type_1 == sig_type_2:
            # Agreement! Use the signal with higher confidence
            if signal_2.get("confidence_score", 0) > signal_1.get("confidence_score", 0):
                final_signal = signal_2
                final_report = report_2
            else:
                final_signal = signal_1
                final_report = report_1

            # Average the confidence scores for a more conservative estimate
            avg_conf = round((signal_1.get("confidence_score", 0) + signal_2.get("confidence_score", 0)) / 2)
            final_signal["confidence_score"] = avg_conf
            final_signal["consensus"] = True
            # Sync the report's 合计 row to match the consensus-adjusted score
            final_report = re.sub(r"(合计[^\d]*)\d+(\s*/\s*12)", rf"\g<1>{avg_conf}\2", final_report)
            logger.info(
                f"[Consensus] AGREEMENT: both calls = {sig_type_1} "
                f"(conf {signal_1.get('confidence_score')}/{signal_2.get('confidence_score')} → avg {avg_conf})"
            )
            return final_signal, final_report
        else:
            # Disagreement → check if one signal is strong enough to stand alone
            conf_1 = signal_1.get("confidence_score", 0)
            conf_2 = signal_2.get("confidence_score", 0)
            max_conf = max(conf_1, conf_2)

            if max_conf >= self.min_confidence + 1:
                # One call is highly confident — trust it (reduce score slightly for uncertainty)
                if conf_1 >= conf_2:
                    final_signal = signal_1
                    final_report = report_1
                else:
                    final_signal = signal_2
                    final_report = report_2
                penalized_conf = max_conf - 1  # Penalize 1 point for lack of consensus
                final_signal["confidence_score"] = penalized_conf
                final_signal["consensus"] = False
                # Safety: if penalized score drops below min_confidence, force wait
                if penalized_conf < self.min_confidence:
                    logger.info(
                        f"[Consensus] DISAGREEMENT: penalized conf {penalized_conf} < min_confidence {self.min_confidence}, "
                        f"forcing wait."
                    )
                    final_signal["signal_type"] = "wait"
                # Sync the report's 合计 row to match the penalized score
                final_report = re.sub(r"(合计[^\d]*)\d+(\s*/\s*12)", rf"\g<1>{penalized_conf}\2", final_report)
                logger.info(
                    f"[Consensus] DISAGREEMENT but high-confidence override: "
                    f"call1={sig_type_1}(conf={conf_1}) vs call2={sig_type_2}(conf={conf_2}) "
                    f"→ using {final_signal['signal_type']} with conf={final_signal['confidence_score']}"
                )
                return final_signal, final_report

            # Both calls low confidence → force wait
            logger.info(
                f"[Consensus] DISAGREEMENT: call1={sig_type_1}(conf={conf_1}) vs call2={sig_type_2}(conf={conf_2}) "
                f"→ both below {self.min_confidence + 1}, forcing wait. Ambiguous market conditions."
            )
            wait_signal = {
                "symbol": payload.get("symbol", "UNKNOWN"),
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "signal_type": "wait",
                "confidence_score": 0,
                "market_regime": signal_1.get("market_regime", "unknown"),
                "entry_zone": {"min": 0, "max": 0},
                "entry_type": "limit",
                "take_profit_targets": [0, 0],
                "stop_loss": 0,
                "risk_reward_ratio": 0,
                "confluence_factors": [],
                "consensus": False,
                "core_reason": f"双次诊断方向不一致（{sig_type_1} vs {sig_type_2}），且置信度均不足，观望等待更明确共振。"
            }
            combined_report = (
                f"### 飞扬盯盘警报：{payload.get('symbol', 'UNKNOWN')} — 双次诊断不一致，观望\n\n"
                f"**诊断结果**：第一次诊断给出 {sig_type_1.upper()} 信号（置信度 {conf_1}），第二次诊断给出 {sig_type_2.upper()} 信号（置信度 {conf_2}）。\n"
                f"方向不一致且置信度不足，兄弟们别急，等市场给出更明确的方向再动手。\n\n"
                f"---\n*以下为第一次诊断参考：*\n\n{report_1}"
            )
            return wait_signal, combined_report

    def quick_confirm(self, payload):
        """
        Lightweight re-diagnosis for pending trades approaching the entry zone.
        Analyzes short-term market data (15min, 30min, 1H) to predict the
        expected direction in the next few hours, then decides whether to
        keep / cancel / reverse the pending order.

        Unlike the full analyze() which does a comprehensive 12-point scoring,
        this is a focused, fast call that only looks at near-term price action
        to determine if the original signal direction is still valid at the
        current price level.

        Args:
            payload: dict with keys:
                - "symbol": str
                - "current_price": float
                - "original_signal_type": "long" or "short"
                - "entry_min": float
                - "entry_max": float
                - "stop_loss": float
                - "take_profit_targets": [float, ...]
                - "core_reason": str (original signal reasoning)
                - "signal_regime": str (original market regime)
                - "signal_stale": bool (whether the signal has exceeded freshness_hours)
                - "signal_stale_since": str or None (when the signal went stale)
                - "market_data_15m": dict or None (compact OHLCV for 15min candles)
                - "market_data_30m": dict or None (compact OHLCV for 30min candles)
                - "market_data_1h": dict or None (compact OHLCV for 1H candles)

        Returns:
            dict with keys:
                - "action": "keep" | "cancel" | "reverse"
                - "new_signal_type": "long" or "short" (only if action == "reverse")
                - "new_entry_min": float (optional)
                - "new_entry_max": float (optional)
                - "new_stop_loss": float (optional)
                - "new_take_profit_targets": [float, ...] (optional)
                - "reason": str (LLM explanation)
                - "confidence": int (0-12, how confident the LLM is in this re-diagnosis)
        """
        symbol = payload.get("symbol", "UNKNOWN")
        current_price = payload.get("current_price", 0.0)
        original_sig = payload.get("original_signal_type", "wait")
        entry_min = payload.get("entry_min", 0.0)
        entry_max = payload.get("entry_max", 0.0)
        stop_loss = payload.get("stop_loss", 0.0)
        tps = payload.get("take_profit_targets", [])
        core_reason = payload.get("core_reason", "")
        signal_regime = payload.get("signal_regime", "unknown")
        signal_stale = payload.get("signal_stale", False)
        signal_stale_since = payload.get("signal_stale_since", None)

        # Build market data summary for each timeframe
        def _format_market_data(md, label):
            """Format a compact market data dict into a readable string."""
            if not md or not isinstance(md, dict):
                return f"【{label}】无数据\n"
            closes = md.get("close", [])
            highs = md.get("high", [])
            lows = md.get("low", [])
            volumes = md.get("volume", [])
            rsis = md.get("rsi", [])
            mas = md.get("ma5", [])
            emas = md.get("ema21", [])
            if not closes or len(closes) < 3:
                return f"【{label}】数据不足\n"
            lines = [f"【{label}】最近 {len(closes)} 根 K 线："]
            lines.append(f"  价格区间：{min(lows):.4f} - {max(highs):.4f}（当前 ${current_price:.4f}）")
            lines.append(f"  最近 5 根收盘价：{[round(c, 4) for c in closes[-5:]]}")
            if rsis and len(rsis) >= 1:
                lines.append(f"  RSI_14 值：{[round(r, 1) for r in rsis[-5:]]}")
            if mas and len(mas) >= 1:
                lines.append(f"  MA5 值：{[round(m, 4) for m in mas[-5:]]}")
            if emas and len(emas) >= 1:
                lines.append(f"  EMA21 值：{[round(e, 4) for e in emas[-5:]]}")
            # Volume trend
            if volumes and len(volumes) >= 5:
                recent_vols = volumes[-5:]
                avg_vol = sum(recent_vols[:-1]) / max(len(recent_vols) - 1, 1)
                vol_ratio = recent_vols[-1] / max(avg_vol, 0.01)
                lines.append(f"  成交量：最新={recent_vols[-1]:.0f}，前4根均值={avg_vol:.0f}，倍率={vol_ratio:.2f}x")
            # Price position relative to entry zone (accurate pullback/rally description)
            entry_center = (entry_min + entry_max) / 2.0
            dist_pct = abs(current_price - entry_center) / current_price * 100.0 if current_price > 0 else 0.0
            if current_price > entry_center:
                lines.append(f"  距吃单区间：当前价高于区间中心 {dist_pct:.2f}%（需价格下行回踩 {dist_pct:.2f}% 触及埋伏区）")
            else:
                lines.append(f"  距吃单区间：当前价低于区间中心 {dist_pct:.2f}%（需价格上行反抽 {dist_pct:.2f}% 触及埋伏区）")
            return "\n".join(lines)

        # Build the directional prediction prompt
        md_15m_block = _format_market_data(payload.get("market_data_15m"), "15分钟")
        md_30m_block = _format_market_data(payload.get("market_data_30m"), "30分钟")
        md_1h_block = _format_market_data(payload.get("market_data_1h"), "1小时")

        stale_tag = "⚠️ 信号已过期" if signal_stale else "✅ 信号仍在有效期内"
        if signal_stale and signal_stale_since:
            stale_tag += f"（{signal_stale_since} 起标记为过期）"

        quick_prompt = (
            f"【挂单埋伏再诊断】{symbol} 当前价格 ${current_price}\n"
            f"{stale_tag}\n\n"
            f"原始信号信息：\n"
            f"  - 方向：{original_sig.upper()}\n"
            f"  - 原始依据：{core_reason}\n"
            f"  - 原始市场状态：{signal_regime}\n"
            f"  - 吃单区间：${entry_min} - ${entry_max}\n"
            f"  - 止损线：${stop_loss}\n"
            f"  - 止盈目标：{', '.join([f'${t}' for t in tps])}\n\n"
            f"多时间框架市场数据：\n"
            f"{md_15m_block}\n"
            f"{md_30m_block}\n"
            f"{md_1h_block}\n\n"
            f"【任务】\n"
            f"请基于以上多时间框架数据，评估该【限价埋伏挂单】在价格接近吃单区间 [${entry_min}, ${entry_max}] 时是否仍然安全有效。\n"
            f"核心交易逻辑提醒：\n"
            f"- 限价做多挂单 (LONG) 本身就是等待价格从上方【回踩下跌】到支撑区间 [${entry_min}, ${entry_max}] 接多！因此 15m/30m 出现正常缩量/温和回踩下跌属于【正常入场过程】，绝不能因为正在回踩支撑就误判为反转做空！\n"
            f"- 限价做空挂单 (SHORT) 本身就是等待价格从下方【反弹上行】到阻力区间 [${entry_min}, ${entry_max}] 接空！因此 15m/30m 出现正常反弹测试阻力属于【正常入场过程】，绝不能因为正在反弹测试阻力就误判为反转做多！\n"
            f"- 只有当 1小时级别趋势结构彻底破坏（例如异常巨量 >2.5x 恐慌砸盘直接击穿支撑结构、或放量强行突破阻力且无任何受阻迹象）时，才应选择 cancel 或 reverse。\n\n"
            f"请严格按以下 JSON 格式输出（不要加 Markdown 代码块标记，只输出纯 JSON）：\n"
            f"{{\n"
            f'  "action": "keep" | "cancel" | "reverse",\n'
            f'  "new_signal_type": "long" | "short"（仅当 action 为 reverse 时必填）,\n'
            f'  "new_entry_min": <float，反转时必填的新吃单区间下限>,\n'
            f'  "new_entry_max": <float，反转时必填的新吃单区间上限>,\n'
            f'  "new_stop_loss": <float，反转时必填的新止损价，LONG必须<new_entry_min，SHORT必须>new_entry_max>,\n'
            f'  "new_take_profit_targets": [<float>, ...]（反转时必填的新止盈目标列表）,\n'
            f'  "reason": "简短的中文理由，说明属于健康回踩/反抽埋伏区(keep)还是大级别结构破坏(cancel/reverse)",\n'
            f'  "confidence": <int 0-12, 本次再诊断置信度>\n'
            f"}}\n\n"
            f"注意：不要输出任何其他内容，只需输出纯 JSON 对象。"
        )

        logger.info(f"[QuickConfirm] Sending directional prediction re-diagnosis for {symbol} (original={original_sig}, stale={signal_stale})...")

        # Use a lower temperature for more deterministic output
        original_temp = self.temperature
        self.temperature = min(0.1, original_temp)

        try:
            qc_extra = {}
            if "volces.com" in str(self.client.base_url):
                qc_extra["extra_body"] = {"thinking": {"type": "disabled"}}

            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是一名机构级加密合约交易员与风控专家。你的任务是在价格接近限价埋伏挂单区间时，"
                            "基于 15分钟、30分钟、1小时 K 线数据检验该埋伏区的支撑/阻力有效性。\n\n"
                            "核心风控准则：\n"
                            "1. 区分【健康回踩/反抽】与【结构击穿】：做多挂单需要价格回落才能成交，做空挂单需要价格反弹才能成交。"
                            "只要回踩/反弹过程中未出现极端放量击穿（1H主结构与防守位完好），必须输出 keep 继续埋伏！\n"
                            "2. cancel：当价格以极端恐慌巨量直冲止损位（接飞刀风险极高）或市场混乱无序时，输出 cancel 撤单避险。\n"
                            "3. reverse：仅当 1H 级别关键结构已被放量彻底击穿并形成明确的反向趋势（置信度 >= 8），且你能给出完整合法的新入场区间、新止损和新止盈时，才输出 reverse。\n"
                            "4. 如果数据不足或回踩节奏正常，一律优先选择 keep。"
                        )
                    },
                    {"role": "user", "content": quick_prompt}
                ],
                temperature=self.temperature,
                max_tokens=4096,
                **qc_extra
            )

            content = (response.choices[0].message.content or "").strip()

            # Parse the JSON response (handle both pure JSON and codeblock-wrapped)
            import re as _re
            json_match = _re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", content, _re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                first_b = content.find("{")
                last_b = content.rfind("}")
                if first_b != -1 and last_b != -1 and last_b > first_b:
                    json_str = content[first_b:last_b + 1]
                else:
                    json_str = content

            # Sanitize common JSON issues
            json_str = _re.sub(r"<br\s*/?>", " ", json_str, flags=_re.IGNORECASE)
            json_str = _re.sub(r",\s*([}\]])", r"\1", json_str)
            json_str = _re.sub(r"//.*?\n", "\n", json_str)

            result = json.loads(json_str)

            # Validate the result
            action = result.get("action", "keep")
            if action not in ("keep", "cancel", "reverse"):
                logger.warning(f"[QuickConfirm] Unknown action '{action}', defaulting to keep.")
                result["action"] = "keep"
                action = "keep"

            # Ensure confidence is present and integer
            try:
                conf_val = int(float(result.get("confidence", 5)))
            except (ValueError, TypeError):
                conf_val = 5
            result["confidence"] = conf_val

            if action == "reverse":
                new_sig = str(result.get("new_signal_type") or "").lower().strip()
                if new_sig not in ("long", "short"):
                    logger.warning("[QuickConfirm] Reverse action missing valid new_signal_type, defaulting to keep.")
                    result["action"] = "keep"
                elif new_sig == str(original_sig).lower().strip():
                    logger.info("[QuickConfirm] Reverse action specified same direction as original, normalizing to keep.")
                    result["action"] = "keep"
                elif conf_val < 7:
                    logger.info(f"[QuickConfirm] Reverse confidence {conf_val} < 7 threshold, defaulting to keep to prevent whipsaw.")
                    result["action"] = "keep"
                else:
                    result["new_signal_type"] = new_sig

            # Ensure reason is present
            if not result.get("reason"):
                result["reason"] = "LLM 再诊断后未提供详细理由"

            logger.info(f"[QuickConfirm] Result for {symbol}: {result['action']} (conf={result['confidence']}) - {result['reason']}")
            return result

        except Exception as e:
            logger.error(f"[QuickConfirm] LLM re-diagnosis failed for {symbol}: {e}")
            # On failure, default to keep (be conservative — don't cancel a trade on API error)
            return {
                "action": "keep",
                "reason": f"再诊断 API 调用失败（{e}），保守起见维持原方向",
                "confidence": 0
            }
        finally:
            self.temperature = original_temp
