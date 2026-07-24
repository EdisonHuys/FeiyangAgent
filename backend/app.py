import os
import json
import yaml
import logging
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

# Ensure modules in backend/ are importable
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from data_fetcher import DataFetcher, get_data_fetcher
    from indicators import calculate_indicators, calculate_fibonacci_levels, clean_and_compress
    from agent import FeiyangAgent, load_system_prompt
    from notifier import Notifier
    from sniper_engine import SniperEngine
    from backtest import BacktestRunner
except ImportError:
    from backend.data_fetcher import DataFetcher, get_data_fetcher
    from backend.indicators import calculate_indicators, calculate_fibonacci_levels, clean_and_compress
    from backend.agent import FeiyangAgent, load_system_prompt
    from backend.notifier import Notifier
    from backend.sniper_engine import SniperEngine
    from backend.backtest import BacktestRunner

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("FeiyangBackend")

# Load environment variables
load_dotenv()

app = FastAPI(title="Feiyang Agent API")

@app.middleware("http")
async def add_html_no_cache_headers(request, call_next):
    """
    WKWebView heuristically caches index.html (StaticFiles sends no cache
    headers), so after an app upgrade the webview can keep loading the OLD
    html -> OLD js bundle -> newly added UI never appears. Force revalidation
    for HTML documents; hashed assets (index-<hash>.js/css) stay cacheable.
    """
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith(".html"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response

import threading
import time
from datetime import datetime

monitor_logs = []
monitor_logs_lock = threading.Lock()

def log_monitor_event(message: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted_log = f"[{timestamp}] {message}"
    with monitor_logs_lock:
        monitor_logs.append(formatted_log)
        if len(monitor_logs) > 100:
            monitor_logs.pop(0)

# CORS: local desktop app — only allow loopback origins (served UI + Vite dev
# server). Wildcard + credentials is both spec-invalid and unsafe here since
# the API exposes config read/write (incl. API keys) to any local web page.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Resolve CONFIG_PATH and ENV_PATH dynamically based on PyInstaller execution (sys.frozen)
if getattr(sys, 'frozen', False):
    exec_dir = os.path.dirname(sys.executable)
    # On macOS, sys.executable is inside the .app package bundle: FeiyangAgent.app/Contents/MacOS/FeiyangAgent
    # We want config.yaml and .env to live outside the bundle, next to the .app file
    if ".app/Contents/MacOS" in exec_dir:
        root_dir = os.path.dirname(os.path.dirname(os.path.dirname(exec_dir)))
    else:
        root_dir = exec_dir
else:
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG_PATH = os.path.join(root_dir, "config.yaml")
ENV_PATH = os.path.join(root_dir, ".env")
STATE_FILE_PATH = os.path.join(root_dir, "last_signals.json")
PROMPT_PATH = os.path.join(root_dir, "feiyang_prompt.txt")

sniper_engine = SniperEngine(root_dir)
backtest_runner = BacktestRunner(root_dir)

def load_signals_state() -> dict:
    if os.path.exists(STATE_FILE_PATH):
        try:
            with open(STATE_FILE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load signal state file: {e}")
    return {}

def save_signals_state(state: dict):
    try:
        with open(STATE_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Failed to save signal state file: {e}")

def process_signal_evaluation(symbol: str, payload: dict, json_signal: dict, markdown_report: str, yaml_cfg: dict, source_tag: str = "24H盯盘"):
    """
    Unified signal lifecycle evaluation & notification dispatcher.
    Used by both 24H background monitor and manual analysis API.
    """
    try:
        current_price = float(payload.get("current_price", 0.0))
    except (ValueError, TypeError):
        current_price = 0.0

    # Trigger active sniper positions update against current price
    if current_price > 0:
        sniper_engine.check_market_prices({symbol: current_price})

    sig_type = str(json_signal.get("signal_type", "wait")).lower()
    conf = json_signal.get("confidence_score", 0)
    
    entry_zone = json_signal.get("entry_zone") or {}
    try:
        raw_min = entry_zone.get("min")
        raw_max = entry_zone.get("max")
        entry_min = float(raw_min) if raw_min is not None else None
        entry_max = float(raw_max) if raw_max is not None else None
    except (ValueError, TypeError):
        entry_min, entry_max = None, None
        
    if entry_min is not None and entry_max is not None:
        lower_entry = min(entry_min, entry_max)
        upper_entry = max(entry_min, entry_max)
    else:
        lower_entry, upper_entry = None, None
        
    try:
        sl = float(json_signal.get("stop_loss")) if json_signal.get("stop_loss") is not None else None
    except (ValueError, TypeError):
        sl = None
        
    raw_tps = json_signal.get("take_profit_targets") or []
    tp_list = []
    for x in raw_tps:
        try:
            if x is not None:
                tp_list.append(float(x))
        except (ValueError, TypeError):
            pass

    last_signals = load_signals_state()
    prev_state = last_signals.get(symbol, {"signal_type": "wait"})
    prev_sig = prev_state.get("signal_type", "wait")

    if sig_type == "wait":
        last_signals[symbol] = {"signal_type": "wait"}
        save_signals_state(last_signals)
        log_monitor_event(f"✅ [{source_tag}] {symbol} 诊断完成。交易决策：WAIT (观望等待)，当前价：${current_price}。已静默不发送推送。")
        return

    if sig_type in ["long", "short"]:
        should_push = False
        push_reason = ""

        # Check if currently inside entry zone
        in_zone_now = (lower_entry is not None and upper_entry is not None and (lower_entry <= current_price <= upper_entry))

        # Condition 1: New signal direction or previous was wait
        if prev_sig != sig_type:
            should_push = True
            push_reason = f"🚨 发现全新的 {sig_type.upper()} 交易信号"
        else:
            prev_sl = prev_state.get("stop_loss")
            prev_tps = prev_state.get("tp_targets", [])
            prev_min = prev_state.get("entry_min")
            prev_max = prev_state.get("entry_max")

            sl_broken = False
            tp_reached = False

            if prev_sig == "long":
                if prev_sl and current_price <= prev_sl:
                    sl_broken = True
                if prev_tps and current_price >= max(prev_tps):
                    tp_reached = True
            elif prev_sig == "short":
                if prev_sl and current_price >= prev_sl:
                    sl_broken = True
                if prev_tps and current_price <= min(prev_tps):
                    tp_reached = True

            if sl_broken:
                should_push = True
                push_reason = f"⚠️ 上一单防守线 (${prev_sl}) 已破位，发布重置策略"
            elif tp_reached:
                should_push = True
                push_reason = f"🎉 上一单止盈目标到达，发布更新策略"
            else:
                was_entered = prev_state.get("entered_zone", False)
                if in_zone_now and not was_entered:
                    should_push = True
                    push_reason = f"🎯 价格已回踩进入最佳吃单区间 (${lower_entry} - ${upper_entry})"
                else:
                    if prev_min and prev_max and lower_entry and upper_entry:
                        shift_min = abs(lower_entry - prev_min) / prev_min
                        shift_max = abs(upper_entry - prev_max) / prev_max
                        if shift_min > 0.01 or shift_max > 0.01:
                            should_push = True
                            push_reason = f"🔄 诊断模型显著调整了吃单点位"

        # Also pass valid signal to Sniper Engine
        try:
            sniper_trade = sniper_engine.process_new_signal(symbol, current_price, json_signal, markdown_report)
            if sniper_trade:
                mode_str = sniper_engine.get_config().get("mode", "paper").upper()
                log_monitor_event(f"🎯 [狙击系统] [{mode_str}] 成功自动挂单/成交 {symbol} {sig_type.upper()} 计划！（保证金 ${sniper_trade['margin_usd']}，杠杆 {sniper_trade['leverage']}x）")
        except Exception as se:
            logger.warning(f"Sniper engine order placement error: {se}")

        if should_push:
            notify_cfg = yaml_cfg.get("notifications", {})
            notify_enabled = notify_cfg.get("enabled", False)
            notify_on_signal = notify_cfg.get("notify_on_signal", False)

            if notify_enabled and notify_on_signal:
                logger.info(f"[{source_tag}] Pushing alert for {symbol} ({sig_type.upper()}): {push_reason}")
                log_monitor_event(f"🎯 [{source_tag}] {symbol} ({push_reason})！正在向通道推送...")

                tp_targets_str = ", ".join([f"${t}" for t in tp_list])
                sig_label = "建议做多 (LONG) 📈" if sig_type == "long" else "建议做空 (SHORT) 📉"
                emoji = "📈" if sig_type == "long" else "📉"

                signal_header = (
                    f"🚨 *[{source_tag}警报] {symbol} {emoji}*\n"
                    f"📌 触发依据：{push_reason}\n"
                    f"🔥 交易信号：{sig_label}\n"
                    f"🔥 置信度评分：{conf} / 10\n"
                    f"----------------------------------------\n"
                    f"📥 合理吃单区间：${lower_entry} - ${upper_entry}\n"
                    f"🛡️ 防守线 (止损)：${sl}\n"
                    f"🎯 阶梯止盈目标：{tp_targets_str}\n"
                    f"----------------------------------------\n\n"
                )

                try:
                    notifier = Notifier(yaml_cfg)
                    notifier.send_notification(f"{source_tag}：{symbol}", signal_header + markdown_report)
                    log_monitor_event(f"📬 [{source_tag}] {symbol} 交易警报已送达。")
                except Exception as ne:
                    logger.warning(f"Failed to push notification: {ne}")
            else:
                log_monitor_event(f"ℹ️ [{source_tag}] {symbol} 触发诊断信号 ({push_reason})。（信号诊断推送开关已关闭，静默跳过推送）")

            last_signals[symbol] = {
                "signal_type": sig_type,
                "entry_min": lower_entry,
                "entry_max": upper_entry,
                "stop_loss": sl,
                "tp_targets": tp_list,
                "entered_zone": in_zone_now
            }
            save_signals_state(last_signals)
        else:
            if isinstance(last_signals.get(symbol), dict):
                last_signals[symbol]["entered_zone"] = in_zone_now
                save_signals_state(last_signals)
            log_monitor_event(f"ℹ️ [{source_tag}] {symbol} 处于已有 {sig_type.upper()} 计划中，未满足二次提醒条件（未到建仓点/无破位），已静默。")

class ConfigUpdate(BaseModel):
    symbol: str
    exchange: str
    timeframes: List[str]
    symbols: List[str]
    fib_lookback: int
    scan_interval_minutes: Optional[int] = 15
    llm_model: str
    llm_temp: float
    llm_max_tokens: int
    notify_enabled: bool
    notify_on_signal: Optional[bool] = False
    notify_on_trade: Optional[bool] = True
    notify_channels: List[str]
    telegram_chat_id: Optional[str] = ""
    # Keys
    openai_api_key: Optional[str] = ""
    openai_api_base: Optional[str] = ""
    telegram_bot_token: Optional[str] = ""
    serverchan_send_key: Optional[str] = ""
    bark_device_key: Optional[str] = ""

class AnalysisRequest(BaseModel):
    symbol: str

def load_yaml_config():
    if not os.path.exists(CONFIG_PATH):
        default_cfg = {
            "symbol": "BTC/USDT",
            "exchange": "binance",
            "timeframes": ["1M", "1W", "1D", "4h", "1h"],
            "symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "ZEC/USDT"],
            "fibonacci": {
                "lookback_days": 100
            },
            "llm": {
                "model": "gpt-4o",
                "temperature": 0.1,
                "max_tokens": 3000
            },
            "notifications": {
                "enabled": False,
                "channels": ["telegram"],
                "telegram": {
                    "chat_id": ""
                }
            }
        }
        write_yaml_config(default_cfg)
        return default_cfg
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
        if "symbols" not in cfg:
            cfg["symbols"] = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "ZEC/USDT"]
        return cfg

def write_yaml_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, default_flow_style=False, allow_unicode=True)

def update_dotenv(updates: Dict[str, str]):
    # Read existing env
    env_lines = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            env_lines = f.readlines()
            
    env_dict = {}
    for line in env_lines:
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            env_dict[k.strip()] = v.strip().strip('"').strip("'")
            
    # Apply updates
    for k, v in updates.items():
        env_dict[k] = v
        
    # Write back
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write("# Auto-generated env keys\n")
        for k, v in env_dict.items():
            f.write(f'{k}="{v}"\n')
            
    # Reload environment
    os.environ.update(env_dict)

CHART_INDICATOR_COLS = [
    'MA5', 'MA10', 'MA30', 'EMA55',
    'BB_Lower', 'BB_Middle', 'BB_Upper',
    'RSI_14', 'KDJ_K', 'KDJ_D', 'KDJ_J',
    'MACD_DIF', 'MACD_Hist', 'MACD_DEA'
]

def get_chart_data(df):
    """
    Convert an OHLCV + indicators DataFrame into chart-ready records.
    Vectorized (bulk column ops + to_dict) instead of row-by-row iterrows.
    Output shape is identical to the legacy implementation:
    {"time": int, "open": float, ..., "<indicator_lower>": float (NaN omitted)}
    """
    base_cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
    present_indicators = [c for c in CHART_INDICATOR_COLS if c in df.columns]

    out = df[base_cols + present_indicators].copy()
    out['time'] = (out['timestamp'] // 1000)
    out = out.drop(columns=['timestamp'])
    out = out.rename(columns={c: c.lower() for c in present_indicators})

    chart_data = []
    for rec in out.to_dict('records'):
        # Drop NaN values; cast to native Python types for safe JSON encoding
        chart_data.append({
            k: (int(v) if k == 'time' else float(v))
            for k, v in rec.items()
            if pd.notna(v)
        })
    return chart_data

@app.get("/api/config")
def get_config():
    """
    Get backend configuration settings.
    """
    yaml_cfg = load_yaml_config()
    
    # Read secret keys from environment/dotenv
    load_dotenv(override=True)
    return {
        "symbol": yaml_cfg.get("symbol", "BTC/USDT"),
        "exchange": yaml_cfg.get("exchange", "binance"),
        "timeframes": yaml_cfg.get("timeframes", ["1M", "1W", "1D", "4h", "1h"]),
        "symbols": yaml_cfg.get("symbols", ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "ZEC/USDT"]),
        "fib_lookback": yaml_cfg.get("fibonacci", {}).get("lookback_days", 100),
        "scan_interval_minutes": yaml_cfg.get("scan_interval_minutes", 15),
        "llm_model": yaml_cfg.get("llm", {}).get("model", "gpt-4o"),
        "llm_temp": yaml_cfg.get("llm", {}).get("temperature", 0.1),
        "llm_max_tokens": yaml_cfg.get("llm", {}).get("max_tokens", 3000),
        "notify_enabled": yaml_cfg.get("notifications", {}).get("enabled", False),
        "notify_on_signal": yaml_cfg.get("notifications", {}).get("notify_on_signal", False),
        "notify_on_trade": yaml_cfg.get("notifications", {}).get("notify_on_trade", True),
        "notify_channels": yaml_cfg.get("notifications", {}).get("channels", []),
        "telegram_chat_id": yaml_cfg.get("notifications", {}).get("telegram", {}).get("chat_id", ""),
        
        # Secret keys (for a local desktop client we return them; UI masks it in password field)
        "openai_api_key": os.getenv("OPENAI_API_KEY", ""),
        "openai_api_base": os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1"),
        "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "serverchan_send_key": os.getenv("SERVERCHAN_SEND_KEY", ""),
        "bark_device_key": os.getenv("BARK_DEVICE_KEY", ""),
    }

@app.post("/api/config")
def save_config(cfg: ConfigUpdate):
    """
    Save configuration changes to yaml and .env.
    """
    try:
        # Update config.yaml
        yaml_cfg = {
            "symbol": cfg.symbol,
            "exchange": cfg.exchange,
            "scan_interval_minutes": cfg.scan_interval_minutes or 15,
            "timeframes": cfg.timeframes,
            "symbols": cfg.symbols,
            "fibonacci": {
                "lookback_days": cfg.fib_lookback
            },
            "llm": {
                "model": cfg.llm_model,
                "temperature": cfg.llm_temp,
                "max_tokens": cfg.llm_max_tokens
            },
            "notifications": {
                "enabled": cfg.notify_enabled,
                "notify_on_signal": bool(cfg.notify_on_signal),
                "notify_on_trade": bool(cfg.notify_on_trade),
                "channels": cfg.notify_channels,
                "telegram": {
                    "chat_id": cfg.telegram_chat_id
                }
            }
        }
        write_yaml_config(yaml_cfg)
        
        # Sync sniper engine to auto-cancel pending orders of removed symbols
        sniper_engine.sync_watchlist_symbols(cfg.symbols)
        
        # Update .env
        env_updates = {
            "OPENAI_API_KEY": cfg.openai_api_key or "",
            "OPENAI_API_BASE": cfg.openai_api_base or "https://api.openai.com/v1",
            "TELEGRAM_BOT_TOKEN": cfg.telegram_bot_token or "",
            "SERVERCHAN_SEND_KEY": cfg.serverchan_send_key or "",
            "BARK_DEVICE_KEY": cfg.bark_device_key or ""
        }
        update_dotenv(env_updates)
        
        return {"status": "success", "message": "Configuration saved successfully."}
    except Exception as e:
        logger.error(f"Error saving configuration: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/monitor-logs")
def get_monitor_logs():
    with monitor_logs_lock:
        return {"logs": list(monitor_logs)}

@app.post("/api/monitor-logs/clear")
def clear_monitor_logs():
    with monitor_logs_lock:
        monitor_logs.clear()
    return {"status": "success", "logs": []}

# Short-TTL cache for /api/market responses. The frontend polls this endpoint
# every 60s; without a cache each poll re-downloads 5 timeframes x 200 candles
# from the exchange. 30s is well within candle freshness tolerance for
# visualization while cutting exchange traffic roughly in half.
MARKET_CACHE_TTL_SECONDS = 30
_market_cache = {}
_market_cache_lock = threading.Lock()

def _market_cache_get(key):
    with _market_cache_lock:
        entry = _market_cache.get(key)
        if entry and (time.time() - entry[0]) < MARKET_CACHE_TTL_SECONDS:
            return entry[1]
    return None

def _market_cache_set(key, data):
    with _market_cache_lock:
        # Keep the cache bounded: drop stale entries on insert
        now = time.time()
        stale_keys = [k for k, (ts, _) in _market_cache.items() if now - ts >= MARKET_CACHE_TTL_SECONDS]
        for k in stale_keys:
            _market_cache.pop(k, None)
        _market_cache[key] = (now, data)

@app.get("/api/market")
def get_market_data(symbol: str = "BTC/USDT", force_refresh: bool = False):
    """
    Fetch market OHLCV data and calculate all indicators for visualization.
    Responses are cached for MARKET_CACHE_TTL_SECONDS unless force_refresh is set.
    """
    yaml_cfg = load_yaml_config()
    cache_key = (yaml_cfg.get("exchange", "binance"), symbol)
    if not force_refresh:
        cached = _market_cache_get(cache_key)
        if cached is not None and all(tf in cached.get("charts", {}) for tf in timeframes):
            return cached
    exchange_id = yaml_cfg.get("exchange", "binance")
    timeframes = yaml_cfg.get("timeframes", ["1M", "1W", "1D", "4h", "1h"])
    fib_lookback = yaml_cfg.get("fibonacci", {}).get("lookback_days", 100)

    fetcher = get_data_fetcher(exchange_id)
    raw_dfs = {}
    
    # 1. Fetch OHLCV data
    try:
        raw_dfs = fetcher.fetch_all_timeframes(symbol, timeframes, limit=200)
    except Exception as e:
        logger.error(f"Error fetching data: {e}")
        raise HTTPException(status_code=500, detail=f"Exchange fetching error: {str(e)}")
        
    # 2. Calculate Technical Indicators
    processed_dfs = {}
    charts_data = {}
    for tf, df in raw_dfs.items():
        try:
            df_with_indicators = calculate_indicators(df)
            processed_dfs[tf] = df_with_indicators
            # Format timeframe for chart
            charts_data[tf] = get_chart_data(df_with_indicators)
        except Exception as e:
            logger.error(f"Error calculating indicators for {tf}: {e}")
            raise HTTPException(status_code=500, detail=f"Indicator calculation error: {str(e)}")
            
    # 3. Calculate Fibonacci levels based on 1D timeframe
    if "1D" not in processed_dfs:
        raise HTTPException(status_code=400, detail="Daily (1D) timeframe data is required for Fibonacci calculation.")
        
    try:
        fib_levels = calculate_fibonacci_levels(processed_dfs["1D"], lookback=fib_lookback)
    except Exception as e:
        logger.error(f"Error calculating Fib levels: {e}")
        raise HTTPException(status_code=500, detail=f"Fibonacci calculation error: {str(e)}")
        
    # 4. Generate compressed payload
    try:
        payload = clean_and_compress(processed_dfs, fib_levels, symbol)
    except Exception as e:
        logger.error(f"Error compressing data: {e}")
        raise HTTPException(status_code=500, detail=f"Data packaging error: {str(e)}")
        
    result = {
        "symbol": symbol,
        "fibonacci_levels": fib_levels,
        "charts": charts_data,
        "payload": payload
    }
    _market_cache_set(cache_key, result)
    return result

@app.post("/api/analyze")
def run_analysis(req: AnalysisRequest):
    """
    Run full prediction pipeline: fetch market data -> compute indicators -> call LLM -> notify -> return response
    """
    symbol = req.symbol
    logger.info(f"Triggering diagnostic analysis for symbol: {symbol}")
    log_monitor_event(f"⚡ [手动触发] 手动开启 {symbol} 诊断，正在拉取 K 线并计算指标...")
    
    # 1. Fetch market data (bypass cache so the LLM always sees fresh data)
    market_data = get_market_data(symbol, force_refresh=True)
    payload = market_data["payload"]
    
    # 2. Setup Agent
    load_dotenv(override=True)
    api_key = os.getenv("OPENAI_API_KEY")
    api_base = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
    
    if not api_key or api_key == "your-llm-api-key":
        raise HTTPException(
            status_code=400, 
            detail="大模型 API Key 尚未配置，请在设置中填写并保存！"
        )
        
    yaml_cfg = load_yaml_config()
    llm_cfg = yaml_cfg.get("llm", {})
    model_name = llm_cfg.get("model", "gpt-4o")
    temperature = llm_cfg.get("temperature", 0.1)
    max_tokens = llm_cfg.get("max_tokens", 3000)
    
    agent = FeiyangAgent(
        api_key=api_key,
        api_base=api_base,
        model_name=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        system_prompt=load_system_prompt(root_dir)
    )

    # 3. Call LLM
    try:
        json_signal, markdown_report = agent.analyze(payload)
    except Exception as e:
        logger.error(f"LLM analyze error: {e}")
        log_monitor_event(f"❌ [手动诊断失败] {symbol}。原因：{str(e)}")
        raise HTTPException(status_code=500, detail=f"LLM 诊断失败: {str(e)}")
        
    # 4. Process signal evaluation & send notification
    try:
        process_signal_evaluation(symbol, payload, json_signal, markdown_report, yaml_cfg, source_tag="手动诊断")
    except Exception as e:
        logger.warning(f"Notification delivery failed: {e}")
        
    sig_label = json_signal.get("signal_type", "wait").upper()
    conf = json_signal.get("confidence_score", 0)
    log_monitor_event(f"✅ [手动诊断成功] {symbol} 诊断完成。交易决策：{sig_label}，置信度：{conf}/10。")
        
    return {
        "status": "success",
        "signal": json_signal,
        "report": markdown_report
    }

class LLMTestRequest(BaseModel):
    openai_api_key: str
    openai_api_base: str
    llm_model: str
    llm_temp: float

@app.post("/api/test-llm")
def test_llm_connection(req: LLMTestRequest):
    """
    Test connectivity to the LLM API using provided parameters.
    """
    from openai import OpenAI
    import time
    
    api_key = req.openai_api_key.strip()
    api_base = req.openai_api_base.strip()
    model = req.llm_model.strip()
    
    if not api_key:
        return {
            "status": "error",
            "message": "连接测试失败：API Key 不能为空。"
        }
        
    try:
        start_time = time.time()
        client = OpenAI(api_key=api_key, base_url=api_base)
        
        # Test connection with a lightweight prompt
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": "Ping. Respond with exactly 'pong' and nothing else."}
            ],
            temperature=req.llm_temp,
            max_tokens=10
        )
        elapsed_time = round(time.time() - start_time, 2)
        response_text = response.choices[0].message.content.strip()
        
        return {
            "status": "success",
            "message": f"连接成功！响应时间：{elapsed_time} 秒。",
            "response": response_text
        }
    except Exception as e:
        logger.error(f"LLM Connection Test failed: {e}")
        return {
            "status": "error",
            "message": f"连接测试失败：{str(e)}"
        }

