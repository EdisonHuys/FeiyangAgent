import sys
from types import ModuleType

# Mock numba module for Python 3.14 compatibility since pandas_ta imports it for JIT speedup
if 'numba' not in sys.modules:
    n = ModuleType('numba')
    n.njit = lambda *args, **kwargs: (lambda f: f) if not args else args[0]
    n.jit = lambda *args, **kwargs: (lambda f: f) if not args else args[0]
    sys.modules['numba'] = n

import pandas as pd
import pandas_ta as ta
import numpy as np
import logging

logger = logging.getLogger(__name__)


def calculate_kdj(df, period=9, signal_k=3, signal_d=3):
    """Calculate KDJ indicator with EMA-like recursive smoothing."""
    df = df.copy()
    low_min = df['low'].rolling(window=period).min()
    high_max = df['high'].rolling(window=period).max()
    denom = (high_max - low_min).replace(0, np.nan)
    rsv = ((df['close'] - low_min) / denom * 100).replace([np.inf, -np.inf], np.nan).fillna(50.0)

    k = [50.0]
    for r in rsv:
        k.append((2.0 / signal_k) * k[-1] + (1.0 / signal_k) * r)
    k = k[1:]

    d = [50.0]
    for val in k:
        d.append((2.0 / signal_d) * d[-1] + (1.0 / signal_d) * val)
    d = d[1:]

    j = [3 * kv - 2 * dv for kv, dv in zip(k, d)]

    df['KDJ_K'] = k
    df['KDJ_D'] = d
    df['KDJ_J'] = j
    return df


def calculate_rsi(df, period=14):
    """Calculate Wilder's RSI indicator and store in 'RSI' and 'RSI_14' columns."""
    df = df.copy()
    delta = df['close'].diff()
    gain = delta.clip(lower=0.0).ewm(alpha=1.0 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0.0)).ewm(alpha=1.0 / period, adjust=False).mean()
    rsi_vals = np.where(
        (gain == 0) & (loss == 0),
        50.0,
        np.where(loss == 0, 100.0, 100.0 - (100.0 / (1.0 + (gain / loss))))
    )
    rsi_series = pd.Series(rsi_vals, index=df.index).fillna(50.0)
    df['RSI'] = rsi_series
    df[f'RSI_{period}'] = rsi_series
    return df


def detect_market_regime(df):
    """
    Detect market regime: trending_up, trending_down, ranging, volatile.
    Uses ADX + EMA slope + Bollinger Band Width for classification.
    Returns a string label and a numeric strength (0-100).
    """
    if len(df) < 30:
        return "unknown", 0

    close = df['close']
    high = df['high']
    low = df['low']

    # ADX (14) for trend strength
    try:
        adx_df = ta.adx(high, low, close, length=14)
        if adx_df is not None and len(adx_df.columns) >= 3:
            adx_val = float(adx_df.iloc[-1, 0]) if pd.notna(adx_df.iloc[-1, 0]) else 0
            plus_di = float(adx_df.iloc[-1, 1]) if pd.notna(adx_df.iloc[-1, 1]) else 0
            minus_di = float(adx_df.iloc[-1, 2]) if pd.notna(adx_df.iloc[-1, 2]) else 0
        else:
            adx_val, plus_di, minus_di = 0, 0, 0
    except Exception:
        adx_val, plus_di, minus_di = 0, 0, 0

    # EMA20 slope (last 5 bars)
    ema20 = ta.ema(close, length=20)
    if ema20 is not None and len(ema20) >= 6 and pd.notna(ema20.iloc[-1]) and pd.notna(ema20.iloc[-5]):
        denom = float(ema20.iloc[-5])
        ema_slope = ((float(ema20.iloc[-1]) - denom) / denom * 100.0) if denom != 0 else 0.0
    else:
        ema_slope = 0.0

    # Bollinger Band Width (volatility)
    bb = ta.bbands(close, length=20, std=2)
    if bb is not None and len(bb.columns) >= 3:
        bb_upper = float(bb.iloc[-1, 2]) if pd.notna(bb.iloc[-1, 2]) else 0
        bb_lower = float(bb.iloc[-1, 0]) if pd.notna(bb.iloc[-1, 0]) else 0
        bb_mid = float(bb.iloc[-1, 1]) if pd.notna(bb.iloc[-1, 1]) else 1
        bb_width_pct = (bb_upper - bb_lower) / bb_mid * 100 if bb_mid > 0 else 0
    else:
        bb_width_pct = 0

    # Classification logic
    if adx_val >= 25:
        if plus_di > minus_di and ema_slope > 0.1:
            regime = "trending_up"
        elif minus_di > plus_di and ema_slope < -0.1:
            regime = "trending_down"
        else:
            regime = "trending_up" if ema_slope > 0 else "trending_down"
        strength = min(100, int(adx_val * 1.5))
    elif bb_width_pct > 4.0:
        regime = "volatile"
        strength = min(100, int(bb_width_pct * 15))
    else:
        regime = "ranging"
        strength = max(0, int(50 - adx_val))

    return regime, strength


