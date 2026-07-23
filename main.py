import sys
import os
import yaml
import argparse
import time
import logging
from datetime import datetime
from dotenv import load_dotenv

# If running inside a PyInstaller frozen application, force the CWD to the app bundle directory
if getattr(sys, 'frozen', False):
    exec_dir = os.path.dirname(sys.executable)
    if ".app/Contents/MacOS" in exec_dir:
        # Move up 3 levels from Contents/MacOS/FeiyangAgent
        root_dir = os.path.dirname(os.path.dirname(os.path.dirname(exec_dir)))
    else:
        root_dir = exec_dir
    os.chdir(root_dir)

# Import modules from the backend package
from backend.data_fetcher import DataFetcher
from backend.indicators import calculate_indicators, calculate_fibonacci_levels, clean_and_compress
from backend.agent import FeiyangAgent
from backend.notifier import Notifier
from backend.sniper_engine import SniperEngine

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("FeiyangAgentMain")

def load_config(config_path="config.yaml"):
    """
    Load parameters from YAML configuration.
    """
    if not os.path.exists(config_path):
        logger.warning(f"Configuration file {config_path} not found! Falling back to defaults.")
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def run_pipeline(config, dry_run=False, symbol_override=None):
    """
    Execute the core data collection and prediction pipeline.
    """
    logger.info("Starting prediction pipeline execution...")
    
    # 1. Resolve configuration parameters
    symbol = symbol_override or config.get("symbol", "BTC/USDT")
    exchange_id = config.get("exchange", "binance")
    timeframes = config.get("timeframes", ["1M", "1W", "1D", "4h", "1h"])
    fib_lookback = config.get("fibonacci", {}).get("lookback_days", 100)
    
    # 2. Fetch data from CCXT
    fetcher = DataFetcher(exchange_id=exchange_id)
    raw_data_frames = {}
    
    try:
        for tf in timeframes:
            df = fetcher.fetch_ohlcv(symbol, tf, limit=200)
            raw_data_frames[tf] = df
    except Exception as e:
        logger.error(f"Failed to fetch market data: {e}")
        return
        
    # 3. Calculate technical indicators in local Python environment
    logger.info("Calculating technical indicators...")
    processed_data_frames = {}
    for tf, df in raw_data_frames.items():
        try:
            df_with_indicators = calculate_indicators(df)
            processed_data_frames[tf] = df_with_indicators
        except Exception as e:
            logger.error(f"Error calculating indicators for timeframe {tf}: {e}")
            return

    # 4. Compute daily Fibonacci levels based on lookback period
    logger.info(f"Computing Fibonacci levels with {fib_lookback}-day lookback...")
    if "1D" not in processed_data_frames:
        logger.error("Daily (1D) data is required to calculate Fibonacci swing levels.")
        return
        
    try:
        fib_levels = calculate_fibonacci_levels(processed_data_frames["1D"], lookback=fib_lookback)
    except Exception as e:
        logger.error(f"Error calculating Fibonacci levels: {e}")
        return

    # 5. Clean, format, and compress payload
    logger.info("Formatting and compressing payload for LLM...")
    try:
        payload = clean_and_compress(processed_data_frames, fib_levels, symbol)
    except Exception as e:
        logger.error(f"Error building payload: {e}")
        return
        
    # If dry-run, we print the payload and stop
    if dry_run:
        import json
        logger.info("=== DRY RUN: COMPRESSED PAYLOAD JSON ===")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        logger.info("Dry run completed successfully.")
        return

    # 6. Call LLM for Feiyang style prediction
    api_key = os.getenv("OPENAI_API_KEY")
    api_base = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
    
    if not api_key or api_key == "your-llm-api-key":
        logger.error(
            "API key is not configured or still set to the default placeholder! "
            "Please check the OPENAI_API_KEY in your .env file."
        )
        return

    llm_config = config.get("llm", {})
    model_name = llm_config.get("model", "gpt-4o")
    temperature = llm_config.get("temperature", 0.1)
    max_tokens = llm_config.get("max_tokens", 3000)
    
    agent = FeiyangAgent(
        api_key=api_key,
        api_base=api_base,
        model_name=model_name,
        temperature=temperature,
        max_tokens=max_tokens
    )
    
    try:
        json_signal, markdown_report = agent.analyze(payload)
    except Exception as e:
        logger.error(f"Agent analysis failed: {e}")
        return

    # 7. Dispatch notifications
    logger.info("Dispatching notifications...")
    notifier = Notifier(config)
    title = f"飞扬盯盘警报：{symbol}"
    notifier.send_notification(title, markdown_report)
    logger.info("Pipeline execution completed successfully.")