# --- Precision Sniper System API Routes ---
class SniperConfigRequest(BaseModel):
    mode: Optional[str] = None
    account_balance: Optional[float] = None
    risk_per_trade_percent: Optional[float] = None
    max_active_trades: Optional[int] = None
    min_confidence: Optional[int] = None
    leverage_mode: Optional[str] = None
    min_leverage: Optional[int] = None
    max_leverage: Optional[int] = None
    fixed_leverage: Optional[int] = None
    live_exchange: Optional[str] = None
    live_api_key: Optional[str] = None
    live_secret: Optional[str] = None
    live_passphrase: Optional[str] = None
    live_trading_mode: Optional[str] = None
    circuit_breaker_enabled: Optional[bool] = None
    daily_max_loss_percent: Optional[float] = None
    pending_ttl_hours: Optional[float] = None
    taker_fee_rate: Optional[float] = None
    maker_fee_rate: Optional[float] = None
    slippage_rate: Optional[float] = None

@app.get("/api/sniper/dashboard")
def get_sniper_dashboard():
    return sniper_engine.get_dashboard_data()

@app.get("/api/sniper/trades")
def get_sniper_trades():
    return {
        "status": "success",
        "trades": sniper_engine.get_trades()
    }

@app.post("/api/sniper/config")
def update_sniper_config(req: SniperConfigRequest):
    new_cfg = {k: v for k, v in req.dict().items() if v is not None}
    updated = sniper_engine.update_config(new_cfg)
    mode_str = updated.get("mode", "paper").upper()
    log_monitor_event(f"⚙️ [狙击系统配置已更新] 运作模式：{mode_str}，单笔风控上限：{updated.get('risk_per_trade_percent')}%，杠杆模式：{updated.get('leverage_mode')}")
    return {
        "status": "success",
        "config": updated
    }