def detect_multi_timeframe_regime(df_1d=None, df_4h=None, df_1h=None, df_15m=None, current_price: float = 0.0) -> Dict[str, Any]:
    """
    Institutional Multi-Timeframe Resonance Engine (15M Instant Momentum + 1H Wave + 4H Structure + 1D Macro Filter).
    Computes an agile composite resonance score (-100 to +100) and provides rapid reversal detection.
    """
    has_15m = df_15m is not None and hasattr(df_15m, "columns") and 'close' in df_15m.columns and len(df_15m) >= 15
    res_15m = {"bias": "neutral", "label": "15M 动能平稳", "score": 0}
    res_1h = {"bias": "neutral", "label": "1H 动能平稳", "score": 0}
    res_4h = {"bias": "neutral", "label": "4H 结构整理", "score": 0}
    res_1d = {"bias": "neutral", "label": "1D 日线整理", "score": 0}

    # Weight distribution:
    # Anchor to 4H (45%) + 1D (20%) + 1H (25%) + 15M (10% micro timing)
    # This guarantees the macro anchor is rock solid and prevents whipsaw flips on minor 15m noise.
    w_15m = 10 if has_15m else 0
    w_1h = 25
    w_4h = 45
    w_1d = 20

    # 1. 15M Micro-Momentum & Rapid Reversal Analysis
    if has_15m:
        try:
            close_15m = df_15m['close']
            curr_15m = float(close_15m.iloc[-1])
            ema9_15m = float(close_15m.ewm(span=9).mean().iloc[-1])
            ema21_15m = float(close_15m.ewm(span=21).mean().iloc[-1]) if len(close_15m) >= 21 else ema9_15m
            base_px_15m = max(1e-8, float(close_15m.iloc[-4])) if len(close_15m) >= 4 else max(1e-8, curr_15m)
            slope_15m = (curr_15m - base_px_15m) / base_px_15m * 100.0

            if curr_15m > ema9_15m and slope_15m > 0.25:
                res_15m = {"bias": "bullish", "label": "15M 短线上攻加速", "score": w_15m}
            elif curr_15m < ema9_15m and slope_15m < -0.25:
                res_15m = {"bias": "bearish", "label": "15M 短线向下急跌", "score": -w_15m}
            else:
                res_15m = {"bias": "neutral", "label": "15M 窄幅整理", "score": 0}
        except Exception as e_15m:
            logger.debug(f"[MTF] 15M analysis notice: {e_15m}")

    # 2. 1H Wave Direction & Momentum Analysis (Strict Alignment: Price Position AND Slope must agree)
    try:
        if df_1h is not None and hasattr(df_1h, "columns") and 'close' in df_1h.columns and len(df_1h) >= 15:
            close_1h = df_1h['close']
            curr_1h = float(close_1h.iloc[-1])
            ema20_1h = float(close_1h.ewm(span=20).mean().iloc[-1])
            base_px_1h = max(1e-8, float(close_1h.iloc[-4])) if len(close_1h) >= 4 else max(1e-8, curr_1h)
            slope_1h = (curr_1h - base_px_1h) / base_px_1h * 100.0 if len(close_1h) >= 4 else 0.0

            # Must satisfy BOTH price above EMA AND non-negative slope to be bullish.
            # If price is above EMA but dumping fast (slope < -0.2), it is a pullback, NOT bullish!
            if curr_1h > ema20_1h and slope_1h > 0.15:
                res_1h = {"bias": "bullish", "label": "1H 向上加速发散", "score": w_1h}
            elif curr_1h < ema20_1h and slope_1h < -0.15:
                res_1h = {"bias": "bearish", "label": "1H 向下破位放量", "score": -w_1h}
            else:
                res_1h = {"bias": "neutral", "label": "1H 窄幅整理", "score": 0}
    except Exception as e_1h:
        logger.debug(f"[MTF] 1H analysis notice: {e_1h}")

    # 3. 4H Medium Structure Analysis (Requires genuine trend regime, not just single-bar EMA crossing)
    try:
        if df_4h is not None and hasattr(df_4h, "columns") and 'close' in df_4h.columns and len(df_4h) >= 20:
            regime_4h, strength_4h = detect_market_regime(df_4h)
            close_4h = df_4h['close']
            ema20_4h = float(close_4h.ewm(span=20).mean().iloc[-1])
            curr_4h = float(close_4h.iloc[-1])
            if regime_4h == "trending_up" and (curr_4h >= ema20_4h or strength_4h >= 45):
                s_val = int(w_4h * (max(40, strength_4h) / 100.0))
                res_4h = {"bias": "bullish", "label": f"4H 上升通道 (强度 {strength_4h}/100)", "score": s_val}
            elif regime_4h == "trending_down" and (curr_4h <= ema20_4h or strength_4h >= 45):
                s_val = -int(w_4h * (max(40, strength_4h) / 100.0))
                res_4h = {"bias": "bearish", "label": f"4H 下跌通道 (强度 {strength_4h}/100)", "score": s_val}
            else:
                res_4h = {"bias": "neutral", "label": f"4H 均衡震荡 (强度 {strength_4h}/100)", "score": 0}
    except Exception as e_4h:
        logger.debug(f"[MTF] 4H analysis notice: {e_4h}")

    # 4. 1D Macro Trend Filter
    try:
        if df_1d is not None and hasattr(df_1d, "columns") and 'close' in df_1d.columns and len(df_1d) >= 15:
            close_1d = df_1d['close']
            ema20_1d = float(close_1d.ewm(span=20).mean().iloc[-1])
            ema50_1d = float(close_1d.ewm(span=50).mean().iloc[-1]) if len(close_1d) >= 50 else ema20_1d
            curr_1d = float(close_1d.iloc[-1])
            if curr_1d > ema20_1d and ema20_1d >= ema50_1d:
                res_1d = {"bias": "bullish", "label": "1D 宏观多头格局", "score": w_1d}
            elif curr_1d < ema20_1d and ema20_1d <= ema50_1d:
                res_1d = {"bias": "bearish", "label": "1D 宏观空头格局", "score": -w_1d}
            else:
                res_1d = {"bias": "neutral", "label": "1D 宏观大箱体震荡", "score": 0}
    except Exception as e_1d:
        logger.debug(f"[MTF] 1D analysis notice: {e_1d}")

    # 5. RSI Extreme Overbought/Oversold Mean-Reversion Guard (Active in non-trending macro regimes)
    # In a confirmed 1D/4H macro trend, RSI > 75 or < 25 is trend momentum continuation (RSI钝化);
    # in a ranging/neutral macro market, extreme 1H RSI is a range-boundary exhaustion trap!
    rsi_penalty = 0
    rsi_note = ""
    try:
        if df_1h is not None and hasattr(df_1h, "columns") and 'close' in df_1h.columns and len(df_1h) >= 15:
            df_rsi = calculate_rsi(df_1h, period=14)
            rsi_val = float(df_rsi['RSI'].iloc[-1])
            if rsi_val >= 75 and res_1d["bias"] != "bullish" and res_4h["bias"] != "bullish":
                # Extreme overbought at range top: prevent chasing top, pull back bias towards neutral/short
                rsi_penalty = -20
                rsi_note = f" (⚠️1H RSI {round(rsi_val,1)} 严重超买防冲顶)"
            elif rsi_val <= 25 and res_1d["bias"] != "bearish" and res_4h["bias"] != "bearish":
                # Extreme oversold at range bottom: prevent selling bottom, pull back bias towards neutral/long
                rsi_penalty = 20
                rsi_note = f" (🛡️1H RSI {round(rsi_val,1)} 严重超卖防地板割肉)"
    except Exception as e_rsi:
        logger.debug(f"[MTF] RSI guard notice: {e_rsi}")

    # ── Instant Tactical Reversal Detection ──
    # If 15M and 1H momentum both turn against the macro direction, this is an active reversal wave!
    tactical_reversal = False
    tactical_note = ""
    if res_15m["bias"] == "bearish" and res_1h["bias"] == "bearish":
        tactical_reversal = True
        tactical_note = " (⚠️15M+1H 短线共振转空)"
    elif res_15m["bias"] == "bullish" and res_1h["bias"] == "bullish":
        tactical_reversal = True
        tactical_note = " (🚀15M+1H 短线共振转多)"

    composite_score = res_15m["score"] + res_1h["score"] + res_4h["score"] + res_1d["score"] + rsi_penalty
    composite_score = max(-100, min(100, composite_score))

    # If tactical reversal is active, prevent lagging macro inertia from masking the turn
    if res_15m["bias"] == "bearish" and res_1h["bias"] == "bearish":
        if composite_score > -35:
            composite_score = min(-35, composite_score - 40)
    elif res_15m["bias"] == "bullish" and res_1h["bias"] == "bullish":
        if composite_score < 35:
            composite_score = max(35, composite_score + 40)

    composite_score = max(-100, min(100, composite_score))

    # Broad Institutional Neutral Zone (Hysteresis Band: -35 to +35)
    # Prevents chopping slices in normal 0.3%~1.0% intraday oscillations
    if composite_score >= 50:
        recommended_mode = "long"
        resonance_level = "strong_bullish"
        regime_title = f"🚀 4H+1D 确定性强多头主升浪{tactical_note}{rsi_note} (共振分 +{composite_score}/100)"
    elif composite_score >= 35:
        recommended_mode = "long"
        resonance_level = "moderate_bullish"
        regime_title = f"📈 多头结构稳健上行{tactical_note}{rsi_note} (共振分 +{composite_score}/100)"
    elif composite_score <= -50:
        recommended_mode = "short"
        resonance_level = "strong_bearish"
        regime_title = f"📉 4H+1D 确定性强空头主跌浪{tactical_note}{rsi_note} (共振分 {composite_score}/100)"
    elif composite_score <= -35:
        recommended_mode = "short"
        resonance_level = "moderate_bearish"
        regime_title = f"📉 空头破位下行整理{tactical_note}{rsi_note} (共振分 {composite_score}/100)"
    else:
        recommended_mode = "neutral"
        resonance_level = "ranging"
        regime_title = f"⚖️ 宽幅震荡黄金做市箱体{tactical_note}{rsi_note} (共振分 {composite_score}/100)"

    return {
        "resonance_score": composite_score,
        "resonance_level": resonance_level,
        "recommended_mode": recommended_mode,
        "regime_title": regime_title,
        "tactical_reversal": tactical_reversal,
        "timeframes": {
            "15m": res_15m,
            "1h": res_1h,
            "4h": res_4h,
            "1d": res_1d
        }
    }


