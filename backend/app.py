import os
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

from data_fetcher import DataFetcher
from indicators import calculate_indicators, calculate_fibonacci_levels, clean_and_compress
from agent import FeiyangAgent
from notifier import Notifier

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("FeiyangBackend")

# Load environment variables
load_dotenv()

app = FastAPI(title="Feiyang Agent API")

import threading
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

# Enable CORS for local frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins in development
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

        if should_push:
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

            notifier = Notifier(yaml_cfg)
            notifier.send_notification(f"{source_tag}：{symbol}", signal_header + markdown_report)
            log_monitor_event(f"📬 [{source_tag}] {symbol} 交易警报已送达。")

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
    llm_model: str
    llm_temp: float
    llm_max_tokens: int
    notify_enabled: bool
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

def get_chart_data(df):
    chart_data = []
    for _, row in df.iterrows():
        record = {
            "time": int(row['timestamp'] / 1000),  # UNIX timestamp in seconds
            "open": float(row['open']),
            "high": float(row['high']),
            "low": float(row['low']),
            "close": float(row['close']),
            "volume": float(row['volume']),
        }
        # Add technical indicators if they are not NaN
        cols = [
            'MA5', 'MA10', 'MA30', 'EMA55',
            'BB_Lower', 'BB_Middle', 'BB_Upper',
            'RSI_14', 'KDJ_K', 'KDJ_D', 'KDJ_J',
            'MACD_DIF', 'MACD_Hist', 'MACD_DEA'
        ]
        for col in cols:
            val = row.get(col)
            if pd.notna(val):
                record[col.lower()] = float(val)
        chart_data.append(record)
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
        "llm_model": yaml_cfg.get("llm", {}).get("model", "gpt-4o"),
        "llm_temp": yaml_cfg.get("llm", {}).get("temperature", 0.1),
        "llm_max_tokens": yaml_cfg.get("llm", {}).get("max_tokens", 3000),
        "notify_enabled": yaml_cfg.get("notifications", {}).get("enabled", False),
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
                "channels": cfg.notify_channels,
                "telegram": {
                    "chat_id": cfg.telegram_chat_id
                }
            }
        }
        write_yaml_config(yaml_cfg)
        
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
        log_monitor_event("运行日志已清空。正在持续监控中...")
    return {"status": "success"}

@app.get("/api/market")
def get_market_data(symbol: str = "BTC/USDT"):
    """
    Fetch market OHLCV data and calculate all indicators for visualization.
    """
    yaml_cfg = load_yaml_config()
    exchange_id = yaml_cfg.get("exchange", "binance")
    timeframes = yaml_cfg.get("timeframes", ["1M", "1W", "1D", "4h", "1h"])
    fib_lookback = yaml_cfg.get("fibonacci", {}).get("lookback_days", 100)
    
    fetcher = DataFetcher(exchange_id=exchange_id)
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
        
    return {
        "symbol": symbol,
        "fibonacci_levels": fib_levels,
        "charts": charts_data,
        "payload": payload
    }

@app.post("/api/analyze")
def run_analysis(req: AnalysisRequest):
    """
    Run full prediction pipeline: fetch market data -> compute indicators -> call LLM -> notify -> return response
    """
    symbol = req.symbol
    logger.info(f"Triggering diagnostic analysis for symbol: {symbol}")
    log_monitor_event(f"⚡ [手动触发] 手动开启 {symbol} 诊断，正在拉取 K 线并计算指标...")
    
    # 1. Fetch market data
    market_data = get_market_data(symbol)
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
        max_tokens=max_tokens
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

def start_background_monitor():
    import time
    from threading import Thread
    from notifier import Notifier
    
    def monitor_loop():
        try:
            print("MONITOR_LOOP THREAD STARTED!!!")
            logger.info("24H Background Monitor Loop started.")
            log_monitor_event("24H 自动盯盘后台服务已启动。正在持续监控市场...")
        except Exception as ex:
            print("CRITICAL ERROR IN MONITOR_LOOP STARTUP:", ex)
            import traceback
            traceback.print_exc()
            return
            
        last_signals = {} # {symbol: signal_type}
        
        # Let the server bind and start up fully first
        time.sleep(10)
        
        while True:
            try:
                yaml_cfg = load_yaml_config()
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
                
                # Only analyze if notifications are enabled and API Key is configured
                if enabled and api_key:
                    log_monitor_event(f"🔄 启动新一轮自动盯盘轮询，监控币对：{', '.join(symbols)}")
                    logger.info(f"[24H Monitor] Starting cycle for symbols: {symbols}")
                    for symbol in symbols:
                        try:
                            log_monitor_event(f"📊 [正在诊断] 币对：{symbol}... 正在拉取 Binance / OKX / Bybit 实时行情并计算指标")
                            fetcher = DataFetcher(exchange_id=exchange_id)
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
                                max_tokens=max_tokens
                            )
                            json_signal, markdown_report = agent.analyze(payload)
                            process_signal_evaluation(symbol, payload, json_signal, markdown_report, yaml_cfg, source_tag="24H盯盘")
                        except Exception as inner_e:
                            logger.error(f"[24H Monitor] Error analyzing {symbol}: {inner_e}")
                            log_monitor_event(f"❌ [诊断失败] {symbol}。原因：{str(inner_e)}")
                    log_monitor_event("😴 本轮自选盯盘完成。后台线程将休眠等待 15 分钟，随后自动启动下一轮轮询...")
                else:
                    log_monitor_event("⏳ 自动盯盘后台运行中（未开启通知推送或未配置 API Key，将暂不执行分析流程，请前往“核心配置参数”页面检查）")
            except Exception as e:
                logger.error(f"[24H Monitor] Loop error: {e}")
                log_monitor_event(f"⚠️ [盯盘异常] 异常信息：{str(e)}")
                
            # Wait 15 minutes between monitoring cycles to prevent Binance rate limits
            time.sleep(900)

    t = Thread(target=monitor_loop)
    t.daemon = True
    t.start()

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
