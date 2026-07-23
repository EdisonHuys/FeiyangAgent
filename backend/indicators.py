import sys
from types import ModuleType

# Mock numba module for Python 3.14 compatibility since pandas_ta imports it for JIT speedup
if 'numba' not in sys.modules:
    n = ModuleType('numba')
    # Support decorators: @njit or @njit(parallel=True)
    n.njit = lambda *args, **kwargs: (lambda f: f) if not args else args[0]
    n.jit = lambda *args, **kwargs: (lambda f: f) if not args else args[0]
    sys.modules['numba'] = n

import pandas as pd
import pandas_ta as ta
import numpy as np
import logging

logger = logging.getLogger(__name__)

def calculate_kdj(df, period=9, signal_k=3, signal_d=3):
    """
    Calculate KDJ indicator.
    """
    df = df.copy()
    low_min = df['low'].rolling(window=period).min()
    high_max = df['high'].rolling(window=period).max()
    rsv = (df['close'] - low_min) / (high_max - low_min) * 100
    rsv = rsv.fillna(50.0)  # Seed value to avoid propagating NaNs
    
    # Smooth K and D using EMA-like recursion
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

def calculate_indicators(df):
    """
    Calculate technical indicators for a given OHLCV DataFrame.
    """
    df = df.copy()
    
    # 1. Moving Averages
    df['MA5'] = df['close'].rolling(5).mean()
    df['MA10'] = df['close'].rolling(10).mean()
    df['MA30'] = df['close'].rolling(30).mean()
    
    # 2. EMA 55
    df['EMA55'] = ta.ema(df['close'], length=55)
    
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
    
    # 5. KDJ
    df = calculate_kdj(df)
    
    # 6. MACD (12, 26, 9)
    macd = ta.macd(df['close'], fast=12, slow=26, signal=9)
    if macd is not None:
        df['MACD_DIF'] = macd.iloc[:, 0]  # MACD line
        df['MACD_Hist'] = macd.iloc[:, 1] # MACD histogram
        df['MACD_DEA'] = macd.iloc[:, 2]  # MACD signal line
    else:
        df['MACD_DIF'] = np.nan
        df['MACD_Hist'] = np.nan
        df['MACD_DEA'] = np.nan
        
    return df

def calculate_fibonacci_levels(df_1d, lookback=100):
    """
    Calculate Fibonacci support and resistance levels based on the High/Low of the last N daily candles.
    """
    subset = df_1d.iloc[-lookback:] if len(df_1d) >= lookback else df_1d
    high = subset['high'].max()
    low = subset['low'].min()
    diff = high - low
    
    levels = {
        "swing_high": float(high),
        "swing_low": float(low),
        "upward_levels": {
            "0.382": float(low + 0.382 * diff),
            "0.618": float(low + 0.618 * diff),
            "1.618": float(low + 1.618 * diff),
            "2.618": float(low + 2.618 * diff),
            "3.618": float(low + 3.618 * diff)
        },
        "downward_levels": {
            "0.382": float(high - 0.382 * diff),
            "0.618": float(high - 0.618 * diff),
            "1.618": float(high - 1.618 * diff),
            "2.618": float(high - 2.618 * diff),
            "3.618": float(high - 3.618 * diff)
        }
    }
    return levels

def clean_and_compress(data_frames, fib_levels, symbol):
    """
    Extract the latest 3 candles from each timeframe, format columns,
    and assemble the clean payload JSON.
    """
    compressed_market_data = {}
    
    for timeframe, df in data_frames.items():
        # Get the last 3 rows
        latest_rows = df.tail(3).copy()
        
        # Round numerical values to keep the payload clean
        cols_to_round = [
            'open', 'high', 'low', 'close', 'volume',
            'MA5', 'MA10', 'MA30', 'EMA55',
            'BB_Lower', 'BB_Middle', 'BB_Upper',
            'RSI_14', 'KDJ_K', 'KDJ_D', 'KDJ_J',
            'MACD_DIF', 'MACD_Hist', 'MACD_DEA'
        ]
        
        # Format columns: convert timestamp to string, drop columns we don't need
        records = []
        for _, row in latest_rows.iterrows():
            record = {
                "datetime": row['datetime'].strftime('%Y-%m-%d %H:%M:%S'),
            }
            for col in cols_to_round:
                val = row.get(col)
                if pd.notna(val):
                    # Round value for clean output
                    record[col] = round(float(val), 2)
                else:
                    record[col] = None
            records.append(record)
            
        compressed_market_data[timeframe] = records
        
    # Helper to round Fib levels
    def round_dict_values(d):
        return {k: round(v, 2) for k, v in d.items()}

    rounded_fib = {
        "swing_high": round(fib_levels["swing_high"], 2),
        "swing_low": round(fib_levels["swing_low"], 2),
        "upward_levels": round_dict_values(fib_levels["upward_levels"]),
        "downward_levels": round_dict_values(fib_levels["downward_levels"])
    }
    
    payload = {
        "symbol": symbol,
        "current_price": float(data_frames['1h']['close'].iloc[-1]), # Latest price from 1h timeframe
        "fibonacci_levels": rounded_fib,
        "market_data": compressed_market_data
    }
    
    return payload