def calculate_obv(df):
    """Calculate On Balance Volume for volume confirmation."""
    df = df.copy()
    obv = [0]
    for i in range(1, len(df)):
        if df['close'].iloc[i] > df['close'].iloc[i - 1]:
            obv.append(obv[-1] + df['volume'].iloc[i])
        elif df['close'].iloc[i] < df['close'].iloc[i - 1]:
            obv.append(obv[-1] - df['volume'].iloc[i])
        else:
            obv.append(obv[-1])
    df['OBV'] = obv
    # OBV EMA for trend detection
    df['OBV_EMA20'] = pd.Series(obv).ewm(span=20).mean().values
    return df


def calculate_vwap(df):
    """Calculate session VWAP (rolling 24-bar approximation for crypto 24/7 markets)."""
    df = df.copy()
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    cumulative_tp_vol = (typical_price * df['volume']).rolling(24).sum()
    cumulative_vol = df['volume'].rolling(24).sum()
    df['VWAP'] = (cumulative_tp_vol / cumulative_vol.replace(0, np.nan)).bfill().fillna(typical_price)
    return df


def detect_volume_spike(df, threshold=2.5, lookback=20):
    """
    Detect abnormal volume spikes that often precede significant price moves.
    
    Args:
        df: OHLCV DataFrame with 'volume' and 'close' columns
        threshold: Multiple of average volume to trigger spike (default 2.5x)
        lookback: Period for average volume calculation (default 20)
    
    Returns:
        dict with spike info or None if no spike detected:
        {"is_spike": True, "ratio": 3.2, "direction": "bullish", "avg_volume": 1234.5}
    """
    if len(df) < lookback + 1:
        return None

    avg_vol = float(df['volume'].iloc[-(lookback+1):-1].mean())
    current_vol = float(df['volume'].iloc[-1])

    if avg_vol <= 0 or np.isnan(avg_vol) or np.isnan(current_vol):
        return None

    ratio = current_vol / avg_vol

    if ratio >= threshold:
        # Determine direction: bullish if close > open (or prev close), bearish otherwise
        last_row = df.iloc[-1]
        if 'open' in df.columns:
            direction = "bullish" if last_row['close'] >= last_row['open'] else "bearish"
        else:
            prev_close = float(df['close'].iloc[-2]) if len(df) > 1 else last_row['close']
            direction = "bullish" if last_row['close'] >= prev_close else "bearish"
        return {
            "is_spike": True,
            "ratio": round(ratio, 2),
            "direction": direction,
            "avg_volume": round(avg_vol, 2),
            "current_volume": round(current_vol, 2)
        }

    return {"is_spike": False, "ratio": round(ratio, 2)}


def calculate_support_resistance(df, lookback=50):
    """
    Identify key support/resistance levels from recent swing highs/lows.
    Returns lists of support and resistance price levels.
    """
    if len(df) < lookback:
        lookback = len(df)
    subset = df.iloc[-lookback:]

    highs = subset['high'].values
    lows = subset['low'].values
    close = float(df['close'].iloc[-1])

    # Find swing highs (local maxima with 3-bar confirmation)
    swing_highs = []
    swing_lows = []
    for i in range(2, len(subset) - 2):
        if highs[i] >= highs[i-1] and highs[i] >= highs[i-2] and highs[i] >= highs[i+1] and highs[i] >= highs[i+2]:
            swing_highs.append(float(highs[i]))
        if lows[i] <= lows[i-1] and lows[i] <= lows[i-2] and lows[i] <= lows[i+1] and lows[i] <= lows[i+2]:
            swing_lows.append(float(lows[i]))

    # Filter: resistance above current price, support below
    resistance = sorted([h for h in swing_highs if h > close])[:3]
    support = sorted([s for s in swing_lows if s < close], reverse=True)[:3]

    return support, resistance


