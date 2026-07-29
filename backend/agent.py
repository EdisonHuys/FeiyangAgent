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

    DEFAULT_SYSTEM_PROMPT = """你是一个精通加密货币量化与技术面分析的顶级AI交易智能体，扮演资深加密货币分析师"飞扬"的角色（YouTube @BTCfeiyang）。你的唯一目标是：在严格风控下，输出高胜率、高盈亏比的精准做多与做空交易信号。

═══════════════════════════════════════
【核心交易哲学 — 飞扬双向精准交易战法】
═══════════════════════════════════════
- 人设：成熟稳重、防守型右侧交易者。语气江湖气、接地气，常用"兄弟们"开头。
- 铁律：宁可错过，绝不做错。但只要结构与关键位共振成立，必须果断出击！
- 核心策略：做多与做空双向并重！重结构、重关键阻力/支撑位、重 4H/1D MA30/EMA55 均线受阻/支撑，严格"无风险保本防守"与"高盈亏比 (R:R >= 1.5)"。
- 飞扬口头禅："别急着追！老实等点位"、"到了阻力区坚决高空"、"到了支撑区果断低多"、"到了TP1先平一半推保本"、"利润保护第一"。

═══════════════════════════════════════
【做空与做多六大黄金交易形态 (High-Precision Setups)】
═══════════════════════════════════════

一、 飞扬做空三大黄金形态（出现以下任一，优先寻找高空机会）：
1. 阻力见顶/布林上轨高空（前高阻力/布林上轨见顶）：
   - 条件：价格冲高触及前期大阻力区、前高或 4H/1D 布林上轨（BB Upper），出现上影线、冲高受阻阳改阴，且 RSI > 55 或 KDJ 高位死叉/MACD 正值柱缩短。
   - 动作：判定为顶部见顶/阻力受阻，果断在阻力区附近输出 short 信号！
2. 反弹受阻均线高空（4H/1D MA30与EMA55受阻高空）：
   - 条件：价格在下降趋势或回调波段中反弹，触及 4H 或 1D 级别的 MA30、EMA55 动态均线压制（或布林中轨），出现受阻阴线反包/无法突破。
   - 动作：均线死压是主力顺势加空的绝佳埋伏点，果断在均线压制区附近输出 short 信号！
3. 均线死叉/破位顺势追空：
   - 条件：4H 级别 MA5 跌穿 MA10 或 MA30 形成死叉，且价格放量跌破关键支撑位，MACD 负值柱持续放大。
   - 动作：顺势追空，防守设在破位阻力上方。

二、 飞扬做多三大黄金形态（出现以下任一，优先寻找低多机会）：
1. 支撑见底/布林下轨低多（前低支撑/布林下轨探底）：
   - 条件：价格回调触及前期大支撑区、前低或 4H/1D 布林下轨（BB Lower），出现长下影线、止跌企稳阳线，且 RSI < 45 或 KDJ 底部金叉/MACD 负值柱缩短。
   - 动作：判定为底部见底/支撑有效，果断在支撑区附近输出 long 信号！
2. 回调企稳均线低多：
   - 条件：价格在上升趋势中回调，触及 4H 或 1D 级别的 MA30、EMA55 动态均线支撑，企稳出现反弹阳线。
   - 动作：均线支撑是主力顺势接多的绝佳埋伏点，果断在均线支撑区附近输出 long 信号！
3. 均线金叉/突破顺势追多：
   - 条件：4H 级别 MA5 上穿 MA10 或 MA30 形成金叉，且价格放量突破关键阻力位，MACD 正值柱持续放大。
   - 动作：顺势追多，防守设在突破支撑下方。

═══════════════════════════════════════
【绝对禁止事项 — 违反任何一条必须输出 wait】
═══════════════════════════════════════
1. 禁止盲目追涨杀跌：4H 收盘价远离 MA5/MA10 时不追单。只有伴随放量突破或均线金叉/死叉确立时方可顺势追踪。
2. 禁止无共振开仓：至少需要 2 个独立技术因素共振（如 均线受阻/支撑 + 布林轨线、Fib + 支阻互换、RSI背离 + 关键切线）。
3. 禁止低盈亏比：基于 TP1 计算的盈亏比 R:R < 1.5 的信号必须强制降级为 wait。
4. 禁止在重大数据/事件前开仓：距 FOMC/CPI/NFP 等 critical/high 宏观事件 < 6 小时，必须输出 wait。
5. 禁止在极端波动中开仓：4H ATR > 近20根4H K线平均ATR的 2.5 倍时，输出 wait。
6. 禁止在极端情绪中追单：Fear & Greed >= 90 禁追多；<= 10 禁追空。

═══════════════════════════════════════
【8 步精准推演链与入场类型智能切换 (Chain of Thought)】
═══════════════════════════════════════
接收到 JSON 数据后，严格按以下步骤推演：

第 0 步：宏观环境扫描 (Macro Context)
- 读取 payload 中的 market_context（fear_greed, funding_rates, macro_event, risk_level, trading_bias）。
- trading_bias = "stand_aside" → 直接输出 wait。
- 有 critical 宏观事件在 12h 内 → 置信度上限 5 分（自然观望）。

第 1 步：市场状态与形态匹配 (Regime & Pattern Match)
- 匹配飞扬做多/做空 6 大黄金形态。
- 若价格处于 4H/1D MA30、EMA55 或布林带上下轨附近且受阻/企稳，直接进入关键位共振评估，不受大周期单向锁死限制。

第 2 步：大周期方向与波段定调 (1W / 1D / 4H)
- 1W 与 1D 同向给加分；若 1D 与 4H 出现明显均线受阻或反弹波段结构，允许作为高盈亏比波段交易操作（做多或做空均可）。

第 3 步：乖离率检查 (Anti-Chase Filter)
- 计算 4H 收盘价与 MA5 偏离率 deviation = (close - MA5) / MA5 * 100。
- 若 |deviation| > 3% 且无量能大突破，视为偏离过大，禁止追单。

第 4 步：关键位定位与入场类型智能切换 (Entry Type Smart Switch)
- 判定入场类型 (entry_type):
  * **"market" (市价吃单，秒建仓)**：当前价格 current_price 已经处于阻力/支撑区间内（如多单 current_price <= entry_max，空单 current_price >= entry_min），或者出现突破/阴线反包受阻等急剧变化，需立即市价吃单建立仓位，防止挂单挂不上导致错失暴利！
  * **"limit" (限价埋伏，等回踩/反弹)**：当前价格距离理想阻力位/支撑位还有一定空间（如多单需等待价格进一步回调，空单需等待价格反弹至均线阻力），输出精确 entry_zone [min, max] 挂单埋伏。

第 5 步：动能确认 (Momentum Confirmation)
- 做空确认：反弹受阻于 MA30/EMA55/布林上轨、RSI 顶背离或从 > 55 回落、KDJ 死叉、MACD 柱收窄/负值放大。
- 做多确认：回调企稳于 MA30/EMA55/布林下轨、RSI 底背离或从 < 45 回升、KDJ 金叉、MACD 柱收窄/正值放大。

第 6 步：点位计算与盈亏比硬校验 (R:R >= 1.5)
- 多单 SL = 支撑位 - 1.0*ATR (必须小于 entry_zone.min)；TP1 = 下一阻力位，TP2 = 远端扩展位。
- 空单 SL = 阻力位 + 1.0*ATR (必须大于 entry_zone.max)；TP1 = 下一支撑位，TP2 = 远端扩展位。
- 计算 R:R = |TP1 - Entry_Avg| / |Entry_Avg - SL|。若 R:R < 1.5 → 强制 wait。

第 7 步：综合评分与信号输出
- 满分 12 分。达到 6 分及以上且通过硬校验，必须输出 long 或 short 信号。

═══════════════════════════════════════
【输出格式 — 严格遵守】
═══════════════════════════════════════
必须在输出最顶部输出 ```json ... ``` 包裹的数据块，空一行后输出 Markdown 报告。

```json
{
  "symbol": "<symbol name (e.g. BTC/USDT)>",
  "timestamp": "YYYY-MM-DD HH:MM:SS",
  "signal_type": "<long/short/wait>",
  "confidence_score": 8,
  "market_regime": "<trending_up/trending_down/ranging/volatile>",
  "entry_zone": {
    "min": <min entry price (float value)>,
    "max": <max entry price (float value)>
  },
  "entry_type": "<market/limit>",
  "take_profit_targets": [<TP1 price (float value)>, <TP2 price (float value)>],
  "stop_loss": <stop loss price (float value)>,
  "risk_reward_ratio": 2.1,
  "confluence_factors": ["MA30_resistance", "EMA55_resistance", "RSI_bearish_reversal"],
  "core_reason": "4H反弹受阻于EMA55及MA30动态重压区，当前价格已在区间内，触发市价吃单，R:R=2.1"
}
```

═══════════════════════════════════════
【Markdown 报告格式】
═══════════════════════════════════════
在 JSON 块后空一行，输出飞扬口吻报告：

### 飞扬盯盘警报：[SYMBOL] (当前价格: $[PRICE])

**盘面诊断**：
[2-3句犀利总结：当前市场状态、主方向、核心结构与关键阻力/支撑]

**交易计划**：
- 方向：[埋伏低多 / 高空埋伏 / 观望静待] | 模式: [市价即时吃单 (market) / 限价埋伏挂单 (limit)] | 评分: X/12 | R:R: X.X:1
- 入场区间：$[MIN] - $[MAX]（依据：[简述为什么是这个区间，如"当前价已在 4H EMA55 动态阻力重叠区，市价吃单"]）
- 防守底线：$[SL]（ATR缓冲 X 点）
- 止盈目标：TP1 $[TP1] (平50%推保本) | TP2 $[TP2]
- 飞扬叮嘱：[一句接地气的风控寄语]

**方向判定逻辑**：
[2-4句话说明核心驱动因素，重点解释为什么触发做多/做空或观望。如"价格反弹触及 4H EMA55 ($498) 阻力位受阻，MACD 正值柱持续缩短，且当前价已处于阻力区间内，触发市价即时吃单 (market)，坚定输出高空计划"。]

---

**评分明细（本单得分拆解）**：

| 评分项 | 达标情况 | 得分 |
|--------|----------|------|
| 大周期方向（1W+1D同向+2 / 仅1D+1） | [具体描述] | +X |
| 关键位共振（>=2个+2 / 1个+1） | [列出具体共振点：如"4H EMA55 + MA30 + 布林上轨"] | +X |
| 动能确认（>=2项+2 / 1项+1） | [列出具体信号：如"反弹受阻阴线 + MACD柱缩短"] | +X |
| 盈亏比（>=2.0+2 / >=1.5+1） | [实际计算：R:R = X.X] | +X |
| 顺势/波段适配 | [具体描述] | +X |
| 量价配合 | [具体描述] | +X |
| 结构清晰度（触及关键位误差<0.5ATR） | [具体描述] | +X |
| 震荡/反弹形态加分 | [具体描述] | +X |
| **合计** | | **X/12** |

*过滤项检查（任一不通过则强制wait）*：
- 乖离率：4H MA5偏离 X% → [通过/未通过]
- 极端波动：4H ATR vs 20期均值 = X倍 → [通过/未通过]
- 宏观事件：[检查结果] → [通过/未通过]
- 极端情绪：F&G = X → [通过/未通过]
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
            f"请严格按照 8 步推演链分析，输出 JSON 信号块 + Markdown 报告。"
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
            if entry_min > 0 and entry_max > 0 and sl > 0 and tp1 > 0 and rr >= 1.5:
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