class CloseTradeRequest(BaseModel):
    trade_id: str

@app.post("/api/sniper/close-trade")
def close_trade_manually(req: CloseTradeRequest):
    return sniper_engine.close_position_manually(req.trade_id)

class ResetPaperRequest(BaseModel):
    initial_balance: float = 10000.0

@app.post("/api/sniper/reset-paper")
def reset_paper_trading(req: ResetPaperRequest):
    res = sniper_engine.reset_paper_data(req.initial_balance)
    log_monitor_event(f"🗑️ [模拟盘重置成功] 已清空模拟持仓记录，重置初始模拟资金为 ${req.initial_balance} USD！")
    return res

@app.post("/api/sniper/reset-breaker")
def reset_circuit_breaker():
    """
    Manually release today's circuit breaker for the CURRENT mode and
    re-baseline its day-start balance at the current balance.
    """
    res = sniper_engine.reset_circuit_breaker()
    log_monitor_event(f"🔓 [熔断手动解除] 用户手动复位了日内熔断器（当前模式），盈亏基准已重置。")
    return res

class ExchangeTestRequest(BaseModel):
    exchange_id: str
    api_key: str
    secret: str
    passphrase: Optional[str] = ""

@app.post("/api/sniper/test-exchange-api")
def test_exchange_api(req: ExchangeTestRequest):
    """
    Test connectivity to the selected exchange API and fetch live account balance.
    """
    import ccxt
    fetcher = get_data_fetcher(req.exchange_id)
    try:
        ex_class = getattr(ccxt, req.exchange_id.lower())
        ex_params = {
            "apiKey": req.api_key.strip(),
            "secret": req.secret.strip(),
            "enableRateLimit": True,
            "timeout": 10000
        }
        if req.passphrase and req.passphrase.strip():
            ex_params["password"] = req.passphrase.strip()
        if getattr(fetcher, 'proxies', None):
            ex_params["proxies"] = fetcher.proxies

        ex = ex_class(ex_params)
        balance = ex.fetch_balance()
        usdt_free = balance.get("free", {}).get("USDT", 0.0)
        usdt_total = balance.get("total", {}).get("USDT", 0.0)

        # Also update sniper live balance to match real account if connected
        # successfully (config key is live_account_balance — update_config only
        # writes keys that already exist, "account_balance" would be dropped)
        sniper_engine.update_config({
            "live_exchange": req.exchange_id,
            "live_api_key": req.api_key,
            "live_secret": req.secret,
            "live_passphrase": req.passphrase,
            "live_account_balance": round(usdt_total, 2)
        })

        return {
            "status": "success",
            "message": f"实盘 API 验证成功！连通交易所：{req.exchange_id.upper()}，合约账户可用 USDT 余额：${round(usdt_free, 2)}，总资产：${round(usdt_total, 2)}",
            "usdt_free": usdt_free,
            "usdt_total": usdt_total
        }
    except Exception as e:
        logger.error(f"Exchange API test failed: {e}")
        return {
            "status": "error",
            "message": f"实盘 API 验证失败：{str(e)}。请检查 Key / Secret 及网络 VPN 代理。"
        }