def calculate_indicators(df):
    """Calculate comprehensive technical indicators for a given OHLCV DataFrame."""
    df = df.copy()

    # 1. Moving Averages
    df['MA5'] = df['close'].rolling(5).mean()
    df['MA10'] = df['close'].rolling(10).mean()
    df['MA30'] = df['close'].rolling(30).mean()

    # 2. EMA 55 (core trend filter) + EMA 21 (intermediate)
    df['EMA55'] = ta.ema(df['close'], length=55)
    df['EMA21'] = ta.ema(df['close'], length=21)

    # 3. Bollinger Bands (20, 2)
    bb = ta.bbands(df['close'], length=20, std=2)
    if bb is not None:
        df['BB_Lower'] = bb.iloc[:, 0]
        df['BB_Middle'] = bb.iloc[:, 1]
        df['BB_Upper'] = bb.iloc[:, 2]
    else:
        df['BB_Lower'] = np.nan
        df['BB_Middle'] = np.nan
        df['BB_Upper'] = np.nan

    # 4. RSI (14)
    df['RSI_14'] = ta.rsi(df['close'], length=14)

    # 4b. ATR (14) — volatility context for stop-loss placement & sizing
    try:
        df['ATR_14'] = ta.atr(df['high'], df['low'], df['close'], length=14)
    except Exception:
        df['ATR_14'] = np.nan

    # 4c. ADX (14) — trend strength
    try:
        adx_df = ta.adx(df['high'], df['low'], df['close'], length=14)
        if adx_df is not None and len(adx_df.columns) >= 1:
            df['ADX_14'] = adx_df.iloc[:, 0]
        else:
            df['ADX_14'] = np.nan
    except Exception:
        df['ADX_14'] = np.nan

    # 5. KDJ
    df = calculate_kdj(df)

    # 6. MACD (12, 26, 9)
    macd = ta.macd(df['close'], fast=12, slow=26, signal=9)
    if macd is not None:
        df['MACD_DIF'] = macd.iloc[:, 0]
        df['MACD_Hist'] = macd.iloc[:, 1]
        df['MACD_DEA'] = macd.iloc[:, 2]
    else:
        df['MACD_DIF'] = np.nan
        df['MACD_Hist'] = np.nan
        df['MACD_DEA'] = np.nan

    # 7. OBV (volume confirmation)
    df = calculate_obv(df)

    # 8. VWAP (24-bar rolling)
    df = calculate_vwap(df)

    return df


def calculate_fibonacci_levels(df_1d, lookback=100):
    """Calculate Fibonacci support/resistance from daily swing high/low."""
    if df_1d is None or len(df_1d) == 0:
        return {
            "swing_high": 0.0,
            "swing_low": 0.0,
            "upward_levels": {r: 0.0 for r in ["0.382", "0.5", "0.618", "0.786", "1.272", "1.618", "2.618"]},
            "downward_levels": {r: 0.0 for r in ["0.382", "0.5", "0.618", "0.786", "1.272", "1.618", "2.618"]}
        }
    subset = df_1d.iloc[-lookback:] if len(df_1d) >= lookback else df_1d
    high = subset['high'].max()
    low = subset['low'].min()
    diff = high - low

    # Guard against flat market (high == low): return swing levels with neutral midpoints
    if diff <= 0:
        mid = float(high)
        return {
            "swing_high": float(high),
            "swing_low": float(low),
            "upward_levels": {r: mid for r in ["0.382", "0.5", "0.618", "0.786", "1.272", "1.618", "2.618"]},
            "downward_levels": {r: mid for r in ["0.382", "0.5", "0.618", "0.786", "1.272", "1.618", "2.618"]}
        }

    # Determine impulse direction: did swing_low occur before swing_high (uptrend impulse) or after (downtrend impulse)?
    try:
        high_pos = int(subset['high'].values.argmax())
        low_pos = int(subset['low'].values.argmin())
        impulse_dir = "up" if low_pos <= high_pos else "down"
    except Exception:
        impulse_dir = "up"

    upward_levels = {
        "0.382": float(low + 0.382 * diff),
        "0.5": float(low + 0.5 * diff),
        "0.618": float(low + 0.618 * diff),
        "0.786": float(low + 0.786 * diff),
        "1.272": float(low + 1.272 * diff),
        "1.618": float(low + 1.618 * diff),
        "2.618": float(low + 2.618 * diff)
    }
    downward_levels = {
        "0.382": float(high - 0.382 * diff),
        "0.5": float(high - 0.5 * diff),
        "0.618": float(high - 0.618 * diff),
        "0.786": float(high - 0.786 * diff),
        "1.272": float(high - 1.272 * diff),
        "1.618": float(high - 1.618 * diff),
        "2.618": float(high - 2.618 * diff)
    }
    # In an UP impulse (low -> high), a retracement of 38.2% pulls back from high: high - 0.382*diff
    # In a DOWN impulse (high -> low), a retracement of 38.2% bounces from low: low + 0.382*diff
    true_retracements = downward_levels if impulse_dir == "up" else upward_levels
    gp_a = float(high - 0.618 * diff) if impulse_dir == "up" else float(low + 0.618 * diff)
    gp_b = float(high - 0.650 * diff) if impulse_dir == "up" else float(low + 0.650 * diff)

    levels = {
        "swing_high": float(high),
        "swing_low": float(low),
        "impulse_direction": impulse_dir,
        "upward_levels": upward_levels,
        "downward_levels": downward_levels,
        "true_retracements": true_retracements,
        "golden_pocket_zone": [round(min(gp_a, gp_b), 6), round(max(gp_a, gp_b), 6)],
    }
    return levels


