import ccxt
import pandas as pd
import time
import logging
import urllib.request
import urllib.parse
import json
import os
import socket
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

FALLBACK_EXCHANGES = ['binance', 'okx', 'bybit', 'gate']
BINANCE_VISION_BASE = "https://data-api.binance.vision/api/v3"
_GEO_BLOCK_TTL_SEC = 300.0  # Cache HTTP 451/403 geo-blocked exchange status for 5 minutes

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
        self._exchange_locks = {}
        self._blocked_exchanges = {}  # {ex_id: blocked_at_ts}
        
        proxy_url = detect_local_vpn_proxy()
        self.proxy_url = proxy_url
        self.proxies = {'http': proxy_url, 'https': proxy_url} if proxy_url else None

        # Apply proxy env vars once per process, while bypassing proxy for domestic LLM APIs
        global _proxy_env_applied
        if proxy_url and not _proxy_env_applied:
            with _proxy_env_lock:
                if not _proxy_env_applied:
                    os.environ['HTTP_PROXY'] = proxy_url
                    os.environ['HTTPS_PROXY'] = proxy_url
                    os.environ['http_proxy'] = proxy_url
                    os.environ['https_proxy'] = proxy_url
                    no_proxy_domains = "localhost,127.0.0.1,.volces.com,.deepseek.com,.aliyuncs.com,.moonshot.cn"
                    os.environ.setdefault('NO_PROXY', no_proxy_domains)
                    os.environ.setdefault('no_proxy', no_proxy_domains)
                    _proxy_env_applied = True

        exchange_order = [self.primary_exchange_id] + [ex for ex in FALLBACK_EXCHANGES if ex != self.primary_exchange_id]
        
        for ex_id in exchange_order:
            try:
                ex_class = getattr(ccxt, ex_id)
                config = {
                    'enableRateLimit': True,
                    'timeout': 8000,
                    'has': {'fetchCurrencies': False},
                    'options': {
                        'defaultType': 'spot',
                        'fetchCurrencies': False,
                    }
                }
                if proxy_url:
                    config['proxies'] = {
                        'http': proxy_url,
                        'https': proxy_url
                    }
                self.exchanges[ex_id] = ex_class(config)
                self._exchange_locks[ex_id] = threading.Lock()
            except Exception as e:
                logger.warning(f"Could not initialize exchange {ex_id}: {e}")

    @property
    def exchange(self):
        """Return the primary exchange CCXT instance."""
        return self.exchanges.get(self.primary_exchange_id)

    def _is_real_ccxt_exchange(self, exchange) -> bool:
        ex_cls = getattr(ccxt, "Exchange", None)
        if isinstance(ex_cls, type):
            return isinstance(exchange, ex_cls)
        return not type(exchange).__name__.endswith("Mock")

    def _is_geo_or_waf_blocked_error(self, err: Exception) -> bool:
        msg = str(err)
        return ("451" in msg or "403 Forbidden" in msg or "restricted location" in msg or "CloudFront" in msg)

    def _fetch_binance_vision_ohlcv(self, symbol: str, normalized_tf: str, limit: int):
        """
        Lightweight direct REST fallback to data-api.binance.vision/api/v3/klines.
        Bypasses api.binance.com HTTP 451 geo-blocks and avoids 25MB /exchangeInfo downloads.
        """
        is_thousand_contract = symbol.upper().startswith("1000")
        base_spot_symbol = symbol.replace("1000", "", 1) if is_thousand_contract else symbol
        raw_pair = base_spot_symbol.split(":")[0].replace("/", "").upper()
        url = f"{BINANCE_VISION_BASE}/klines?symbol={urllib.parse.quote(raw_pair)}&interval={urllib.parse.quote(normalized_tf)}&limit={int(limit)}"
        req = urllib.request.Request(url, headers={"User-Agent": "FeiyangAgent/1.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not data or not isinstance(data, list):
            return None
        rows = [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in data]
        df = pd.DataFrame(rows, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
        if is_thousand_contract:
            for p_col in ['open', 'high', 'low', 'close']:
                df[p_col] = df[p_col] * 1000.0
            df['volume'] = df['volume'] / 1000.0
        return df

    def _fetch_binance_vision_prices(self, symbols) -> dict:
        """
        Lightweight direct REST fallback to data-api.binance.vision/api/v3/ticker/price.
        """
        prices = {}
        req = urllib.request.Request(f"{BINANCE_VISION_BASE}/ticker/price", headers={"User-Agent": "FeiyangAgent/1.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            items = json.loads(resp.read().decode("utf-8"))
        if not isinstance(items, list):
            return prices
        price_map = {item["symbol"]: float(item["price"]) for item in items if "symbol" in item and "price" in item}
        for sym in symbols:
            clean = sym.split(":")[0].replace("/", "").upper()
            if clean in price_map:
                prices[sym] = price_map[clean]
            elif sym.upper().startswith("1000"):
                base_clean = clean.replace("1000", "", 1)
                if base_clean in price_map:
                    prices[sym] = price_map[base_clean] * 1000.0
            else:
                th_clean = f"1000{clean}"
                if th_clean in price_map:
                    prices[sym] = price_map[th_clean] / 1000.0
        return prices

    def fetch_ohlcv(self, symbol, timeframe, limit=200):
        normalized_tf = '1M' if timeframe.upper() == '1M' else timeframe.lower()
        
        is_thousand_contract = symbol.upper().startswith("1000")
        base_spot_symbol = symbol.replace("1000", "", 1) if is_thousand_contract else symbol
        
        last_error = None
        for ex_id, exchange in self.exchanges.items():
            is_real_ex = self._is_real_ccxt_exchange(exchange)
            if is_real_ex:
                blocked_at = self._blocked_exchanges.get(ex_id, 0.0)
                if blocked_at and (time.time() - blocked_at) < _GEO_BLOCK_TTL_SEC:
                    if ex_id == "binance":
                        try:
                            df_vis = self._fetch_binance_vision_ohlcv(symbol, normalized_tf, limit)
                            if df_vis is not None and not df_vis.empty:
                                return df_vis
                        except Exception as ve:
                            last_error = ve
                    continue

            candidate_symbols = [symbol]
            if is_thousand_contract:
                candidate_symbols.append(base_spot_symbol)
            elif not symbol.upper().startswith("1000"):
                candidate_symbols.append(f"1000{symbol}")
                
            for idx, cand_sym in enumerate(candidate_symbols):
                try:
                    # Serialize initial load_markets() per exchange to avoid thread-pool stampede
                    if is_real_ex and not getattr(exchange, "markets", None):
                        lock = self._exchange_locks.setdefault(ex_id, threading.Lock())
                        with lock:
                            if not getattr(exchange, "markets", None):
                                exchange.load_markets()

                    ohlcv = exchange.fetch_ohlcv(cand_sym, timeframe=normalized_tf, limit=limit)
                    if not ohlcv:
                        continue
                    
                    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                    df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
                    
                    for col in ['open', 'high', 'low', 'close', 'volume']:
                        df[col] = df[col].astype(float)
                        
                    # If requested 1000PEPE but fetched PEPE from spot: multiply price by 1000
                    if is_thousand_contract and cand_sym == base_spot_symbol:
                        for p_col in ['open', 'high', 'low', 'close']:
                            df[p_col] = df[p_col] * 1000.0
                        df['volume'] = df['volume'] / 1000.0
                    # If requested PEPE but fetched 1000PEPE: divide price by 1000
                    elif (not is_thousand_contract) and cand_sym.startswith("1000"):
                        for p_col in ['open', 'high', 'low', 'close']:
                            df[p_col] = df[p_col] / 1000.0
                        df['volume'] = df['volume'] * 1000.0
                    
                    return df
                except Exception as e:
                    last_error = e
                    if is_real_ex and self._is_geo_or_waf_blocked_error(e):
                        self._blocked_exchanges[ex_id] = time.time()
                        if ex_id == "binance":
                            try:
                                df_vis = self._fetch_binance_vision_ohlcv(symbol, normalized_tf, limit)
                                if df_vis is not None and not df_vis.empty:
                                    return df_vis
                            except Exception as ve:
                                last_error = ve
                        break
                    # Only try 1000x alternate symbol if the primary symbol raised BadSymbol
                    bad_sym_cls = getattr(ccxt, "BadSymbol", None)
                    is_bad_sym = (isinstance(bad_sym_cls, type) and isinstance(e, bad_sym_cls)) or ("does not have market symbol" in str(e))
                    if idx == 0 and is_real_ex and not is_bad_sym:
                        if ex_id == "binance":
                            try:
                                df_vis = self._fetch_binance_vision_ohlcv(symbol, normalized_tf, limit)
                                if df_vis is not None and not df_vis.empty:
                                    return df_vis
                            except Exception:
                                pass
                        break
                    continue
                
        logger.error(f"All exchanges failed to fetch data for {symbol} - {timeframe}.")
        raise ValueError(f"无法从任何交易所 (Binance/OKX/Bybit/Gate) 获取 {symbol} 的 K 线数据: {last_error}")

    def fetch_all_timeframes(self, symbol, timeframes, limit=100):
        """
        Fetch multiple timeframes concurrently using a thread pool.
        """
        if not timeframes:
            return {}
        results = {}
        with ThreadPoolExecutor(max_workers=max(1, len(timeframes))) as executor:
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
        Fetch the latest prices for a list of symbols in as few requests as possible.
        """
        prices = {}

        # Fast path: one batched tickers request per exchange
        for ex_id, exchange in self.exchanges.items():
            is_real_ex = self._is_real_ccxt_exchange(exchange)
            if is_real_ex:
                blocked_at = self._blocked_exchanges.get(ex_id, 0.0)
                if blocked_at and (time.time() - blocked_at) < _GEO_BLOCK_TTL_SEC:
                    if ex_id == "binance":
                        try:
                            vis_prices = self._fetch_binance_vision_prices([s for s in symbols if s not in prices])
                            prices.update(vis_prices)
                            if len(prices) == len(symbols):
                                return prices
                        except Exception:
                            pass
                    continue
            try:
                tickers = exchange.fetch_tickers()
                for sym in symbols:
                    if sym in prices:
                        continue
                    ticker = tickers.get(sym) or tickers.get(sym.split(":")[0]) or tickers.get(f"{sym}:USDT")
                    if not ticker and sym.startswith("1000"):
                        base_sym = sym.replace("1000", "", 1)
                        ticker_base = tickers.get(base_sym) or tickers.get(f"{base_sym}:USDT")
                        if ticker_base:
                            last = ticker_base.get('last') or ticker_base.get('close')
                            if last:
                                prices[sym] = float(last) * 1000.0
                    elif not ticker and not sym.startswith("1000"):
                        thousand_sym = f"1000{sym}"
                        ticker_th = tickers.get(thousand_sym) or tickers.get(f"{thousand_sym}:USDT")
                        if ticker_th:
                            last = ticker_th.get('last') or ticker_th.get('close')
                            if last:
                                prices[sym] = float(last) / 1000.0
                    elif ticker:
                        last = ticker.get('last') or ticker.get('close')
                        if last:
                            prices[sym] = float(last)
                if len(prices) == len(symbols):
                    return prices
            except Exception as e:
                if is_real_ex and self._is_geo_or_waf_blocked_error(e):
                    self._blocked_exchanges[ex_id] = time.time()
                if is_real_ex and ex_id == "binance":
                    try:
                        vis_prices = self._fetch_binance_vision_prices([s for s in symbols if s not in prices])
                        prices.update(vis_prices)
                        if len(prices) == len(symbols):
                            return prices
                    except Exception:
                        pass
                logger.debug(f"fetch_tickers failed on {ex_id}: {e}")
                continue

        # Slow path
        for sym in symbols:
            if sym not in prices:
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
