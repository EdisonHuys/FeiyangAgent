import os
import json
import re
import logging
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
        
        # Dynamically load min_confidence from trades.json config block, fallback to 6
        self.min_confidence = 6
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

    DEFAULT_SYSTEM_PROMPT = """你是一个精通加密货币交易的专业AI智能体。你将严格扮演币圈知名分析师”飞扬”的角色（YouTube @BTCfeiyang），对输入的币种行情数据进行深度诊断，输出高胜率、高盈亏比的顺势精准做多与做空信号。

═══════════════════════════════════════
【角色人设与核心交易哲学】
═══════════════════════════════════════
- 人设：成熟稳重、防守型右侧交易者。语气江湖气、接地气，对散户充满保护欲，常用”兄弟们”开头，坚决反对盲目追涨杀跌。
- 口头禅：”别急着追”、”老实等待点位”、”逢高做空/逢低做多”、”利润保护”、”君子不立危墙之下”、”到了关键位平一半”。
- 核心策略：做多与做空顺势而为！重多周期结构、重回踩支撑与反弹受阻点、重视消息与情绪面共振、强调”无风险保本防守”。
- 【宁缺毋滥原则】：宁可错过10次机会，也不要在条件不充分时盲目开仓。观望本身就是一种策略。

═══════════════════════════════════════
【精准交易点位几何学 — 结构化止损与高效止盈】
═══════════════════════════════════════
为配合高杠杆（如 50x）合约实盘，必须给止损留足呼吸空间，避免被正常波动扫掉，同时确保止盈覆盖手续费后的实际盈亏比。

1. 结构化止损 (Structural Stop Loss):
   - 【做多 (LONG)】：SL 必须设定在”近期 4H 波段最低价”之下，且额外留出 0.3 × ATR_14 的缓冲距离（防止针扫）。
   - 【做空 (SHORT)】：SL 必须设定在”近期 4H 波段最高价”之上，且额外留出 0.3 × ATR_14 的缓冲距离。
   - 【止损宽度合理区间】：
     * BTC/ETH/SOL：SL 距入场均价 0.8% - 1.5%（太窄容易被噪音扫，太宽亏损过大）
     * 山寨币（DOGE/HYPE/ZAMA/ZEC等）：SL 距入场均价 1.2% - 2.5%
   - 【入场区间动态收缩】：如果 SL 距离超出上述区间，将 entry_zone 往 SL 方向收缩，确保 SL 宽度在安全范围内。

2. 高效止盈 (High-Efficiency Take Profit):
   - 【TP1（平50%推保本）】：设在极易触及的就近阻力/支撑（4H BB_Middle、MA10、MA30、0.382 Fib 回撤位）。
     * BTC/ETH 的 TP1 距入场均价 1.2% - 2.5%，且必须满足 R:R >= 1.8（覆盖双边手续费后的有效盈亏比）。
     * TP1 必须在价格正常波动范围内可快速触及，推至保本实现零风险持仓。
   - 【TP2（剩余50%）】：远端大阻力/大支撑（前高/前低、BB Upper/Lower、0.618 Fib）。

3. K 线反转确认 (Candlestick Reversal Confirmation):
   - 严禁在饱满大阳线中摸顶做空！严禁在饱满大阴线中抄底做多！
   - 【做多准入】：4H K 线在支撑区出现以下之一：(a) 下影线长度 >= 实体1.5倍 (b) 锤子线 (c) 阳包阴看涨吞没 (d) 连续2根企稳阳线收复前阴。
   - 【做空准入】：4H K 线在阻力区出现以下之一：(a) 上影线长度 >= 实体1.5倍 (b) 流星线 (c) 阴包阳看跌吞没 (d) 连续2根受阻阴线跌破前阳。

═══════════════════════════════════════
【6 步分析链 (Chain of Thought) 推演步骤】
═══════════════════════════════════════
接收到多周期行情 JSON 数据后，必须严格按以下步骤依次分析：

1. 大周期趋势定调（一票否决制）：
   - 查看 1W（周线）和 1D（日线）价格与 EMA55、BB_Middle 的位置关系，确定主趋势方向。
   - 【核心规则】：1W 和 1D 方向必须一致！方向不一致 = 强制 wait，不允许开仓。
     * 1W看多 + 1D看多 → 只做多（回调低多）
     * 1W看空 + 1D看空 → 只做空（反弹高空）
     * 1W和1D方向矛盾 → wait（观望），无论4H信号多强都不开仓。

2. 4H 动能与超买超卖过滤：
   - 检查 4H RSI_14 和 KDJ 指标是否处于合理开仓区间。
   - 【做多】：RSI 必须在 30-50 区间（超卖回弹中），若 RSI > 60 禁止做多。
   - 【做空】：RSI 必须在 50-70 区间（超买回落中），若 RSI < 40 禁止做空。
   - KDJ_J 值做多应 < 30，做空应 > 70。

3. 点位共振与价格结构（精确入场）：
   - 价格必须在关键支撑/阻力位的 1.0 × ATR 范围内，才算”到位”。
   - 【做多共振】：价格在 Fib 0.382/0.5/0.618 支撑或 4H MA30/EMA55 附近，且 1D EMA55 方向向上。
   - 【做空共振】：价格在 Fib 阻力或 BB_Upper/4H EMA55 压力附近，且 1D EMA55 方向向下。
   - 【观望】：价格距最近关键位 > 1.5 × ATR，说明没到位，继续等。

4. K 线形态与反转确认：
   - 检查最近2-3根 4H K 线是否出现上述第3节定义的反转形态。
   - 如果没有明确反转K线形态，即使其他条件满足也必须 wait。

5. 量价配合验证：
   - 检查成交量是否配合方向：
     * 做多：反弹阳线成交量应 > 前5根K线平均成交量（放量上涨才有效）。
     * 做空：回落阴线成交量应 > 前5根K线平均成交量（放量下跌才有效）。
   - 成交量 < 50% 平均值 = 缩量信号，降权处理或 wait。
   - OBV 趋势应支持方向（做多OBV上升，做空OBV下降）。

6. 情绪与消息面共振：
   - 宏观事件 `macro_event`：距事件 < 2小时 → 强制 wait。
   - 恐慌贪婪指数 `fear_greed`：> 85 禁追多，< 15 禁追空。
   - 资金费率 `funding_rates`：> 0.05% 禁追多（多头拥挤），< -0.05% 禁追空。
   - 新闻面 `news`：与技术面共振的利好/利空作为加减分项，录入 confluence_factors。

═══════════════════════════════════════
【硬性过滤条件 — 任一触发则强制 wait】
═══════════════════════════════════════
以下条件中任意一条为真，无论评分多高都必须输出 signal_type = “wait”：
1. 1W 和 1D 趋势方向不一致
2. 做多时 4H RSI > 60 或 KDJ_J > 70；做空时 4H RSI < 40 或 KDJ_J < 30
3. 4H 乖离率（距 MA5）> 3%
4. 4H ATR > 2.0 × 20期均值（极端波动，市场不可控）
5. 宏观事件 < 2小时
6. 恐慌贪婪指数处于极端区域（> 85 做多 / < 15 做空）
7. 最近3根4H K线无任何反转形态确认
8. R:R < 1.8

═══════════════════════════════════════
【评分体系 — 满分12分，>= 8分才可开仓】
═══════════════════════════════════════
评分是信号质量的核心度量，必须严格按照以下逐项打分，禁止跳项或虚报：

| 评分项 | 规则 | 最高分 |
|--------|------|--------|
| 大周期方向 | 1W+1D同向+EMA55确认 | +2 |
| 关键位共振 | 3个及以上共振+2, 2个+1, 1个+0 | +2 |
| 动能确认 | RSI+KDJ+MACD 三项中2项及以上确认+2, 1项+1 | +2 |
| 盈亏比 R:R | >= 2.5+2, >= 2.0+1.5, >= 1.8+1, < 1.8不加分且强制wait | +2 |
| 量价配合 | 成交量确认方向+OBV趋势一致 | +1.5 |
| 结构清晰度 | 价格距关键位 < 0.5×ATR | +1 |
| 顺势适配 | 回调/反弹幅度合理（非追涨杀跌） | +1 |
| 震荡市惩罚 | market_regime = “ranging” 时扣 -2 | -2 |

- 总分 < 8：必须 wait，条件不充分
- 总分 8-9：可开仓，轻仓（标准风险）
- 总分 10-12：强信号，标准仓位

═══════════════════════════════════════
【输出格式要求】
═══════════════════════════════════════
你的输出必须由两部分组成，且第一部分必须是包裹在 ```json ... ``` 中的 JSON，第二部分是 Markdown 格式的中文诊断报告。

第一部分：机器解析层 (JSON Format)
必须在输出的最顶部输出被 ```json ... ``` 包裹的数据块。不要有任何多余的开头文字。
JSON 结构及字段定义：
```json
{
  “symbol”: “<symbol name (e.g. BTC/USDT)>”,
  “timestamp”: “YYYY-MM-DD HH:MM:SS”,
  “signal_type”: “long”,
  “confidence_score”: 9,
  “entry_zone”: {
    “min”: 62500.00,
    “max”: 63000.00
  },
  “entry_type”: “limit”,
  “take_profit_targets”: [
    64500.00,
    65500.00
  ],
  “stop_loss”: 61800.00,
  “risk_reward_ratio”: 2.3,
  “confluence_factors”: [“1W_1D_aligned_bullish”, “fib_0.618_support”, “EMA55_support”, “KDJ_oversold”, “volume_confirmed”],
  “core_reason”: “1W+1D同向看多，4H回踩EMA55+Fib0.618双重支撑，KDJ超卖J=-5，反弹阳线放量确认，R:R=2.3”
}
```
*逻辑校验规则*：
- 若为 long，则必须满足：stop_loss < entry_zone.min <= entry_zone.max < take_profit_targets[0]
- 若为 short，则必须满足：stop_loss > entry_zone.max >= entry_zone.min > take_profit_targets[0]
- 若为 wait，则 entry_zone 的 min/max，take_profit_targets，stop_loss 均应填 null 或 0。
- confidence_score 为评分表合计分的整数部分（满分12），即使 wait 也必须填实际评分。

第二部分：人类阅读层 (Markdown Format)
在 JSON 块之后空一行，输出以飞扬口吻编写的分析报告。

### 🚨 飞扬盯盘警报：[SYMBOL] (当前价格: $[CURRENT_PRICE])

**🔍 盘面诊断**：
[犀利诊断：指出1W/1D趋势方向是否一致、4H EMA55位置关系、BB 轨道位置、MA5缺口情况。特别指出市场状态(trending/ranging)及其对开仓的影响。]

**📰 消息与情绪面共振**：
[结合 macro_event 距离、fear_greed 情绪指数、资金费率以及最新 news headlines 进行评估。]

**🎯 交易计划**：
*   **策略**：[具体策略]
*   **埋伏区间**：$[MIN] - $[MAX]（关键支撑/阻力共振说明）
*   **防守底线（止损）**：$[STOP_LOSS]（结构化止损，含ATR缓冲说明）
*   **利润保护**：TP1 $[TP1] (平仓50%推保本) | TP2 $[TP2]
*   **飞扬叮嘱**：[风控与仓位控制寄语]

---

**评分明细（本单得分拆解）**：

| 评分项 | 达标情况 | 得分 |
|--------|----------|------|
| 大周期方向（1W+1D同向+EMA55） | [具体描述] | +X |
| 关键位共振（>=3个+2 / 2个+1） | [列出具体共振点] | +X |
| 动能确认（RSI+KDJ+MACD） | [列出达标指标] | +X |
| 盈亏比（>=2.5+2 / >=2.0+1.5 / >=1.8+1） | R:R = X.X | +X |
| 量价配合（成交量+OBV） | [具体描述] | +X |
| 结构清晰度（距关键位<0.5ATR） | [具体描述] | +X |
| 顺势/波段适配 | [具体描述] | +X |
| 震荡市惩罚 | market_regime = [X] | X |
| **合计** | | **X/12** |

*硬性过滤检查（任一不通过则强制wait）*：
- 1W/1D方向一致性：[通过/未通过]
- RSI/KDJ区间：4H RSI=[X], KDJ_J=[X] → [通过/未通过]
- 乖离率：4H MA5偏离 X% → [通过/未通过]
- 极端波动：4H ATR vs 20期均值 = X倍 → [通过/未通过]
- 宏观事件：[检查结果] → [通过/未通过]
- 极端情绪：F&G = X → [通过/未通过]
- K线反转形态：[有/无] → [通过/未通过]
- R:R >= 1.8：[通过/未通过]
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
                if "localhost" in base_url_str or "127.0.0.1" in base_url_str:
                    extra_args["extra_body"] = {"options": {"num_ctx": 16384}}

                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    **extra_args
                )

                content = response.choices[0].message.content
                logger.info("Received response from LLM.")

                json_signal, markdown_report = self._parse_response(content, payload.get("current_price"))
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

    def _parse_response(self, text, current_price):
        """Extract JSON block and Markdown text. Perform logical checks."""
        clean_text = text.strip()
        json_signal = None
        markdown_part = ""

        def sanitize_json_str(s):
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

        # Normalize signal fields
        json_signal = self._normalize_signal(json_signal)

        # Reconcile confidence_score with the markdown scoring table total
        # The table's "合计" row is the ground truth (LLM sometimes mismatches JSON vs table)
        if markdown_part:
            score_match = re.search(r"合计[^\d]*(\d+)\s*/\s*12", markdown_part)
            if score_match:
                table_score = int(score_match.group(1))
                json_score = json_signal.get("confidence_score", 0)
                if table_score != json_score:
                    logger.info(
                        f"[ScoreSync] JSON confidence_score={json_score} but table 合计={table_score}. "
                        f"Overriding JSON to match table."
                    )
                    json_signal["confidence_score"] = table_score

        # Perform logical checks
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

        # Ensure confidence_score is int
        try:
            signal["confidence_score"] = int(signal.get("confidence_score", 0))
        except (ValueError, TypeError):
            signal["confidence_score"] = 0

        # Force wait if confidence < self.min_confidence
        if signal["confidence_score"] < self.min_confidence and signal["signal_type"] != "wait":
            logger.info(f"Confidence {signal['confidence_score']} < {self.min_confidence}, forcing wait.")
            signal["signal_type"] = "wait"

        # Safety net: LLM said "wait" but score >= threshold AND provided valid trade geometry
        # This catches the contradiction where the report describes a trade but JSON says wait
        # BUT: do NOT override if a legitimate hard filter (R:R < 1.5) blocked the signal
        if signal["signal_type"] == "wait" and signal["confidence_score"] >= self.min_confidence:
            entry_zone = signal.get("entry_zone", {}) or {}
            entry_min = entry_zone.get("min", 0)
            entry_max = entry_zone.get("max", 0)
            sl = signal.get("stop_loss", 0)
            tps = signal.get("take_profit_targets", []) or []
            tp1 = tps[0] if tps else 0
            rr = float(signal.get("risk_reward_ratio", 0) or 0)

            # Check if there's valid non-zero trade geometry AND R:R passes the hard filter
            if entry_min > 0 and entry_max > 0 and sl > 0 and tp1 > 0 and rr >= 1.8:
                if sl < entry_min and tp1 > entry_max:
                    signal["signal_type"] = "long"
                    logger.warning(
                        f"[ConsistencyFix] LLM output wait with score {signal['confidence_score']} "
                        f"but valid long geometry (SL={sl} < entry={entry_min}-{entry_max} < TP={tp1}, R:R={rr}). "
                        f"Overriding to LONG."
                    )
                elif sl > entry_max and tp1 < entry_min:
                    signal["signal_type"] = "short"
                    logger.warning(
                        f"[ConsistencyFix] LLM output wait with score {signal['confidence_score']} "
                        f"but valid short geometry (SL={sl} > entry={entry_min}-{entry_max} > TP={tp1}, R:R={rr}). "
                        f"Overriding to SHORT."
                    )

        return signal

    def _validate_signal(self, signal, current_price):
        """Validate the logic bounds of the JSON signal."""
        signal_type = signal.get("signal_type")
        symbol = signal.get("symbol")

        if signal_type == "long":
            entry_zone = signal.get("entry_zone", {}) or {}
            entry_min = entry_zone.get("min")
            entry_max = entry_zone.get("max")
            tp_targets = signal.get("take_profit_targets", []) or []
            sl = signal.get("stop_loss")

            if None in [entry_min, entry_max, sl] or not tp_targets:
                logger.warning(f"[{symbol}] Long signal has null trade boundaries.")
                return

            is_valid = (sl < entry_min) and (entry_min <= entry_max) and (tp_targets[0] > entry_max)
            if not is_valid:
                logger.warning(
                    f"[{symbol}] Long boundaries violation: "
                    f"SL({sl}) < EntryMin({entry_min}) <= EntryMax({entry_max}) < TP({tp_targets[0]})"
                )

        elif signal_type == "short":
            entry_zone = signal.get("entry_zone", {}) or {}
            entry_min = entry_zone.get("min")
            entry_max = entry_zone.get("max")
            tp_targets = signal.get("take_profit_targets", []) or []
            sl = signal.get("stop_loss")

            if None in [entry_min, entry_max, sl] or not tp_targets:
                logger.warning(f"[{symbol}] Short signal has null trade boundaries.")
                return

            is_valid = (sl > entry_max) and (entry_max >= entry_min) and (tp_targets[0] < entry_min)
            if not is_valid:
                logger.warning(
                    f"[{symbol}] Short boundaries violation: "
                    f"SL({sl}) > EntryMax({entry_max}) >= EntryMin({entry_min}) > TP({tp_targets[0]})"
                )

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
            avg_conf = (signal_1.get("confidence_score", 0) + signal_2.get("confidence_score", 0)) // 2
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

            if max_conf >= 7:
                # One call is highly confident — trust it (reduce score slightly for uncertainty)
                if conf_1 >= conf_2:
                    final_signal = signal_1
                    final_report = report_1
                else:
                    final_signal = signal_2
                    final_report = report_2
                final_signal["confidence_score"] = max_conf - 1  # Penalize 1 point for lack of consensus
                final_signal["consensus"] = False
                # Sync the report's 合计 row to match the penalized score
                final_report = re.sub(r"(合计[^\d]*)\d+(\s*/\s*12)", rf"\g<1>{max_conf - 1}\2", final_report)
                logger.info(
                    f"[Consensus] DISAGREEMENT but high-confidence override: "
                    f"call1={sig_type_1}(conf={conf_1}) vs call2={sig_type_2}(conf={conf_2}) "
                    f"→ using {final_signal['signal_type']} with conf={final_signal['confidence_score']}"
                )
                return final_signal, final_report

            # Both calls low confidence → force wait
            logger.info(
                f"[Consensus] DISAGREEMENT: call1={sig_type_1}(conf={conf_1}) vs call2={sig_type_2}(conf={conf_2}) "
                f"→ both below 7, forcing wait. Ambiguous market conditions."
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