def calculate_4h_fibonacci(df_4h, lookback=120):
    """Calculate shorter-term Fibonacci from 4H swing (more relevant for entries)."""
    if df_4h is None or len(df_4h) == 0:
        return {
            "swing_high": 0.0,
            "swing_low": 0.0,
            "impulse_direction": "flat",
            "levels": {r: 0.0 for r in ["0.236", "0.382", "0.5", "0.618", "0.786"]},
            "true_retracements": {r: 0.0 for r in ["0.236", "0.382", "0.5", "0.618", "0.786"]},
            "golden_pocket_zone": [0.0, 0.0]
        }
    subset = df_4h.iloc[-lookback:] if len(df_4h) >= lookback else df_4h
    high = subset['high'].max()
    low = subset['low'].min()
    diff = high - low

    # Guard against flat market
    if diff <= 0:
        mid = float(high)
        return {
            "swing_high": float(high),
            "swing_low": float(low),
            "impulse_direction": "flat",
            "levels": {r: mid for r in ["0.236", "0.382", "0.5", "0.618", "0.786"]},
            "true_retracements": {r: mid for r in ["0.236", "0.382", "0.5", "0.618", "0.786"]},
            "golden_pocket_zone": [mid, mid]
        }

    try:
        high_pos = int(subset['high'].values.argmax())
        low_pos = int(subset['low'].values.argmin())
        impulse_dir = "up" if low_pos <= high_pos else "down"
    except Exception:
        impulse_dir = "up"

    levels = {
        "0.236": float(low + 0.236 * diff),
        "0.382": float(low + 0.382 * diff),
        "0.5": float(low + 0.5 * diff),
        "0.618": float(low + 0.618 * diff),
        "0.786": float(low + 0.786 * diff),
    }
    if impulse_dir == "up":
        true_retracements = {
            "0.236": float(high - 0.236 * diff),
            "0.382": float(high - 0.382 * diff),
            "0.5": float(high - 0.5 * diff),
            "0.618": float(high - 0.618 * diff),
            "0.786": float(high - 0.786 * diff),
        }
        gp_low = float(high - 0.650 * diff)
        gp_high = float(high - 0.618 * diff)
    else:
        true_retracements = dict(levels)
        gp_low = float(low + 0.618 * diff)
        gp_high = float(low + 0.650 * diff)

    return {
        "swing_high": float(high),
        "swing_low": float(low),
        "impulse_direction": impulse_dir,
        "levels": levels,
        "true_retracements": true_retracements,
        "golden_pocket_zone": [round(min(gp_low, gp_high), 6), round(max(gp_low, gp_high), 6)]
    }


def detect_candlestick_patterns(df, lookback=3):
    """
    Deterministic institutional price-action candlestick pattern detector on the last `lookback` bars:
    - Hammer / Bullish Pinbar (锤子线 / 多头长下影拒绝针)
    - Shooting Star / Bearish Pinbar (流星线 / 空头长上影拒绝针)
    - Bullish / Bearish Engulfing (看涨吞没 / 看跌吞没)
    - 2-Bar Reversal / Piercing / Dark Cloud (双K线吞噬反转)
    - Bullish / Bearish SFP Liquidity Sweep (假跌破前低收回 / 假突破前高收回)
    """
    empty_res = {
        "bullish_patterns": [],
        "bearish_patterns": [],
        "has_bullish_trigger": False,
        "has_bearish_trigger": False,
        "summary": "无明显K线反转形态"
    }
    if df is None or len(df) < 4:
        return empty_res

    bullish = []
    bearish = []
    n = len(df)
    start_idx = max(1, n - lookback)

    for i in range(start_idx, n):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        bar_tag = "最新K线" if i == n - 1 else f"前{n - 1 - i}根K线"

        o, h, l, c = float(row['open']), float(row['high']), float(row['low']), float(row['close'])
        po, ph, pl, pc = float(prev['open']), float(prev['high']), float(prev['low']), float(prev['close'])
        rng = max(h - l, 1e-9)
        body = abs(c - o)
        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - l
        close_pos = (c - l) / rng  # 0.0 at low, 1.0 at high

        # 1. Bullish Pinbar / Hammer: long lower wick (>= 1.8x body & >= 45% of range), close in top 45%
        if lower_wick >= max(body * 1.8, rng * 0.45) and upper_wick <= rng * 0.25 and close_pos >= 0.55:
            bullish.append(f"{bar_tag}:锤子线/多头Pinbar(下影占{int(lower_wick/rng*100)}%)")

        # 2. Bearish Pinbar / Shooting Star: long upper wick (>= 1.8x body & >= 45% of range), close in bottom 45%
        if upper_wick >= max(body * 1.8, rng * 0.45) and lower_wick <= rng * 0.25 and close_pos <= 0.45:
            bearish.append(f"{bar_tag}:流星线/空头Pinbar(上影占{int(upper_wick/rng*100)}%)")

        # 3. Bullish Engulfing: previous bar bearish, current bar bullish and body engulfs previous body
        prev_body = abs(pc - po)
        if pc < po and c > o and body > prev_body * 1.05 and c >= po and o <= pc and body >= rng * 0.45:
            bullish.append(f"{bar_tag}:看涨吞没(阳包阴)")

        # 4. Bearish Engulfing: previous bar bullish, current bar bearish and body engulfs previous body
        if pc > po and c < o and body > prev_body * 1.05 and c <= po and o >= pc and body >= rng * 0.45:
            bearish.append(f"{bar_tag}:看跌吞没(阴包阳)")

        # 5. Liquidity Sweep / Swing Failure Pattern (SFP) over prior 10 bars
        if i >= 6:
            prior_window = df.iloc[max(0, i - 12):i]
            prior_low = float(prior_window['low'].min())
            prior_high = float(prior_window['high'].max())
            if l < prior_low and c > prior_low and close_pos >= 0.5:
                bullish.append(f"{bar_tag}:多头流动性猎杀收回(SFP假破前低{round(prior_low, 4)})")
            if h > prior_high and c < prior_high and close_pos <= 0.5:
                bearish.append(f"{bar_tag}:空头流动性猎杀收回(SFP假破前高{round(prior_high, 4)})")

    summary_parts = []
    if bullish:
        summary_parts.append("多头信号[" + "; ".join(bullish) + "]")
    if bearish:
        summary_parts.append("空头信号[" + "; ".join(bearish) + "]")

    return {
        "bullish_patterns": bullish,
        "bearish_patterns": bearish,
        "has_bullish_trigger": len(bullish) > 0,
        "has_bearish_trigger": len(bearish) > 0,
        "summary": " | ".join(summary_parts) if summary_parts else "无明显K线反转形态"
    }


