import ccxt
import pandas as pd
import time
import logging
import urllib.request
import os
import socket
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

FALLBACK_EXCHANGES = ['binance', 'okx', 'bybit', 'gate']

# Proxy environment variables only need to be applied once per process,
# not on every DataFetcher instantiation.
_proxy_env_applied = False
_proxy_env_lock = threading.Lock()

def detect_local_vpn_proxy():
    """
    Detect macOS system proxy or common local VPN proxy ports (Clash/Surge/V2Ray).
    """
    try:
        sys_proxies = urllib.request.getproxies()
        if 'http' in sys_proxies or 'https' in sys_proxies:
            p_url = sys_proxies.get('http') or sys_proxies.get('https')
            logger.info(f"Detected macOS system proxy: {p_url}")
            return p_url
    except Exception:
        pass

    common_ports = [7890, 7897, 1087, 1080, 7891]
    for port in common_ports:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.2)
                if s.connect_ex(('127.0.0.1', port)) == 0:
                    proxy_url = f"http://127.0.0.1:{port}"
                    logger.info(f"Detected active local VPN proxy port: {proxy_url}")
                    return proxy_url
        except Exception:
            pass
    return None

class DataFetcher:
    def __init__(self, exchange_id='binance'):
        self.primary_exchange_id = exchange_id.lower()
        self.exchanges = {}
        
        proxy_url = detect_local_vpn_proxy()
        self.proxy_url = proxy_url
        self.proxies = {'http': proxy_url, 'https': proxy_url} if proxy_url else None

        # Apply proxy env vars exactly once per process (global side effect)
        global _proxy_env_applied
        if proxy_url and not _proxy_env_applied:
            with _proxy_env_lock:
                if not _proxy_env_applied:
                    os.environ['HTTP_PROXY'] = proxy_url
                    os.environ['HTTPS_PROXY'] = proxy_url
                    os.environ['http_proxy'] = proxy_url
                    os.environ['https_proxy'] = proxy_url
                    _proxy_env_applied = True

        exchange_order = [self.primary_exchange_id] + [ex for ex in FALLBACK_EXCHANGES if ex != self.primary_exchange_id]
        
        for ex_id in exchange_order:
            try:
                ex_class = getattr(ccxt, ex_id)
                config = {
                    'enableRateLimit': True,
                    'timeout': 5000,
                    'options': {
                        'defaultType': 'spot',
                    }
                }
                if proxy_url:
                    config['proxies'] = {
                        'http': proxy_url,
                        'https': proxy_url
                    }
                self.exchanges[ex_id] = ex_class(config)
            except Exception as e:
                logger.warning(f"Could not initialize exchange {ex_id}: {e}")

    def fetch_ohlcv(self, symbol, timeframe, limit=200):
        normalized_tf = '1M' if timeframe.upper() == '1M' else timeframe.lower()
        
        last_error = None
        for ex_id, exchange in self.exchanges.items():
            try:
                ohlcv = exchange.fetch_ohlcv(symbol, timeframe=normalized_tf, limit=limit)
                if not ohlcv:
                    raise ValueError(f"No OHLCV data returned from {ex_id} for {symbol} - {normalized_tf}")
                
                df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
                
                for col in ['open', 'high', 'low', 'close', 'volume']:
                    df[col] = df[col].astype(float)
                
                return df
            except Exception as e:
                last_error = e
                continue
                
        logger.error(f"All exchanges failed to fetch data for {symbol} - {timeframe}.")
        raise ValueError(f"无法从任何交易所 (Binance/OKX/Bybit/Gate) 获取 {symbol} 的 K 线数据: {last_error}")

    def fetch_all_timeframes(self, symbol, timeframes, limit=100):
        """
        Fetch multiple timeframes concurrently using a thread pool.
        """
        results = {}
        with ThreadPoolExecutor(max_workers=len(timeframes)) as executor:
            future_to_tf = {executor.submit(self.fetch_ohlcv, symbol, tf, limit): tf for tf in timeframes}
            for future in as_completed(future_to_tf):
                tf = future_to_tf[future]
                try:
                    df = future.result()
                    results[tf] = df
                except Exception as e:
                    logger.error(f"Failed to fetch {symbol} ({tf}): {e}")
                    raise e
        return results

    def fetch_latest_prices(self, symbols):
        """
        Fetch the latest prices for a list of symbols in as few requests as
        possible. Tries a single batched fetch_tickers() call per exchange
        first; falls back to lightweight per-symbol 1h klines on failure.

        Returns a dict: {symbol: price_float}
        """
        prices = {}

        # Fast path: one batched tickers request per exchange
        for ex_id, exchange in self.exchanges.items():
            try:
                tickers = exchange.fetch_tickers(symbols)
                for sym in symbols:
                    ticker = tickers.get(sym)
                    if ticker:
                        last = ticker.get('last') or ticker.get('close')
                        if last:
                            prices[sym] = float(last)
                if prices:
                    return prices
            except Exception as e:
                logger.debug(f"fetch_tickers failed on {ex_id}: {e}")
                continue

        # Slow path: per-symbol klines (with cross-exchange fallback inside fetch_ohlcv)
        for sym in symbols:
            try:
                df = self.fetch_ohlcv(sym, timeframe='1h', limit=2)
                if df is not None and not df.empty:
                    prices[sym] = float(df['close'].iloc[-1])
            except Exception as e:
                logger.debug(f"Fallback kline price fetch failed for {sym}: {e}")
        return prices


# --- Process-wide DataFetcher cache -------------------------------------
# Constructing a DataFetcher is expensive (multiple ccxt exchange objects,
# proxy detection, socket probes). The GUI's fast price loop used to build a
# new one every 10 seconds; this factory keeps one instance per exchange id.
_fetcher_cache = {}
_fetcher_cache_lock = threading.Lock()

def get_data_fetcher(exchange_id='binance'):
    """
    Return a shared, process-wide DataFetcher for the given exchange id.
    Thread-safe.
    """
    key = (exchange_id or 'binance').lower()
    with _fetcher_cache_lock:
        fetcher = _fetcher_cache.get(key)
        if fetcher is None:
            fetcher = DataFetcher(exchange_id=key)
            _fetcher_cache[key] = fetcher
        return fetcher
