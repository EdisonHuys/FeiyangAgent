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
    the built-in Feiyang default. The agent is constructed fresh for every
    analysis, so edits from the UI take effect on the next diagnosis
    without any restart.
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
    def __init__(self, api_key, api_base, model_name="gpt-4o", temperature=0.1, max_tokens=3000, system_prompt=None):
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
        logger.info(f"FeiyangAgent initialized with model: {model_name}, endpoint: {api_base}")

    DEFAULT_SYSTEM_PROMPT = """
你是一个精通加密货币量化与技术面分析的顶级专业AI智能体。你将严格扮演币圈知名分析师“飞扬”的角色，对输入的币种多周期行情数据进行精准深度诊断与信号输出。

【交易哲学与核心人设】
- 人设：成熟稳重、防守型右侧交易者，语气江湖气、接地气，对散户充满保护欲，常用“兄弟们”开头，坚决反对盲目追涨杀跌。
- 口头禅：“别急着追”、“老实等待点位”、“逢高做空/逢低做多”、“利润保护”、“君子不立危墙之下”、“到了关键位平一半”。
- 核心策略：低多与高空双向并重！重结构、重关键位回踩与反弹受阻点，严格遵守“无风险保本防守”与“高盈亏比”。

【思考链 (Chain of Thought, CoT) 低多高空精准推演 5 步法】
接收到 JSON 数据后，你必须按以下硬性定量逻辑依次推演：

1. 宏观定调与多空双向主线 (1M / 1W / 1D)：
   - 查看 1M 与 1W 相对 EMA55 及布林带中轨 (BB_Middle) 的位置。
   - 【低多机会 (Low Long)】：在大周期多头回调（回踩 1D/4H EMA55 或 斐波那契 0.5/0.618 支撑）或大周期震荡超跌（1H/4H RSI < 35 / KDJ_J < 15 底背离）时，精准寻找低多埋伏点。
   - 【高空机会 (High Short)】：在大周期空头反弹（受阻于 1D/4H EMA55 或 斐波那契 0.382/0.618 阻力）或大周期震荡超买（1H/4H RSI > 65 / KDJ_J > 85 顶背离）时，精准寻找高空埋伏点。

2. 乖离率与反转点位诊断 (1D / 4H / 1H)：
   - 严禁在价格远离均线时追单：若 4H 价格正偏离过大（远高于 MA5/MA10），绝对不追多，只等待高空或回踩；若负偏离过大（远低于 MA5/MA10），绝对不追空，只等待低多或反弹。

3. 动能背离与关键位共振 (精准挂单点)：
   - 【底背离共振 (Long)】：价格回踩近 14/30 天斐波那契 0.5 / 0.618 / 0.786 支撑位、4H EMA55 或布林带下轨，且 1H/4H MACD 柱状图或 RSI 出现底背离上升。
     * 埋伏区间 [min, max] 精准锁定在：`支撑位` 附近 ±(0.25 * 4H_ATR)。
   - 【顶背离共振 (Short)】：价格反弹触及近 14/30 天斐波那契 0.382 / 0.618 / 1.272 阻力位、4H EMA55 或布林带上轨，且 1H/4H MACD 柱状图或 RSI 出现顶背离衰竭。
     * 埋伏区间 [min, max] 精准锁定在：`阻力位` 附近 ±(0.25 * 4H_ATR)。

4. 盈亏比 (R:R >= 1.5) 与 ATR 动态风控硬计算：
   - 止损计算：多单止损设为 `支撑位 - (0.8 * 4H_ATR)`；空单止损设为 `阻力位 + (0.8 * 4H_ATR)`。
   - 盈亏比计算：设平均入场价 Entry = (min + max) / 2。
     * 多单盈亏比 = (TP1 - Entry) / (Entry - StopLoss)
     * 空单盈亏比 = (Entry - TP1) / (StopLoss - Entry)
   - 规则：如果盈亏比 >= 1.5 且存在明确共振，给予 8~9 分高评分并输出 "long" 或 "short"；若盈亏比 < 1.5 或无法确定关键支撑/阻力，输出 "wait"。

5. 综合确定信号与分层目标：
   - 根据上述推演输出最精准的入场区间 [min, max]，第一止盈位 TP1 (平50%仓位推保本) 与第二止盈位 TP2。

【输出格式要求】
必须先输出 ```json ... ``` 包裹的数据块，空一行后再输出 Markdown 格式的飞扬口吻报告。

第一部分：机器解析层 (JSON Format)
必须在输出的最顶部输出被 ```json ... ``` 包裹的数据块：
```json
{
  "symbol": "BTC/USDT",
  "timestamp": "YYYY-MM-DD HH:MM:SS",
  "signal_type": "long", // 严格限制为 "long", "short", 或 "wait"
  "confidence_score": 8, // 1-10 评分 (低于7分必须输出 wait)
  "entry_zone": {
    "min": 62500.00,
    "max": 63000.00
  },
  "take_profit_targets": [
    64500.00,
    66000.00
  ],
  "stop_loss": 61800.00,
  "risk_reward_ratio": 2.1, // 计算出的真实盈亏比
  "core_reason": "4H级别回踩EMA55，叠加斐波那契0.618强支撑，MACD柱状图底背离，盈亏比达2.1。"
}
```
*逻辑校验硬性规则*：
- 若为 long：必须满足 stop_loss < entry_zone.min <= entry_zone.max < take_profit_targets[0] < take_profit_targets[1]
- 若为 short：必须满足 stop_loss > entry_zone.max >= entry_zone.min > take_profit_targets[0] > take_profit_targets[1]
- 若为 wait：entry_zone 的 min/max、take_profit_targets、stop_loss 均填 0，risk_reward_ratio 填 0。

第二部分：人类阅读层 (Markdown Format)
在 JSON 块之后空一行，输出以飞扬口吻编写的分析报告。

### 🚨 飞扬盯盘警报：[SYMBOL] (当前价格: $[CURRENT_PRICE])

**🔍 盘面诊断**：
[接地气、犀利分析。重点剖析宏观趋势、乖离率情况、MACD/RSI背离信号以及斐波那契共振点]

**🎯 飞扬交易逻辑**：
*   **信号方向**：[🎯 埋伏低多 / ⚡ 高空埋伏 / ☕ 观望静待] (置信度评分: X/10, 预期盈亏比: X:1)
*   **埋伏区间**：$[MIN] - $[MAX]（指出具体的斐波那契与均线共振支撑/阻力点）
*   **防守底线（止损）**：$[STOP_LOSS] (结合 ATR 动态安全垫缓冲，跌破/突破必须认错离场)
*   **止盈目标**：第一目标 $[TP1] (平仓50%锁定利润并推保本) | 第二目标 $[TP2]
*   **飞扬叮嘱**：[结合当前盘面写一句接地气的风控寄语，比如“市场永远不缺机会，只缺本金，到了目标位必须推保本！”]
"""

    def get_system_prompt(self):
        """Return the active system prompt (custom override or built-in default)."""
        return self._system_prompt or self.DEFAULT_SYSTEM_PROMPT

    def analyze(self, payload):
        """
        Send compressed market data payload to LLM and parse results.
        Retries once with a corrective instruction if the model emits an
        unparseable signal block (a common cause of wasted hourly cycles).
        """
        system_prompt = self.get_system_prompt()
        user_prompt = json.dumps(payload, indent=2, ensure_ascii=False)

        logger.info("Sending request to LLM...")
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"当前市场精简数据 Payload JSON 如下：\n{user_prompt}"}
        ]

        last_parse_error = None
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

                # Parse response
                json_signal, markdown_report = self._parse_response(content, payload.get("current_price"))
                return json_signal, markdown_report

            except ValueError as e:
                # JSON extraction/validation failure -> retry once with guidance
                last_parse_error = e
                logger.warning(f"LLM output parse failed (attempt {attempt + 1}/2): {e}")
                messages.append({
                    "role": "user",
                    "content": "你上一条输出无法被解析为合法 JSON 信号块。请严格按格式重新输出：顶部 ```json 数据块 + 空行 + Markdown 报告，不要输出任何其他多余内容。"
                })
            except Exception as e:
                logger.error(f"Error during LLM inference: {e}")
                raise e

        raise ValueError(f"LLM 连续两次输出均无法解析为有效交易信号：{last_parse_error}")

    def _parse_response(self, text, current_price):
        """
        Extract JSON block and Markdown text. Perform logical checks.
        """
        clean_text = text.strip()
        json_signal = None
        markdown_part = ""

        # Helper to clean single line JS comments: // ...
        def sanitize_json_str(s):
            return re.sub(r"//.*?\n", "\n", s)

        # Method 1: Match ```json ... ``` or ``` ... ``` codeblock
        codeblock_pattern = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.IGNORECASE | re.DOTALL)
        match = codeblock_pattern.search(clean_text)

        if match:
            candidate = sanitize_json_str(match.group(1).strip())
            try:
                json_signal = json.loads(candidate)
                markdown_part = clean_text[match.end():].strip()
            except Exception as e:
                logger.warning(f"Codeblock JSON parse failed: {e}. Falling back to outer brace search.")

        # Method 2: Outer brace search if codeblock failed or missing
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
                    raise ValueError(f"无法解析 LLM 的 JSON 诊断数据块（{e}）。原始响应开头：{clean_text[:150]}")
            else:
                raise ValueError(f"LLM 响应中未查找到被 ```json``` 或 {{...}} 包裹的数据块。")

        # Perform logical checks
        self._validate_signal(json_signal, current_price)
        
        return json_signal, markdown_part

    def _validate_signal(self, signal, current_price):
        """
        Validate the logic bounds of the JSON signal for both Long and Short.
        """
        signal_type = signal.get("signal_type")
        symbol = signal.get("symbol")
        
        if signal_type == "long":
            entry_zone = signal.get("entry_zone", {}) or {}
            entry_min = entry_zone.get("min")
            entry_max = entry_zone.get("max")
            tp_targets = signal.get("take_profit_targets", []) or []
            sl = signal.get("stop_loss")
            
            if None in [entry_min, entry_max, sl] or not tp_targets:
                logger.warning(f"[{symbol}] Long signal has null trade boundaries: entry={entry_zone}, tp={tp_targets}, sl={sl}")
                return
                
            is_valid = (sl < entry_min) and (entry_min <= entry_max) and (tp_targets[0] > entry_max)
            if not is_valid:
                logger.warning(
                    f"[{symbol}] Long trade boundaries violation: "
                    f"SL({sl}) < EntryMin({entry_min}) <= EntryMax({entry_max}) < TP({tp_targets[0]})"
                )
                
        elif signal_type == "short":
            entry_zone = signal.get("entry_zone", {}) or {}
            entry_min = entry_zone.get("min")
            entry_max = entry_zone.get("max")
            tp_targets = signal.get("take_profit_targets", []) or []
            sl = signal.get("stop_loss")
            
            if None in [entry_min, entry_max, sl] or not tp_targets:
                logger.warning(f"[{symbol}] Short signal has null trade boundaries: entry={entry_zone}, tp={tp_targets}, sl={sl}")
                return
                
            is_valid = (sl > entry_max) and (entry_max >= entry_min) and (tp_targets[0] < entry_min)
            if not is_valid:
                logger.warning(
                    f"[{symbol}] Short trade boundaries violation: "
                    f"SL({sl}) > EntryMax({entry_max}) >= EntryMin({entry_min}) > TP({tp_targets[0]})"
                )
