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
1. 禁止盲目追涨杀跌：原则上 4H 收盘价远离 MA5/MA10 时不追单。但若满足以下任一条件，在严格控制底线止损（SL 设在关键支撑或阻力之外）的前提下，允许追单：
   - 强力突破：伴随极强突破信号（如放量突破关键水平阻力位/支撑位，且 4H 级别 OBV 创出近期新高/新低，或 ADX > 30 强势单边）。
   - 均线金叉/站稳反弹追多（合理追多）：当 4H 收盘价从布林下轨附近向上突破且站稳 MA5 与 MA10（或者 4H 级别 MA5 金叉 MA10），且 MACD 负值柱状图持续收窄，只要此时距离上方关键阻力位有足够的盈亏空间（R:R >= 1.5），允许作为 4H 级别底部右侧确认信号进行『合理追多』。
   - 均线死叉/跌破回调追空（合理追空）：当 4H 收盘价从布林上轨附近向下跌破且跌穿 MA5 与 MA10（或者 4H 级别 MA5 死叉 MA10），且 MACD 正值柱状图持续收窄，只要此时距离下方关键支撑位有足够的盈亏空间（R:R >= 1.5），允许作为 4H 级别顶部右侧确认信号进行『合理追空』。
2. 禁止盲目逆势开仓：1D 级别明确趋势中，原则上只做顺势单。但若满足以下任一超买/超卖反弹条件，在具备极其紧凑防守位的前提下，允许博取超跌反弹低多或超买回调高空：
   - 极端超买/超卖共振：1H/4H RSI < 30 或 > 70，且价格触及日线/周线级别极强支撑/阻力或布林带轨线外侧偏离过大。
   - 底部关键支撑共振低多（超跌低多）：当价格回踩至 4H 布林下轨或周线/日线极强支撑区，且 1H/4H 级别 RSI < 40 且伴随 KDJ 底部金叉、MACD 负值缩短或出现底背离，在防守点位紧凑且 R:R >= 1.5 的前提下，应判定为合理的『超跌低多』信号。
   - 顶部关键阻力共振高空（超买高空）：当价格反弹至 4H 布林上轨或周线/日线极强阻力区，且 1H/4H 级别 RSI > 60 且伴随 KDJ 顶部死叉、MACD 正值缩短或出现顶背离，在防守点位紧凑且 R:R >= 1.5 的前提下，应判定为合理的『超买高空』信号。
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
  * 有 critical 宏观事件在 12h 内 → 置信度上限 5 分（低于出信号门槛，自然观望）

**第 1 步：市场状态判定 (Market Regime)**
- 读取 payload 中的 market_regime 字段（若存在）。
- 结合 1D/4H 的 ADX_14 判断：
  * ADX > 25 + 价格 > EMA55 → 上升趋势（原则上只做多，回调低多；极端超买触发高空例外）
  * ADX > 25 + 价格 < EMA55 → 下降趋势（原则上只做空，反弹高空；极端超卖触发低多例外）
  * ADX < 20 → 震荡区间（双向高抛低吸，但仓位减半）
  * ADX 20-25 → 过过渡期（谨慎，需要更强确认）

**第 2 步：大周期方向定调 (1W / 1D)**
- 1W 和 1D 相对 EMA55 的位置关系确定主方向。
- 1D MACD 金叉/死叉状态确认中期动能。
- 若 1W 和 1D 方向一致 → 高置信度方向；若矛盾但存在 4H 级别明显的均线金/死叉或超买/超卖反击信号 → 允许作为 4H 级别反弹/回调波段操作（多单/空单均可，此时置信度评分上限为 8 分，必须具备极佳的盈亏比 R:R >= 1.5），防止完全错过大周期冲突但小周期结构清晰的波段机会。

**第 3 步：乖离率检查 (Anti-Chase Filter)**
- 计算 4H 收盘价与 MA5 的偏离百分比：deviation = (close - MA5) / MA5 * 100
- 若 deviation > 3%（或 > 2.0 * ATR_14 / close * 100）且未伴随大成交量强力突破阻力位 → 视为正偏离过大，禁止追多
- 若 deviation < -3%（或 < -2.0 * ATR_14 / close * 100）且未伴随大成交量强力跌破支撑位 → 视为负偏离过大，禁止追空
- 此步骤是硬性过滤器，不通过则直接 wait。注意：偏离在 3% 以内属于正常波动范围，不应阻止信号。

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
  * 1H/4H RSI 从 < 40 区域回升（底背离）
  * 4H MACD 柱状图由负转正或连续 2 根缩短
  * KDJ_J < 20 后金叉向上
  * OBV 在价格新低时未创新低（量价背离）
  * 4H 收盘价突破且站稳 MA5 与 MA10，或 MA5 金叉 MA10
