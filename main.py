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
    
    args = parser.parse_args()
    
    config = load_config(args.config)
    
    # Default to GUI mode if compiled/frozen (double-clicked) or if --gui is passed
    if args.gui or getattr(sys, 'frozen', False):
        import uvicorn
        import webview
        from threading import Thread

        from backend.app import app

        # 1. Run uvicorn server in a background daemon thread
        def run_server():
            logger.info("Starting background FastAPI server on http://127.0.0.1:8000 ...")
            # Pass the FastAPI object directly to prevent import errors in compiled app
            uvicorn.run(app, host="127.0.0.1", port=8000, reload=False, log_level="warning")

        server_thread = Thread(target=run_server)
        server_thread.daemon = True
        server_thread.start()

        # Let the server bind and start up
        time.sleep(0.8)

        # 2. Open native Cocoa desktop window (WKWebView wrapper)
        logger.info("Opening native Cocoa desktop GUI window...")
        window = webview.create_window(
            title="飞扬多周期量化交易终端",
            url="http://127.0.0.1:8000",
            width=1280,
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