def detect_divergences(df, lookback=30):
    """
    Detect Regular Bullish and Bearish Divergences between Price and RSI_14 / MACD_Hist.
    - Bullish Divergence: Price makes a lower low (or double bottom within 0.3%), while RSI_14 / MACD_Hist makes a higher low.
    - Bearish Divergence: Price makes a higher high (or double top within 0.3%), while RSI_14 / MACD_Hist makes a lower high.
    """
    res = {
        "bullish_divergence": False,
        "bearish_divergence": False,
        "details": []
    }
    if df is None or len(df) < 15 or 'RSI_14' not in df.columns:
        return res

    sub = df.iloc[-lookback:].reset_index(drop=True)
    n = len(sub)
    if n < 12:
        return res

    # Compare recent 4-bar extremum vs earlier extremum in [0 .. n-6]
    recent_slice = sub.iloc[n - 5:]
    earlier_slice = sub.iloc[:n - 5]
    if len(earlier_slice) < 5:
        return res

    rec_low_idx = int(recent_slice['low'].idxmin())
    ear_low_idx = int(earlier_slice['low'].idxmin())
    rec_low = float(sub.loc[rec_low_idx, 'low'])
    ear_low = float(sub.loc[ear_low_idx, 'low'])
    rec_rsi_low = float(sub.loc[rec_low_idx, 'RSI_14']) if pd.notna(sub.loc[rec_low_idx, 'RSI_14']) else 50.0
    ear_rsi_low = float(sub.loc[ear_low_idx, 'RSI_14']) if pd.notna(sub.loc[ear_low_idx, 'RSI_14']) else 50.0

    # Bullish RSI divergence: price lower low (or within 0.2%) & RSI higher low by >= 3 pts from oversold/low zone (<48)
    if rec_low <= ear_low * 1.002 and rec_rsi_low >= ear_rsi_low + 3.0 and ear_rsi_low < 48.0:
        res["bullish_divergence"] = True
        res["details"].append(
            f"底背离确认: 价格新低({round(ear_low, 4)}->{round(rec_low, 4)}) 但 RSI抬高({round(ear_rsi_low, 1)}->{round(rec_rsi_low, 1)})"
        )

    rec_high_idx = int(recent_slice['high'].idxmax())
    ear_high_idx = int(earlier_slice['high'].idxmax())
    rec_high = float(sub.loc[rec_high_idx, 'high'])
    ear_high = float(sub.loc[ear_high_idx, 'high'])
    rec_rsi_high = float(sub.loc[rec_high_idx, 'RSI_14']) if pd.notna(sub.loc[rec_high_idx, 'RSI_14']) else 50.0
    ear_rsi_high = float(sub.loc[ear_high_idx, 'RSI_14']) if pd.notna(sub.loc[ear_high_idx, 'RSI_14']) else 50.0

    # Bearish RSI divergence: price higher high (or within 0.2%) & RSI lower high by >= 3 pts from overbought/high zone (>52)
    if rec_high >= ear_high * 0.998 and rec_rsi_high <= ear_rsi_high - 3.0 and ear_rsi_high > 52.0:
        res["bearish_divergence"] = True
        res["details"].append(
            f"顶背离确认: 价格新高({round(ear_high, 4)}->{round(rec_high, 4)}) 但 RSI走弱({round(ear_rsi_high, 1)}->{round(rec_rsi_high, 1)})"
        )

    return res


def detect_smc_zones(df, lookback=40):
    """
    Detect unmitigated Institutional Order Blocks (OB) and Fair Value Gaps (FVG) near current price.
    Provides high-precision institutional liquidity zones for limit order placement.
    """
    zones = {
        "bullish_fvg": None,
        "bearish_fvg": None,
        "bullish_ob": None,
        "bearish_ob": None
    }
    if df is None or len(df) < 10:
        return zones

    sub = df.iloc[-lookback:].reset_index(drop=True)
    curr_px = float(sub['close'].iloc[-1])
    n = len(sub)

    # Scan backwards for nearest unmitigated FVG & Order Block within ±8% of current price
    for i in range(n - 2, 1, -1):
        h_prev = float(sub['high'].iloc[i - 1])
        l_prev = float(sub['low'].iloc[i - 1])
        h_next = float(sub['high'].iloc[i + 1])
        l_next = float(sub['low'].iloc[i + 1])
        o_curr = float(sub['open'].iloc[i])
        c_curr = float(sub['close'].iloc[i])
        subsequent_lows = float(sub['low'].iloc[i + 1:].min())
        subsequent_highs = float(sub['high'].iloc[i + 1:].max())

        # Bullish FVG: candle[i] is strong impulse up, leaving gap between high[i-1] and low[i+1]
        if zones["bullish_fvg"] is None and c_curr > o_curr and l_next > h_prev:
            # Unmitigated if subsequent lows haven't fully closed below h_prev, and below current price
            if subsequent_lows > h_prev and h_prev < curr_px and (curr_px - h_prev) / curr_px <= 0.08:
                zones["bullish_fvg"] = [round(h_prev, 6), round(l_next, 6)]

        # Bearish FVG: candle[i] is strong impulse down, leaving gap between low[i-1] and high[i+1]
        if zones["bearish_fvg"] is None and c_curr < o_curr and h_next < l_prev:
            if subsequent_highs < l_prev and l_prev > curr_px and (l_prev - curr_px) / curr_px <= 0.08:
                zones["bearish_fvg"] = [round(h_next, 6), round(l_prev, 6)]

        # Bullish Order Block: last bearish candle [i-1] before a strong bullish displacement candle [i]
        o_prev = float(sub['open'].iloc[i - 1])
        c_prev = float(sub['close'].iloc[i - 1])
        if zones["bullish_ob"] is None and c_prev < o_prev and c_curr > h_prev and (c_curr - o_curr) > abs(o_prev - c_prev) * 1.5:
            if subsequent_lows >= l_prev * 0.998 and h_prev <= curr_px * 1.005 and (curr_px - l_prev) / curr_px <= 0.08:
                zones["bullish_ob"] = [round(l_prev, 6), round(h_prev, 6)]

        # Bearish Order Block: last bullish candle [i-1] before a strong bearish displacement candle [i]
        if zones["bearish_ob"] is None and c_prev > o_prev and c_curr < l_prev and (o_curr - c_curr) > abs(c_prev - o_prev) * 1.5:
            if subsequent_highs <= h_prev * 1.002 and l_prev >= curr_px * 0.995 and (h_prev - curr_px) / curr_px <= 0.08:
                zones["bearish_ob"] = [round(l_prev, 6), round(h_prev, 6)]

    return zones