class NotificationTestRequest(BaseModel):
    notify_channels: List[Any]
    telegram_bot_token: Optional[Any] = ""
    telegram_chat_id: Optional[Any] = ""
    serverchan_send_key: Optional[Any] = ""
    bark_device_key: Optional[Any] = ""

@app.post("/api/test-notification")
def test_notification(req: NotificationTestRequest):
    """
    Test connectivity to the selected notification channels.
    """
    import requests
    from datetime import datetime
    
    channels = req.notify_channels
    if not channels:
        return {
            "status": "error",
            "message": "请先选择至少一个通知管道（Telegram, Server酱 或 Bark）"
        }
        
    title = "🔔 飞扬多周期量化终端 - 推送连通性测试"
    time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    content = f"恭喜！您的通知推送配置测试成功。\n测试时间：{time_str}"
    
    results = []
    
    for channel in channels:
        if channel == "telegram":
            token = str(req.telegram_bot_token).strip() if req.telegram_bot_token is not None else ""
            chat_id = str(req.telegram_chat_id).strip() if req.telegram_chat_id is not None else ""
            if not token or not chat_id:
                results.append("Telegram: 缺少 Token 或 Chat ID")
                continue
            
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": f"*{title}*\n\n{content}",
                "parse_mode": "Markdown"
            }
            try:
                response = requests.post(url, json=payload, timeout=10)
                res_data = response.json()
                if response.status_code == 200 and res_data.get("ok"):
                    results.append("Telegram 成功")
                else:
                    results.append(f"Telegram 失败 ({res_data.get('description', response.text)})")
            except Exception as e:
                results.append(f"Telegram 错误 ({str(e)})")
                
        elif channel == "serverchan":
            key = str(req.serverchan_send_key).strip() if req.serverchan_send_key is not None else ""
            if not key:
                results.append("Server酱: 缺少 Send Key")
                continue
            url = f"https://sctapi.ftqq.com/{key}.send"
            payload = {"title": title, "desp": content}
            try:
                response = requests.post(url, data=payload, timeout=10)
                res_data = response.json()
                if response.status_code == 200 and (res_data.get("code") == 0 or "data" in res_data):
                    results.append("Server酱 成功")
                else:
                    results.append(f"Server酱 失败 ({response.text})")
            except Exception as e:
                results.append(f"Server酱 错误 ({str(e)})")
                
        elif channel == "bark":
            key = str(req.bark_device_key).strip() if req.bark_device_key is not None else ""
            if not key:
                results.append("Bark: 缺少 Device Key")
                continue
            url = f"https://api.day.app/{key}/{requests.utils.quote(title)}/{requests.utils.quote(content)}"
            try:
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    results.append("Bark 成功")
                else:
                    results.append(f"Bark 失败 ({response.text})")
            except Exception as e:
                results.append(f"Bark 错误 ({str(e)})")
                
    status = "success" if any("成功" in r for r in results) else "error"
    return {
        "status": status,
        "message": " | ".join(results)
    }

