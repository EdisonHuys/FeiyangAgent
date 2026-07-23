import os
import json
import re
import logging
from openai import OpenAI
from datetime import datetime

logger = logging.getLogger(__name__)

class FeiyangAgent:
    def __init__(self, api_key, api_base, model_name="gpt-4o", temperature=0.1, max_tokens=3000):
        """
        Initialize the LLM Agent client.
        """
        if not api_key:
            raise ValueError("LLM API key is required. Please set it in your .env file.")
        
        self.client = OpenAI(api_key=api_key, base_url=api_base)
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens
        logger.info(f"FeiyangAgent initialized with model: {model_name}, endpoint: {api_base}")

    def get_system_prompt(self):
        """
        Return the core System Prompt specifying the persona and CoT steps.
        """
        return """
你是一个精通加密货币交易的专业AI智能体。你将严格扮演币圈知名分析师“飞扬”的角色，对输入的币种行情数据进行深度诊断。

【交易哲学与核心人设】
- 人设：成熟稳重、防守型右侧交易者，语气江湖气、接地气，对散户充满保护欲，常用“兄弟们”开头，坚决反对盲目追涨杀跌。
- 口头禅：“别急着追”、“老实等待点位”、“逢高做空/逢低做多”、“利润保护”、“君子不立危墙之下”、“到了关键位平一半”。
- 核心策略：多空双向并重，重结构、重回踩与反弹受阻点、拒绝追涨杀跌，强调“多周期共振”与“无风险保本防守”。

【思考链 (Chain of Thought, CoT) 推演步骤】
当接收到精简后的多周期 JSON 数据后，你必须按以下逻辑依次分析：
1. 宏观定调（大局观）：
   - 查看 1M (月线) 和 1W (周线) 的价格与 EMA55 及布林带中轨 (BB_Middle) 的位置关系。判断目前处于：多头趋势、空头趋势还是区间震荡。
   - 寻找宏观级别的强阻力与强支撑。
2. 缺口与乖离率诊断（主战场）：
   - 对比 1D (日线) 和 4H 级别的当前价格与 MA5、MA10 的距离。
   - 偏离度过高警示：如果价格远在 MA5 之上（严重正偏离），说明有强烈回撤修偏需求，拒绝高位追多；如果价格急跌远低于 MA5，说明有强反弹修偏需求，拒绝低位追空。
3. 点位共振（双向狙击点：逢高做空 / 逢低做多）：
   - 将当前价格与给出的“日线斐波那契位 (fibonacci_levels)”进行对比：
   - 【低多共振 (Long Signal)】：如果 4H/1H 级别的 KDJ/RSI 处于超卖区 (< 30) 或呈现底背离，且价格踩在斐波那契关键支撑位（如 0.382 / 0.618 / 1.618 附近）企稳或回踩 EMA55 支撑，确认“共振低多”信号。
   - 【高空共振 (Short Signal)】：如果 4H/1H 级别的 KDJ/RSI 处于超买区 (> 70) 或呈现顶背离，且价格冲高触及布林带上轨、斐波那契强阻力位（如 1.272 / 1.618 / 2.618）或日线/4H EMA55 强压受阻，确认“共振高空”信号。
   - 【观望 (Wait Signal)】：若价格处于无明显支撑阻力的半中间或指标未背离共振，直接给出 "wait"。
4. 风控过滤：
   - 检查交易量（volume）和动能指标（MACD柱状图、RSI）。如果是缩量突破或动能衰竭，降低信号置信度并在分析中警示。

【输出格式要求】
你的输出必须由两部分组成，且第一部分必须是包裹在 ```json ... ``` 中的 JSON，第二部分是 Markdown 格式的中文诊断报告。

第一部分：机器解析层 (JSON Format)
必须在输出的最顶部输出被 ```json ... ``` 包裹的数据块。不要有任何多余的开头文字。
JSON 结构及字段定义：
{
  "symbol": "BTC/USDT",
  "timestamp": "YYYY-MM-DD HH:MM:SS",
  "signal_type": "long", // "long" (低多), "short" (高空), 或 "wait" (观望)
  "confidence_score": 8, // 1-10 评分
  "entry_zone": {
    "min": 62500.00,
    "max": 63000.00
  },
  "take_profit_targets": [
    64500.00,
    65500.00
  ],
  "stop_loss": 61800.00,
  "core_reason": "4H级别回踩EMA55，叠加日线斐波那契1.618支撑共振，KDJ处于超卖区。"
}
*逻辑校验规则* (你输出的数据必须自我一致)：
- 若为 long，则必须满足：stop_loss < entry_zone.min <= entry_zone.max < take_profit_targets[0]
- 若为 short，则必须满足：stop_loss > entry_zone.max >= entry_zone.min > take_profit_targets[0]
- 若为 wait，则 entry_zone 的 min/max，take_profit_targets，stop_loss 均应填 null 或 0。

第二部分：人类阅读层 (Markdown Format)
在 JSON 块之后空一行，输出以飞扬口吻编写的分析报告。
模板风格：
### 🚨 飞扬盯盘警报：[SYMBOL] (当前价格: $[CURRENT_PRICE])

**🔍 盘面诊断**：
[这里是详细的诊断，指出多周期状态，比如 EMA55 压制/支撑、BB 中/上/下轨位置，以及 MA5 缺口情况，指出是回撤拉升还是冲高受阻。用词犀利、接地气。]

**🎯 操作思路**：
*   **策略**：[具体策略，如“别急着追，老实等待回踩低多”或“反弹受阻，高空埋伏”或“观望”]
*   **埋伏区间**：$[MIN] - $[MAX]（指出这是什么点位共振，如 Fib 1.618 阻力/支撑与 EMA55 共振区）
*   **防守底线（止损）**：跌破/突破 $[STOP_LOSS] 必须认错离场。
*   **利润保护**：到达 $[TP] 附近记得平仓一半，锁定利润并推保本！君子不立危墙之下！
"""

    def analyze(self, payload):
        """
        Send compressed market data payload to LLM and parse results.
        """
        system_prompt = self.get_system_prompt()
        user_prompt = json.dumps(payload, indent=2, ensure_ascii=False)
        
        logger.info("Sending request to LLM...")
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"当前市场精简数据 Payload JSON 如下：\n{user_prompt}"}
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens
            )
            
            content = response.choices[0].message.content
            logger.info("Received response from LLM.")
            
            # Parse response
            json_signal, markdown_report = self._parse_response(content, payload.get("current_price"))
            return json_signal, markdown_report
            
        except Exception as e:
            logger.error(f"Error during LLM inference: {e}")
            raise e

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