def compute_quant_signal_audit(data_frames, fib_levels, fib_4h, key_levels, regime_info, volume_spike, current_price):
    """
    Deterministic Quantitative Pre-Filter & Confluence Auditor (量化硬性过滤与狙击位预审引擎):
    Pre-calculates all hard filters and optimal institutional entry/SL zones in Python so the LLM
    does not have to do error-prone mental arithmetic or hallucinate candlestick patterns.
    """
    audit = {}
    if not data_frames or current_price <= 0:
        return audit

    def _trend_dir(df):
        if df is None or len(df) < 2:
            return "neutral"
        last = df.iloc[-1]
        c = float(last.get('close', 0) or 0)
        ema21 = float(last.get('EMA21', c) or c)
        ema55 = float(last.get('EMA55', c) or c)
        if c > ema21 and ema21 >= ema55 * 0.998:
            return "bullish"
        elif c < ema21 and ema21 <= ema55 * 1.002:
            return "bearish"
        return "ranging"

    dir_1w = _trend_dir(data_frames.get("1W"))
    dir_1d = _trend_dir(data_frames.get("1D"))
    dir_4h = _trend_dir(data_frames.get("4h"))

    if dir_1w == "bullish" and dir_1d == "bullish":
        htf_alignment = "aligned_bullish"
    elif dir_1w == "bearish" and dir_1d == "bearish":
        htf_alignment = "aligned_bearish"
    elif dir_1w != "ranging" and dir_1d != "ranging" and dir_1w != dir_1d:
        htf_alignment = "conflicting_1w_1d"
    else:
        htf_alignment = f"mixed(1W={dir_1w},1D={dir_1d},4H={dir_4h})"

    audit["htf_trend_alignment"] = {
        "1W": dir_1w,
        "1D": dir_1d,
        "4h": dir_4h,
        "status": htf_alignment,
        "counter_trend_warning": htf_alignment == "conflicting_1w_1d"
    }

    df_4h = data_frames.get("4h")
    df_1h = data_frames.get("1h")
    atr_4h = current_price * 0.015

    if df_4h is not None and len(df_4h) >= 5:
        last_4h = df_4h.iloc[-1]
        ma5_4h = float(last_4h.get('MA5', current_price) or current_price)
        ma5_dev_pct = abs(current_price - ma5_4h) / ma5_4h * 100.0 if ma5_4h > 0 else 0.0

        atr_series = df_4h['ATR_14'].dropna() if 'ATR_14' in df_4h.columns else pd.Series(dtype=float)
        if len(atr_series) >= 1:
            atr_4h = float(atr_series.iloc[-1])
        avg_atr_20 = float(atr_series.iloc[-21:-1].mean()) if len(atr_series) >= 21 else atr_4h
        atr_ratio = round(atr_4h / avg_atr_20, 2) if avg_atr_20 > 0 else 1.0

        rsi_4h = float(last_4h.get('RSI_14', 50.0) or 50.0)
        kdj_j_4h = float(last_4h.get('KDJ_J', 50.0) or 50.0)

        audit["hard_filter_checks_4h"] = {
            "ma5_deviation_pct": round(ma5_dev_pct, 2),
            "ma5_chase_blocked": ma5_dev_pct > 3.0,
            "atr_14": round(atr_4h, 6),
            "atr_ratio_vs_20avg": atr_ratio,
            "atr_volatility_anomaly_blocked": atr_ratio > 2.0,
            "rsi_14": round(rsi_4h, 1),
            "kdj_j": round(kdj_j_4h, 1),
            "long_overbought_blocked": (rsi_4h > 78.0 or kdj_j_4h > 95.0),
            "short_oversold_blocked": (rsi_4h < 22.0 or kdj_j_4h < 5.0),
        }
        audit["price_action_4h"] = detect_candlestick_patterns(df_4h, lookback=3)
        audit["divergences_4h"] = detect_divergences(df_4h, lookback=30)
        audit["smc_zones_4h"] = detect_smc_zones(df_4h, lookback=40)

    if df_1h is not None and len(df_1h) >= 5:
        audit["price_action_1h"] = detect_candlestick_patterns(df_1h, lookback=3)
        audit["divergences_1h"] = detect_divergences(df_1h, lookback=24)

    # Compute objective institutional sniper entry & structural SL reference zones
    supports_below = sorted(
        [lv["price"] for lv in (key_levels or []) if -6.0 <= lv.get("distance_pct", 0) < -0.15],
        reverse=True
    )
    resistances_above = sorted(
        [lv["price"] for lv in (key_levels or []) if 0.15 < lv.get("distance_pct", 0) <= 6.0]
    )

    long_anchor = supports_below[0] if supports_below else round(current_price - 0.6 * atr_4h, 6)
    short_anchor = resistances_above[0] if resistances_above else round(current_price + 0.6 * atr_4h, 6)

    long_zone_min = round(long_anchor - 0.25 * atr_4h, 6)
    long_zone_max = round(min(current_price * 0.999, long_anchor + 0.15 * atr_4h), 6)
    if long_zone_min >= long_zone_max:
        long_zone_min = round(long_zone_max - 0.25 * atr_4h, 6)

    short_zone_min = round(max(current_price * 1.001, short_anchor - 0.15 * atr_4h), 6)
    short_zone_max = round(short_anchor + 0.25 * atr_4h, 6)
    if short_zone_max <= short_zone_min:
        short_zone_max = round(short_zone_min + 0.25 * atr_4h, 6)

    audit["recommended_sniper_zones"] = {
        "atr_4h": round(atr_4h, 6),
        "long_ambush_zone": [long_zone_min, long_zone_max],
        "long_structural_sl": round(long_zone_min - 0.85 * atr_4h, 6),
        "short_ambush_zone": [short_zone_min, short_zone_max],
        "short_structural_sl": round(short_zone_max + 0.85 * atr_4h, 6),
    }

    return audit