def main():
    # Load environment variables from .env
    load_dotenv()
    
    parser = argparse.ArgumentParser(description="Feiyang Multi-Timeframe Market Prediction Agent")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--symbol", default=None, help="Override symbol (e.g. BTC/USDT)")
    parser.add_argument("--dry-run", action="store_true", help="Print compressed payload without calling LLM")
    parser.add_argument("--loop", action="store_true", help="Run the agent in a loop")
    parser.add_argument("--interval", type=int, default=3600, help="Loop interval in seconds (default: 3600)")
    parser.add_argument("--gui", action="store_true", help="Launch Graphical Dashboard UI in browser")
    parser.add_argument("--backtest", action="store_true", help="Run a walk-forward historical backtest (uses real LLM calls)")
    parser.add_argument("--bt-days", type=int, default=14, help="Backtest window in days (default: 14, max 90)")
    parser.add_argument("--bt-step", type=int, default=4, help="Hours between LLM signal points (default: 4)")
    parser.add_argument("--bt-calls", type=int, default=60, help="Max LLM calls budget (default: 60)")
    parser.add_argument("--bt-balance", type=float, default=10000.0, help="Initial paper balance (default: 10000)")

    args = parser.parse_args()

    config = load_config(args.config)

    if args.backtest:
        from backend.backtest import BacktestRunner
        symbol = args.symbol or config.get("symbol", "BTC/USDT")
        runner = BacktestRunner(os.path.dirname(os.path.abspath(__file__)))
        res = runner.start(
            symbol=symbol,
            days=args.bt_days,
            step_hours=args.bt_step,
            max_llm_calls=args.bt_calls,
            initial_balance=args.bt_balance,
        )
        if res.get("status") != "success":
            logger.error(f"Backtest failed to start: {res.get('message')}")
            return
        logger.info(f"Backtest running (id {res['run_id']}). This consumes real LLM calls...")
        while True:
            st = runner.status()
            print(f"\r[{st['progress_pct']}%] {st['message']}    ", end="", flush=True)
            if not st["running"]:
                print()
                break
            time.sleep(2)
        result = runner.result()
        if result:
            import json as _json
            summary = {k: v for k, v in result.items() if k not in ("trades", "equity_curve")}
            logger.info("=== BACKTEST SUMMARY ===")
            print(_json.dumps(summary, indent=2, ensure_ascii=False))
        else:
            logger.error(f"Backtest error: {runner.status().get('error')}")
        return

    # Default to GUI mode if compiled/frozen (double-clicked) or if --gui is passed
    if args.gui or getattr(sys, 'frozen', False):
        import uvicorn
        import webview
        from threading import Thread

        from backend.app import app

        # 0. Port pre-flight: if 8000 is already bound (almost certainly another
        # running FeiyangAgent instance), abort BEFORE starting anything.
        # Otherwise the health check below would succeed against the OLD
        # instance, this window would show its backend, and BOTH instances'
        # monitor loops would trade in parallel (double LLM cost / double risk).
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            port_in_use = s.connect_ex(("127.0.0.1", 8000)) == 0
        if port_in_use:
            logger.error(
                "❌ 端口 8000 已被占用：检测到另一个 FeiyangAgent 实例正在运行。\n"
                "   请先退出旧实例（检查 /Applications 或其他项目目录下的 FeiyangAgent.app），再启动本应用。"
            )
            sys.exit(1)

        # 1. Run uvicorn server in a background daemon thread
        def run_server():
            logger.info("Starting background FastAPI server on http://127.0.0.1:8000 ...")
            # Pass the FastAPI object directly to prevent import errors in compiled app
            uvicorn.run(app, host="127.0.0.1", port=8000, reload=False, log_level="warning")

        server_thread = Thread(target=run_server)
        server_thread.daemon = True
        server_thread.start()

        # Wait until the backend is actually responding before opening the
        # window (a fixed sleep can race ahead of uvicorn on slow machines).
        # The port was free at pre-flight, so any responder here is ours;
        # also watch for the server thread dying on unexpected bind errors.
        import urllib.request
        server_ready = False
        for _ in range(50):  # up to ~10 seconds
            if not server_thread.is_alive():
                logger.error("❌ 后端服务启动失败（端口绑定异常），请查看日志。")
                sys.exit(1)
            try:
                with urllib.request.urlopen("http://127.0.0.1:8000/api/config", timeout=0.5) as resp:
                    if resp.status == 200:
                        server_ready = True
                        break
            except Exception:
                time.sleep(0.2)
        if not server_ready:
            logger.error("❌ 后端服务在 10 秒内未就绪，放弃打开窗口。请检查日志或端口占用情况。")
            sys.exit(1)

        # 2. Open native Cocoa desktop window (WKWebView wrapper)
        # Cache-bust the URL with the bundled index.html mtime: WKWebView caches
        # index.html heuristically, and without a fresh cache key it can keep
        # showing the PREVIOUS build's UI after an upgrade.
        try:
            if getattr(sys, 'frozen', False):
                _idx = os.path.join(sys._MEIPASS, "frontend", "dist", "index.html")
            else:
                _idx = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "dist", "index.html")
            ui_version = int(os.path.getmtime(_idx))
        except Exception:
            ui_version = int(time.time())

        logger.info("Opening native Cocoa desktop GUI window...")
        window = webview.create_window(
            title="飞扬多周期量化交易终端",
            url=f"http://127.0.0.1:8000/?v={ui_version}",
            width=1472,
            height=850,
            resizable=True
        )

        # Start native desktop event loop (blocking call)
        webview.start()
        
    elif args.loop:
        logger.info(f"Starting continuous mode. Interval: {args.interval}s")
        while True:
            try:
                run_pipeline(config, dry_run=args.dry_run, symbol_override=args.symbol)
            except Exception as e:
                logger.error(f"Unhandled error in pipeline loop: {e}")
            logger.info(f"Sleeping for {args.interval} seconds...")
            time.sleep(args.interval)
    else:
        run_pipeline(config, dry_run=args.dry_run, symbol_override=args.symbol)

if __name__ == "__main__":
    main()
