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
    rsv = (df['close'] - low_min) / (high_max - low_min) * 100
    rsv = rsv.fillna(50.0)

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
    if ema20 is not None and len(ema20) >= 6:
        ema_slope = (float(ema20.iloc[-1]) - float(ema20.iloc[-5])) / float(ema20.iloc[-5]) * 100
    else:
        ema_slope = 0

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
    df['VWAP'] = cumulative_tp_vol / cumulative_vol.replace(0, np.nan)
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

    if avg_vol <= 0:
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
    subset = df_1d.iloc[-lookback:] if len(df_1d) >= lookback else df_1d
    high = subset['high'].max()
    low = subset['low'].min()
    diff = high - low

    levels = {
        "swing_high": float(high),
        "swing_low": float(low),
        "upward_levels": {
            "0.382": float(low + 0.382 * diff),
            "0.5": float(low + 0.5 * diff),
            "0.618": float(low + 0.618 * diff),
            "0.786": float(low + 0.786 * diff),
            "1.272": float(low + 1.272 * diff),
            "1.618": float(low + 1.618 * diff),
            "2.618": float(low + 2.618 * diff)
        },
        "downward_levels": {
            "0.382": float(high - 0.382 * diff),
            "0.5": float(high - 0.5 * diff),
            "0.618": float(high - 0.618 * diff),
            "0.786": float(high - 0.786 * diff),
            "1.272": float(high - 1.272 * diff),
            "1.618": float(high - 1.618 * diff),
            "2.618": float(high - 2.618 * diff)
        }
    }
    return levels


def calculate_4h_fibonacci(df_4h, lookback=120):
    """Calculate shorter-term Fibonacci from 4H swing (more relevant for entries)."""
    subset = df_4h.iloc[-lookback:] if len(df_4h) >= lookback else df_4h
    high = subset['high'].max()
    low = subset['low'].min()
    diff = high - low

    return {
        "swing_high": float(high),
        "swing_low": float(low),
        "levels": {
            "0.236": float(low + 0.236 * diff),
            "0.382": float(low + 0.382 * diff),
            "0.5": float(low + 0.5 * diff),
            "0.618": float(low + 0.618 * diff),
            "0.786": float(low + 0.786 * diff),
        }
    }


def compute_key_levels_context(current_price, fib_1d, fib_4h, support_4h, resistance_4h):
    """
    Pre-compute distance to nearest key levels for the LLM.
    This helps the model focus on what's actually relevant near current price.
    """
    all_levels = []

    # Collect 1D fibonacci levels
    for direction in ['upward_levels', 'downward_levels']:
        if direction in fib_1d:
            for ratio, price in fib_1d[direction].items():
                dist_pct = (price - current_price) / current_price * 100
                all_levels.append({
                    "source": f"1D_Fib_{direction.split('_')[0]}_{ratio}",
                    "price": round(price, 2),
                    "distance_pct": round(dist_pct, 2)
                })

    # Collect 4H fibonacci levels
    if fib_4h and 'levels' in fib_4h:
        for ratio, price in fib_4h['levels'].items():
            dist_pct = (price - current_price) / current_price * 100
            all_levels.append({
                "source": f"4H_Fib_{ratio}",
                "price": round(price, 2),
                "distance_pct": round(dist_pct, 2)
            })

    # Collect swing S/R
    for s in support_4h:
        dist_pct = (s - current_price) / current_price * 100
        all_levels.append({"source": "4H_swing_support", "price": round(s, 2), "distance_pct": round(dist_pct, 2)})
    for r in resistance_4h:
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

    rounded_fib = {
        "swing_high": dynamic_round_price(fib_levels["swing_high"]),
        "swing_low": dynamic_round_price(fib_levels["swing_low"]),
        "upward_levels": round_dict_values(fib_levels["upward_levels"]),
        "downward_levels": round_dict_values(fib_levels["downward_levels"])
    }

    # Determine current price
    if '1h' in data_frames:
        current_price = float(data_frames['1h']['close'].iloc[-1])
    else:
        first_tf = list(data_frames.values())[0]
        current_price = float(first_tf['close'].iloc[-1])

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
            "levels": round_dict_values(fib_4h["levels"])
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

    payload["market_data"] = compressed_market_data

    return payload