def compute_key_levels_context(current_price, fib_1d, fib_4h, support_4h, resistance_4h):
    """
    Pre-compute distance to nearest key levels for the LLM.
    This helps the model focus on what's actually relevant near current price.
    """
    if not current_price or current_price <= 0:
        return []
    all_levels = []
    support_4h = support_4h or []
    resistance_4h = resistance_4h or []

    # Collect 1D fibonacci levels
    if fib_1d and isinstance(fib_1d, dict):
        for direction in ['upward_levels', 'downward_levels']:
            dir_levels = fib_1d.get(direction)
            if dir_levels and isinstance(dir_levels, dict):
                for ratio, price in dir_levels.items():
                    if price and price > 0:
                        dist_pct = (price - current_price) / current_price * 100
                        all_levels.append({
                            "source": f"1D_Fib_{direction.split('_')[0]}_{ratio}",
                            "price": round(price, 2),
                            "distance_pct": round(dist_pct, 2)
                        })

    # Collect 4H fibonacci levels
    if fib_4h and isinstance(fib_4h, dict) and 'levels' in fib_4h and isinstance(fib_4h['levels'], dict):
        for ratio, price in fib_4h['levels'].items():
            if price and price > 0:
                dist_pct = (price - current_price) / current_price * 100
                all_levels.append({
                    "source": f"4H_Fib_{ratio}",
                    "price": round(price, 2),
                    "distance_pct": round(dist_pct, 2)
                })

    # Collect swing S/R
    for s in support_4h:
        if s and s > 0:
            dist_pct = (s - current_price) / current_price * 100
            all_levels.append({"source": "4H_swing_support", "price": round(s, 2), "distance_pct": round(dist_pct, 2)})
    for r in resistance_4h:
        if r and r > 0:
            dist_pct = (r - current_price) / current_price * 100
            all_levels.append({"source": "4H_swing_resistance", "price": round(r, 2), "distance_pct": round(dist_pct, 2)})

    # Sort by absolute distance, keep only levels within ±8% of current price
    nearby = [lv for lv in all_levels if abs(lv["distance_pct"]) <= 8.0]
    nearby.sort(key=lambda x: abs(x["distance_pct"]))

    return nearby[:12]  # Top 12 nearest levels


def clean_and_compress(data_frames, fib_levels, symbol, fib_4h=None, key_levels=None, regime_info=None, volume_spike=None):
    """
    Extract the latest 5 candles from each timeframe, format columns,
    and assemble the enriched payload JSON for the LLM.
    Increased from 3 to 5 candles for better pattern recognition.
    """
    compressed_market_data = {}

    def dynamic_round_price(v):
        if v is None:
            return 0.0
        val = float(v)
        abs_val = abs(val)
        if abs_val >= 1000:
            return round(val, 2)
        elif abs_val >= 100:
            return round(val, 3)
        elif abs_val >= 1:
            return round(val, 4)
        elif abs_val >= 0.1:
            return round(val, 5)
        else:
            return round(val, 6)

    for timeframe, df in data_frames.items():
        # Get the last 5 rows (increased from 3 for better context)
        latest_rows = df.tail(5).copy()

        cols_to_round = [
            'open', 'high', 'low', 'close', 'volume',
            'MA5', 'MA10', 'MA30', 'EMA55', 'EMA21',
            'BB_Lower', 'BB_Middle', 'BB_Upper',
            'RSI_14', 'ATR_14', 'ADX_14',
            'KDJ_K', 'KDJ_D', 'KDJ_J',
            'MACD_DIF', 'MACD_Hist', 'MACD_DEA',
            'VWAP', 'OBV', 'OBV_EMA20'
        ]

        records = []
        for _, row in latest_rows.iterrows():
            record = {
                "datetime": row['datetime'].strftime('%Y-%m-%d %H:%M:%S'),
            }
            for col in cols_to_round:
                val = row.get(col)
                if pd.notna(val):
                    abs_val = abs(float(val))
                    if col in ['RSI_14', 'ADX_14', 'KDJ_K', 'KDJ_D', 'KDJ_J', 'volume', 'OBV', 'OBV_EMA20']:
                        record[col] = round(float(val), 2)
                    elif col in ['MACD_DIF', 'MACD_Hist', 'MACD_DEA']:
                        if abs_val == 0:
                            record[col] = 0.0
                        elif abs_val >= 1:
                            record[col] = round(float(val), 4)
                        elif abs_val >= 0.1:
                            record[col] = round(float(val), 5)
                        else:
                            record[col] = round(float(val), 7)
                    else:
                        record[col] = dynamic_round_price(val)
                else:
                    record[col] = None
            records.append(record)

        compressed_market_data[timeframe] = records

    # Helper to round Fib levels
    def round_dict_values(d):
        return {k: dynamic_round_price(v) for k, v in d.items()}

    fib_levels = fib_levels or {}
    rounded_fib = {
        "swing_high": dynamic_round_price(fib_levels.get("swing_high")),
        "swing_low": dynamic_round_price(fib_levels.get("swing_low")),
        "impulse_direction": fib_levels.get("impulse_direction", "up"),
        "upward_levels": round_dict_values(fib_levels.get("upward_levels", {})),
        "downward_levels": round_dict_values(fib_levels.get("downward_levels", {})),
        "true_retracements": round_dict_values(fib_levels.get("true_retracements", {})),
        "golden_pocket_zone": [dynamic_round_price(x) for x in fib_levels.get("golden_pocket_zone", [0.0, 0.0])]
    }

    # Determine current price
    if '1h' in data_frames and len(data_frames['1h']) > 0:
        current_price = float(data_frames['1h']['close'].iloc[-1])
    elif data_frames and any(len(df) > 0 for df in data_frames.values()):
        first_valid = next(df for df in data_frames.values() if len(df) > 0)
        current_price = float(first_valid['close'].iloc[-1])
    else:
        current_price = 0.0

    payload = {
        "symbol": symbol,
        "current_price": dynamic_round_price(current_price),
        "fibonacci_levels_1d": rounded_fib,
    }

    # Add 4H fibonacci if available
    if fib_4h:
        payload["fibonacci_levels_4h"] = {
            "swing_high": dynamic_round_price(fib_4h["swing_high"]),
            "swing_low": dynamic_round_price(fib_4h["swing_low"]),
            "impulse_direction": fib_4h.get("impulse_direction", "up"),
            "levels": round_dict_values(fib_4h["levels"]),
            "true_retracements": round_dict_values(fib_4h.get("true_retracements", {})),
            "golden_pocket_zone": [dynamic_round_price(x) for x in fib_4h.get("golden_pocket_zone", [0.0, 0.0])]
        }

    # Add pre-computed key levels context
    if key_levels:
        payload["nearby_key_levels"] = key_levels

    # Add market regime info
    if regime_info:
        payload["market_regime"] = regime_info

    # Add volume spike detection
    if volume_spike:
        payload["volume_spike"] = volume_spike

    # Add deterministic quantitative signal & hard-filter audit
    try:
        quant_audit = compute_quant_signal_audit(
            data_frames=data_frames,
            fib_levels=fib_levels,
            fib_4h=fib_4h,
            key_levels=key_levels,
            regime_info=regime_info,
            volume_spike=volume_spike,
            current_price=current_price
        )
        if quant_audit:
            payload["quant_audit"] = quant_audit
    except Exception:
        pass

    payload["market_data"] = compressed_market_data

    return payload
