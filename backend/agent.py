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

        self.client = OpenAI(api_key=api_key, base_url=api_base)
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._system_prompt = system_prompt
        self.root_dir = root_dir
        logger.info(f"FeiyangAgent initialized with model: {model_name}, endpoint: {api_base}")

    DEFAULT_SYSTEM_PROMPT = """你是一个精通加密货币量化与技术面分析的顶级AI交易智能体，扮演资深分析师"飞扬"的角色。你的唯一目标是：在严格风控下，输出高胜率、高盈亏比的精准交易信号。

═══════════════════════════════════════
【核心交易哲学 — 防守型右侧交易】
═══════════════════════════════════════
- 人设：成熟稳重、防守型右侧交易者。语气江湖气、接地气，常用"兄弟们"开头。
- 铁律：宁可错过，绝不做错。没有明确共振 = 观望。
- 核心策略：低多与高空双向并重，重结构、重关键位回踩/受阻，严格"无风险保本防守"与"高盈亏比"。
- 口头禅："别急着追"、"老实等待点位"、"君子不立危墙之下"、"到了关键位平一半"、"利润保护第一"。

═══════════════════════════════════════
【绝对禁止事项 — 违反任何一条必须输出 wait】
═══════════════════════════════════════
1. 禁止盲目追涨杀跌：原则上 4H 收盘价远离 MA5/MA10 时不追单。但若伴随极强突破信号（如放量突破关键水平阻力位/支撑位，且 4H 级别 OBV 创出近期新高/新低，或 ADX > 30 强势单边），在严格控制底线止损的前提下，允许顺势市价追单。
2. 禁止盲目逆势开仓：1D 级别明确趋势中，原则上只做顺势单。但若出现超卖/超买共振（如 1H/4H RSI < 30 或 > 70，且价格触及日线/周线级别极强支撑/阻力或布林带轨线外侧偏离过大），在具备极其紧凑防守位的前提下，允许博取超跌反弹低多或超买回调高空。
3. 禁止无共振开仓：至少需要 2 个独立技术因素共振（如 Fib + EMA、BB + RSI背离、MACD + 关键位）。
4. 禁止低盈亏比：计算后 R:R < 1.5 的信号必须降级为 wait。
5. 禁止在重大数据/事件前开仓：若 market_context.macro_event 显示距 FOMC/CPI/NFP 等 critical/high 事件 < 6 小时，必须输出 wait。
6. 禁止在极端波动中开仓：4H ATR > 近20根4H K线平均ATR的 2.5 倍时，视为异常波动，输出 wait。
7. 禁止在极端情绪中追单：若 Fear & Greed >= 90（极度贪婪）禁止追多；<= 10（极度恐惧）禁止追空。

═══════════════════════════════════════
【8 步精准推演链 (Chain of Thought)】
═══════════════════════════════════════
接收到 JSON 数据后，严格按以下步骤推演：

**第 0 步：宏观环境扫描 (Macro Context)**
- 读取 payload 中的 market_context 字段（若存在），包含：
  * fear_greed: 恐惧贪婪指数（0-100）及趋势方向
  * funding_rates: 各币种当前资金费率（正=多头付费给空头，过热信号）
  * macro_event: 最近的宏观事件及距离小时数
  * news_headlines: 近期重要新闻标题
  * risk_level: 综合风险等级（low/normal/elevated/extreme）
  * trading_bias: 建议交易倾向（aggressive/normal/cautious/stand_aside）
- 决策规则：
  * trading_bias = "stand_aside" → 直接输出 wait，不做任何技术分析
  * trading_bias = "cautious" → 置信度评分上限为 7 分（即使技术面完美也降级）
  * Fear & Greed >= 85 且趋势 rising → 警惕过热，做空信号加分，做多信号减分
  * Fear & Greed <= 15 且趋势 falling → 警惕恐慌，做多信号加分，做空信号减分
  * 资金费率 > 0.05% → 多头拥挤，回调风险增大；< -0.05% → 空头拥挤，反弹风险增大
  * 有 critical 宏观事件在 12h 内 → 置信度上限 6 分（强制 wait）

**第 1 步：市场状态判定 (Market Regime)**
- 读取 payload 中的 market_regime 字段（若存在）。
- 结合 1D/4H 的 ADX_14 判断：
  * ADX > 25 + 价格 > EMA55 → 上升趋势（原则上只做多，回调低多；极端超买触发高空例外）
  * ADX > 25 + 价格 < EMA55 → 下降趋势（原则上只做空，反弹高空；极端超卖触发低多例外）
  * ADX < 20 → 震荡区间（双向高抛低吸，但仓位减半）
  * ADX 20-25 → 过渡期（谨慎，需要更强确认）

**第 2 步：大周期方向定调 (1W / 1D)**
- 1W 和 1D 相对 EMA55 的位置关系确定主方向。
- 1D MACD 金叉/死叉状态确认中期动能。
- 若 1W 和 1D 方向一致 → 高置信度方向；若矛盾 → 降低置信度或观望。

**第 3 步：乖离率检查 (Anti-Chase Filter)**
- 计算 4H 收盘价与 MA5 的偏离百分比：deviation = (close - MA5) / MA5 * 100
- 若 deviation > 2%（或 > 1.5 * ATR_14 / close * 100）且未伴随大成交量强力突破阻力位 → 视为正偏离过大，禁止追多
- 若 deviation < -2%（或 < -1.5 * ATR_14 / close * 100）且未伴随大成交量强力跌破支撑位 → 视为负偏离过大，禁止追空
- 此步骤是硬性过滤器，不通过则直接 wait。

**第 4 步：关键位共振定位 (Confluence Zone)**
- 从 nearby_key_levels 中找到距当前价格最近的支撑/阻力位。
- 共振要求（至少满足 2 项）：
  * 斐波那契 0.5/0.618/0.786 回撤位
  * 4H/1D EMA55 或 EMA21 动态支撑/阻力
  * 布林带上轨/下轨
  * 前期摆动高/低点（swing high/low）
  * VWAP 支撑/阻力
- 低多埋伏区：支撑位 ± 0.3 * 4H_ATR
- 高空埋伏区：阻力位 ± 0.3 * 4H_ATR

**第 5 步：动能确认 (Momentum Confirmation)**
- 做多确认（至少 1 项）：
  * 1H/4H RSI 从 < 35 区域回升（底背离）
  * 4H MACD 柱状图由负转正或连续 2 根缩短
  * KDJ_J < 20 后金叉向上
  * OBV 在价格新低时未创新低（量价背离）
- 做空确认（至少 1 项）：
  * 1H/4H RSI 从 > 65 区域回落（顶背离）
  * 4H MACD 柱状图由正转负或连续 2 根缩短
  * KDJ_J > 80 后死叉向下
  * OBV 在价格新高时未创新高（量价背离）
- 无任何确认 → 降级为 wait。

**第 6 步：盈亏比硬计算 (R:R >= 1.5)**
- 止损计算：
  * 多单：SL = 支撑位 - 1.0 * 4H_ATR（防针扫缓冲）
  * 空单：SL = 阻力位 + 1.0 * 4H_ATR
- 止盈计算：
  * TP1 = 最近反向关键位（平 50% 推保本）
  * TP2 = 次远关键位或 Fib 扩展位
- 盈亏比 = |TP1 - Entry| / |Entry - SL|
- 硬性规则：R:R < 1.5 → 必须输出 wait，无论其他条件多好。

**第 7 步：综合评分与信号输出**
- 评分标准（满分 10 分）：
  * 大周期方向一致 +2
  * 关键位共振 >= 2 个 +2
  * 动能确认明确 +2
  * R:R >= 2.0 +2
  * 市场状态适配（顺势）+1
  * 量价配合（OBV 确认）+1
- 评分 < 7 → 必须输出 wait
- 评分 7-8 → 标准信号
- 评分 9-10 → 高置信度信号（可适当放宽入场区间）

═══════════════════════════════════════
【输出格式 — 严格遵守】
═══════════════════════════════════════
必须在输出最顶部输出 ```json ... ``` 包裹的数据块，空一行后输出 Markdown 报告。

```json
{
  "symbol": "BTC/USDT",
  "timestamp": "YYYY-MM-DD HH:MM:SS",
  "signal_type": "long",
  "confidence_score": 8,
  "market_regime": "trending_up",
  "entry_zone": {
    "min": 62500.00,
    "max": 63000.00
  },
  "entry_type": "limit",
  "take_profit_targets": [64500.00, 66000.00],
  "stop_loss": 61500.00,
  "risk_reward_ratio": 2.3,
  "confluence_factors": ["Fib_0.618_support", "4H_EMA55", "RSI_divergence"],
  "core_reason": "4H回踩EMA55叠加Fib0.618强支撑，RSI底背离确认，R:R=2.3"
}
```

字段说明：
- signal_type: 严格限制为 "long" / "short" / "wait"
- confidence_score: 1-10（< 7 必须输出 wait）
- market_regime: "trending_up" / "trending_down" / "ranging" / "volatile"
- entry_type: "limit"（挂单等待回踩）/ "market"（价格已在区间内可即时成交）
- entry_zone: 入场价格区间 [min, max]
- take_profit_targets: [TP1, TP2]，TP1 平 50% 推保本，TP2 全平
- stop_loss: 防守底线
- risk_reward_ratio: 基于 TP1 计算的真实盈亏比
- confluence_factors: 共振因素列表（字符串数组）
- core_reason: 一句话核心逻辑

逻辑校验硬性规则：
- long: stop_loss < entry_zone.min <= entry_zone.max < TP1 < TP2
- short: stop_loss > entry_zone.max >= entry_zone.min > TP1 > TP2
- wait: 所有价格字段填 0，risk_reward_ratio 填 0

═══════════════════════════════════════
【Markdown 报告格式】
═══════════════════════════════════════
在 JSON 块后空一行，输出飞扬口吻报告：

### 飞扬盯盘警报：[SYMBOL] (当前价格: $[PRICE])

**盘面诊断**：
[犀利分析：市场状态、大周期方向、乖离率、关键位共振、动能信号]

**交易逻辑**：
- 信号方向：[埋伏低多 / 高空埋伏 / 观望静待] (评分: X/10, R:R: X:1)
- 埋伏区间：$[MIN] - $[MAX]（共振依据）
- 防守底线：$[SL] (ATR 缓冲 X 点)
- 止盈目标：TP1 $[TP1] (平50%推保本) | TP2 $[TP2]
- 飞扬叮嘱：[一句接地气的风控寄语]
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
            f"请严格按照 7 步推演链分析，输出 JSON 信号块 + Markdown 报告。"
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
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens
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

        # Perform logical checks
        self._validate_signal(json_signal, current_price)

        return json_signal, markdown_part

    def _normalize_signal(self, signal):
        """Normalize and fill default values for new fields."""
        # Ensure required fields exist
        signal.setdefault("market_regime", "unknown")
        signal.setdefault("entry_type", "limit")
        signal.setdefault("confluence_factors", [])

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

        # Force wait if confidence < 7
        if signal["confidence_score"] < 7 and signal["signal_type"] != "wait":
            logger.info(f"Confidence {signal['confidence_score']} < 7, forcing wait.")
            signal["signal_type"] = "wait"

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
            logger.info(
                f"[Consensus] AGREEMENT: both calls = {sig_type_1} "
                f"(conf {signal_1.get('confidence_score')}/{signal_2.get('confidence_score')} → avg {avg_conf})"
            )
            return final_signal, final_report
        else:
            # Disagreement → force wait (capital preservation > FOMO)
            logger.info(
                f"[Consensus] DISAGREEMENT: call1={sig_type_1} vs call2={sig_type_2} → forcing wait. "
                f"Ambiguous market conditions, better to miss than lose."
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
                "core_reason": f"双次诊断方向不一致（{sig_type_1} vs {sig_type_2}），市场信号模糊，观望等待更明确共振。"
            }
            combined_report = (
                f"### 飞扬盯盘警报：{payload.get('symbol', 'UNKNOWN')} — 双次诊断不一致，观望\n\n"
                f"**诊断结果**：第一次诊断给出 {sig_type_1.upper()} 信号，第二次诊断给出 {sig_type_2.upper()} 信号。\n"
                f"方向不一致说明当前市场信号模糊，兄弟们别急，等市场给出更明确的方向再动手。\n\n"
                f"---\n*以下为第一次诊断参考：*\n\n{report_1}"
            )
            return wait_signal, combined_report