# --- Strategy Prompt Management API ---
class PromptUpdateRequest(BaseModel):
    prompt: str = ""   # empty string restores the built-in default

@app.get("/api/prompt")
def get_strategy_prompt():
    """
    Return the currently active Feiyang system prompt and whether it is a
    user-customized override (feiyang_prompt.txt) or the built-in default.
    """
    custom = ""
    if os.path.exists(PROMPT_PATH):
        try:
            with open(PROMPT_PATH, "r", encoding="utf-8") as f:
                custom = f.read()
        except Exception as e:
            logger.warning(f"Failed to read custom prompt: {e}")
    is_custom = bool(custom.strip())
    return {
        "is_custom": is_custom,
        "prompt": custom if is_custom else FeiyangAgent.DEFAULT_SYSTEM_PROMPT,
    }

@app.post("/api/prompt")
def save_strategy_prompt(req: PromptUpdateRequest):
    """
    Save a custom Feiyang system prompt. Takes effect on the next diagnosis
    (agent is rebuilt per analysis — no restart needed). Empty prompt
    deletes the override and restores the built-in default.
    """
    text = (req.prompt or "").strip()
    try:
        if not text:
            if os.path.exists(PROMPT_PATH):
                os.remove(PROMPT_PATH)
            log_monitor_event("🧠 [策略 Prompt] 已恢复为内置默认 Prompt。")
            return {"status": "success", "is_custom": False, "message": "已恢复为内置默认 Prompt。"}
        if len(text) < 50:
            raise HTTPException(status_code=400, detail="Prompt 太短（< 50 字符），不像是一个完整的策略人设，已拒绝保存。")
        if "```json" not in text or "signal_type" not in text:
            raise HTTPException(status_code=400, detail="Prompt 必须保留 ```json 信号块与 signal_type 字段定义，否则信号将无法解析驱动交易系统。")
        with open(PROMPT_PATH, "w", encoding="utf-8") as f:
            f.write(text)
        log_monitor_event("🧠 [策略 Prompt] 自定义 Prompt 已保存，下一次诊断即刻生效。")
        return {"status": "success", "is_custom": True, "message": "自定义 Prompt 已保存，下一次诊断即刻生效。"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to save custom prompt: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Historical Backtest API Routes ---
class BacktestStartRequest(BaseModel):
    symbol: str
    days: int = 14
    step_hours: int = 4
    max_llm_calls: int = 60
    initial_balance: float = 10000.0

@app.post("/api/backtest/start")
def start_backtest(req: BacktestStartRequest):
    """
    Launch a walk-forward historical backtest in a background thread.
    Each signal point consumes one real LLM call (budget-capped).
    """
    if not req.symbol or "/" not in req.symbol:
        raise HTTPException(status_code=400, detail="交易对格式不正确，示例：BTC/USDT")
    res = backtest_runner.start(
        symbol=req.symbol.strip().upper(),
        days=req.days,
        step_hours=req.step_hours,
        max_llm_calls=req.max_llm_calls,
        initial_balance=req.initial_balance,
    )
    if res.get("status") == "success":
        log_monitor_event(f"📈 [历史回测] 已启动 {req.symbol.upper()} 近 {req.days} 天回放（每 {req.step_hours}h 一个诊断点，LLM 预算 {req.max_llm_calls} 次）")
    return res

@app.get("/api/backtest/status")
def get_backtest_status():
    return backtest_runner.status()

@app.get("/api/backtest/result")
def get_backtest_result():
    res = backtest_runner.result()
    if res is None:
        return {"status": "empty", "message": "暂无已完成的回测结果"}
    return {"status": "success", "result": res}

@app.post("/api/backtest/stop")
def stop_backtest():
    res = backtest_runner.stop()
    if res.get("status") == "success":
        log_monitor_event("⏹ [历史回测] 用户手动停止回测任务")
    return res

def start_background_monitor():
    import time
    from threading import Thread
    
    # 1. Thread 1: 10-Second Fast Price Check & Fill Trigger Loop
    def fast_price_check_loop():
        time.sleep(5)
        logger.info("Fast Price Check Loop (10s) started.")
        while True:
            try:
                yaml_cfg = load_yaml_config()
                symbols = yaml_cfg.get("symbols", ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "ZEC/USDT"])
                exchange_id = yaml_cfg.get("exchange", "binance")
                
                sniper_cfg = sniper_engine.get_config()
                sniper_mode = sniper_cfg.get("mode", "off")
                
                if sniper_mode != "off" and symbols:
                    fetcher = get_data_fetcher(exchange_id)
                    # Batched lightweight ticker fetch (1 request) instead of
                    # per-symbol kline requests on every 10s tick
                    prices_dict = fetcher.fetch_latest_prices(symbols)

                    if prices_dict:
                        sniper_engine.check_market_prices(prices_dict)
            except Exception as e:
                logger.warning(f"[FastPriceCheck] Error in 10s loop: {e}")
            time.sleep(10)

    # 2. Thread 2: 1-Hour LLM Deep Diagnostic Loop
    def hourly_llm_monitor_loop():
        time.sleep(10)
        logger.info("LLM Diagnostic Monitor Loop started.")
        log_monitor_event("🤖 大模型智能诊断后台盯盘服务已启动（已优化为短线15分钟敏捷调频）。")

        while True:
            try:
                yaml_cfg = load_yaml_config()
                scan_mins = int(yaml_cfg.get("scan_interval_minutes", 15))
                enabled = yaml_cfg.get("notifications", {}).get("enabled", False)
                symbols = yaml_cfg.get("symbols", ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "ZEC/USDT"])
                exchange_id = yaml_cfg.get("exchange", "binance")
                fib_lookback = yaml_cfg.get("fibonacci", {}).get("lookback_days", 100)
                
                llm_cfg = yaml_cfg.get("llm", {})
                model_name = llm_cfg.get("model", "gpt-4o")
                temperature = llm_cfg.get("temperature", 0.1)
                max_tokens = llm_cfg.get("max_tokens", 3000)
                
                load_dotenv(override=True)
                api_key = os.getenv("OPENAI_API_KEY")
                api_base = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
                
                sniper_cfg = sniper_engine.get_config()
                sniper_active = (sniper_cfg.get("mode", "off") != "off")
                
                if (enabled or sniper_active) and api_key:
                    log_monitor_event(f"🔄 [{scan_mins}分钟定时诊断] 启动新一轮大模型深度诊盘，目标币种：{', '.join(symbols)}")
                    logger.info(f"[{scan_mins}M LLM Monitor] Starting cycle for symbols: {symbols}")
                    for symbol in symbols:
                        try:
                            log_monitor_event(f"📊 [正在诊断] 币对：{symbol}... 正在拉取多周期 K 线并计算指标")
                            fetcher = get_data_fetcher(exchange_id)
                            timeframes = yaml_cfg.get("timeframes", ["1M", "1W", "1D", "4h", "1h"])
                            raw_dfs = fetcher.fetch_all_timeframes(symbol, timeframes, limit=100)
                                
                            processed_dfs = {}
                            charts_data = {}
                            for tf, df in raw_dfs.items():
                                df_with_indicators = calculate_indicators(df)
                                processed_dfs[tf] = df_with_indicators
                                charts_data[tf] = get_chart_data(df_with_indicators)
                                
                            daily_df = processed_dfs.get("1D")
                            fib_levels = calculate_fibonacci_levels(daily_df, lookback=fib_lookback)
                            payload = clean_and_compress(processed_dfs, fib_levels, symbol)
                            
                            agent = FeiyangAgent(
                                api_key=api_key,
                                api_base=api_base,
                                model_name=model_name,
                                temperature=temperature,
                                max_tokens=max_tokens,
                                system_prompt=load_system_prompt(root_dir)
                            )
                            json_signal, markdown_report = agent.analyze(payload)
                            process_signal_evaluation(symbol, payload, json_signal, markdown_report, yaml_cfg, source_tag=f"{scan_mins}M定时诊断")
                        except Exception as inner_e:
                            logger.error(f"[{scan_mins}M LLM Monitor] Error analyzing {symbol}: {inner_e}")
                            log_monitor_event(f"❌ [诊断失败] {symbol}。原因：{str(inner_e)}")
                    log_monitor_event(f"😴 本轮 {scan_mins} 分钟诊断完成，伏击表格已更新。后台休眠 {scan_mins} 分钟，价格监听（10秒）持续进行中...")
                    
                    # Sleep for scan_mins * 60 seconds, checking every 10 seconds
                    total_iterations = max(1, int(scan_mins * 60 / 10))
                    for _ in range(total_iterations):
                        time.sleep(10)
                else:
                    log_monitor_event("⏳ 自动盯盘后台运行中（未开启通知推送/狙击引擎未启动或未配置 API Key，请前往配置页面检查）")
                    time.sleep(30)
            except Exception as e:
                logger.error(f"[LLM Monitor] Loop error: {e}")
                log_monitor_event(f"⚠️ [盯盘异常] 异常信息：{str(e)}")
                time.sleep(30)

    t_price = Thread(target=fast_price_check_loop)
    t_price.daemon = True
    t_price.start()

    t_llm = Thread(target=hourly_llm_monitor_loop)
    t_llm.daemon = True
    t_llm.start()

@app.on_event("startup")
def startup_event():
    start_background_monitor()

# Mount static frontend build directory if it exists (resolves path under PyInstaller temp dir if frozen)
def get_resource_path(relative_path):
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), relative_path)

frontend_dist = get_resource_path(os.path.join("frontend", "dist"))
if os.path.exists(frontend_dist):
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="static")