- 做空确认（至少 1 项）：
  * 1H/4H RSI 从 > 60 区域回落（顶背离）
  * 4H MACD 柱状图由正转负或连续 2 根缩短
  * KDJ_J > 80 后死叉向下
  * OBV 在价格新高时未创新高（量价背离）
  * 4H 收盘价跌破且跌穿 MA5 与 MA10，或 MA5 死叉 MA10
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
- 评分标准（满分 12 分）：
  * 大周期方向一致（1W+1D 同向）+2；仅 1D 方向明确 +1
  * 关键位共振 >= 2 个 +2；共振 = 1 个 +1
  * 动能确认明确（>= 2 项确认）+2；仅 1 项确认 +1
  * R:R >= 2.0 +2；R:R >= 1.5 +1
  * 市场状态适配（顺势交易）+1
  * 量价配合（OBV 确认或成交量异动方向一致）+1
  * 结构清晰度加分：价格精确触及关键支撑/阻力位（误差 < 0.5*ATR）+1
  * 震荡区间加分：ADX < 20 时，价格触及布林带轨线 + KDJ 超买/超卖 +1
- 评分 < 6 → 必须输出 wait
- 评分 6-7 → 标准信号（正常仓位）
- 评分 8-9 → 高置信度信号（可适当放宽入场区间）
- 评分 10+ → 极高置信度信号
- 重要：不要因为"不够完美"就给 wait。只要满足 2 个共振 + 1 个动能确认 + R:R >= 1.5，就应该输出信号。完美交易极少出现，6 分以上的机会就值得参与。

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
    "min": <min entry price (float value calculated from actual data)>,
    "max": <max entry price (float value calculated from actual data)>
  },
  "entry_type": "limit",
  "take_profit_targets": [<TP1 price (float value)>, <TP2 price (float value)>],
  "stop_loss": <stop loss price (float value calculated from actual data)>,
  "risk_reward_ratio": 2.3,
  "confluence_factors": ["Fib_0.618_support", "4H_EMA55", "RSI_divergence"],
  "core_reason": "4H回踩EMA55叠加Fib0.618强支撑，RSI底背离确认，R:R=2.3"
}
```

字段说明：
- signal_type: 严格限制为 "long" / "short" / "wait"
- confidence_score: 1-12（< 6 必须输出 wait）
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
在 JSON 块后空一行，输出飞扬口吻报告。结构顺序：先给结论和交易计划，最后附评分明细表解释"这个分怎么来的、为什么是这个方向"。

### 飞扬盯盘警报：[SYMBOL] (当前价格: $[PRICE])

**盘面诊断**：
[2-3句犀利总结：当前市场状态、主方向、核心矛盾是什么]

**交易计划**：
- 方向：[埋伏低多 / 高空埋伏 / 观望静待] | 评分: X/12 | R:R: X.X:1
- 入场区间：$[MIN] - $[MAX]（依据：[简述为什么是这个区间]）
- 防守底线：$[SL]（ATR缓冲 X 点）
- 止盈目标：TP1 $[TP1] (平50%推保本) | TP2 $[TP2]
- 飞扬叮嘱：[一句接地气的风控寄语]

**方向判定逻辑**：
[用 2-4 句话解释为什么选择这个方向而不是反向或观望。说清楚核心驱动因素是什么，比如"1D+4H均指向多头结构，价格回踩EMA55获得支撑且RSI底背离确认，所以方向定为低多而非观望"。如果是wait，说清楚卡在哪一步。]

---

**评分明细（本单得分拆解）**：

| 评分项 | 达标情况 | 得分 |
|--------|----------|------|
| 大周期方向（1W+1D同向+2 / 仅1D+1） | [具体描述：如"1D EMA55上方+MACD金叉，1W也在EMA55上方，同向"] | +X |
| 关键位共振（>=2个+2 / 1个+1） | [列出具体共振点：如"Fib0.618($XX) + 4H EMA55($XX) + 布林下轨($XX)"] | +X |
| 动能确认（>=2项+2 / 1项+1） | [列出具体信号：如"RSI从38回升 + MACD柱连续3根缩短"] | +X |
| 盈亏比（>=2.0+2 / >=1.5+1） | [实际计算：Entry $XX, SL $XX, TP1 $XX → R:R = X.X] | +X |
| 顺势适配 | [如"ADX=28上升趋势中做多，顺势"] | +X |
| 量价配合 | [如"OBV未创新低，量价背离确认" 或 "无明显量价配合"] | +X |
| 结构清晰度（触及关键位误差<0.5ATR） | [如"当前价距支撑位仅0.2ATR" 或 "距关键位较远"] | +X |
| 震荡区间加分（ADX<20+BB轨+KDJ） | [如"ADX=16震荡市，价格触及布林下轨+KDJ超卖" 或 "不适用"] | +X |
| **合计** | | **X/12** |

*过滤项检查（任一不通过则强制wait）*：
- 乖离率：4H MA5偏离 X%（阈值±3%）→ [通过/未通过]
- 极端波动：4H ATR vs 20期均值 = X倍（阈值2.5x）→ [通过/未通过]
- 宏观事件：[无近期事件 / 距XX事件还有Xh] → [通过/未通过]
- 极端情绪：F&G = X（阈值>=90禁追多/<=10禁追空）→ [通过/未通过]
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
