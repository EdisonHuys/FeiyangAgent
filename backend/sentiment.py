"""
Market Sentiment & News Analysis Module for FeiyangAgent.

Data sources:
1. Fear & Greed Index (alternative.me - free, no key needed)
2. Funding Rates (via ccxt exchange API)
3. Economic Calendar (recurring macro events + proximity detection)
4. Crypto News Headlines (CryptoPanic free tier / fallback RSS)

All functions are designed to fail gracefully — if a source is unreachable,
it returns None/empty rather than crashing the pipeline.
"""

import logging
import time
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# Cache to avoid hammering APIs on every 15-min cycle
_cache = {}
_CACHE_TTL = 300  # 5 minutes


def _cache_get(key: str) -> Optional[Any]:
    entry = _cache.get(key)
    if entry and time.time() - entry["ts"] < _CACHE_TTL:
        return entry["data"]
    return None


def _cache_set(key: str, data: Any):
    _cache[key] = {"data": data, "ts": time.time()}


# ═══════════════════════════════════════════════════
# 1. Fear & Greed Index
# ═══════════════════════════════════════════════════

def fetch_fear_greed_index() -> Optional[Dict]:
    """
    Fetch the Crypto Fear & Greed Index from alternative.me.
    Returns: {"value": 72, "label": "Greed", "timestamp": "..."}
    Scale: 0=Extreme Fear, 25=Fear, 50=Neutral, 75=Greed, 100=Extreme Greed
    """
    cached = _cache_get("fear_greed")
    if cached:
        return cached

    try:
        import urllib.request
        url = "https://api.alternative.me/fng/?limit=2"
        req = urllib.request.Request(url, headers={"User-Agent": "FeiyangAgent/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())

        if data.get("data") and len(data["data"]) > 0:
            current = data["data"][0]
            result = {
                "value": int(current["value"]),
                "label": current["value_classification"],
                "timestamp": datetime.fromtimestamp(int(current["timestamp"])).strftime("%Y-%m-%d %H:%M")
            }
            # Also grab yesterday for trend
            if len(data["data"]) > 1:
                prev = data["data"][1]
                result["prev_value"] = int(prev["value"])
                result["trend"] = "rising" if result["value"] > int(prev["value"]) else "falling"
            _cache_set("fear_greed", result)
            return result
    except Exception as e:
        logger.warning(f"[Sentiment] Fear & Greed fetch failed: {e}")
    return None


# ═══════════════════════════════════════════════════
# 2. Funding Rates (via ccxt)
# ═══════════════════════════════════════════════════

def fetch_funding_rates(symbols: List[str], exchange_id: str = "binance") -> Optional[Dict[str, float]]:
    """
    Fetch current funding rates for given symbols.
    Positive = longs pay shorts (bullish sentiment overheated).
    Negative = shorts pay longs (bearish sentiment overheated).
    Returns: {"BTC/USDT": 0.0001, "ETH/USDT": -0.0002, ...}
    """
    cache_key = f"funding_{exchange_id}_{','.join(symbols)}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    try:
        import ccxt

        # Create a dedicated futures/swap exchange instance for funding rate queries.
        # The data_fetcher uses 'spot' type, which cannot reliably fetch funding rates.
        proxy_url = os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("http_proxy") or os.environ.get("https_proxy")
        config = {
            'enableRateLimit': True,
            'timeout': 5000,
            'options': {
                'defaultType': 'future',
            }
        }
        if proxy_url:
            config['proxies'] = {
                'http': proxy_url,
                'https': proxy_url
            }
        ex_class = getattr(ccxt, exchange_id)
        exchange = ex_class(config)

        rates = {}
        for symbol in symbols:
            try:
                # ccxt fetch_funding_rate for perpetual swaps
                swap_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
                fr = exchange.fetch_funding_rate(swap_symbol)
                if fr and fr.get("fundingRate") is not None:
                    rates[symbol] = round(float(fr["fundingRate"]), 6)
            except Exception:
                # Some exchanges/symbols don't support funding rate
                pass

        if rates:
            _cache_set(cache_key, rates)
            return rates
    except Exception as e:
        logger.warning(f"[Sentiment] Funding rate fetch failed: {e}")
    return None


# ═══════════════════════════════════════════════════
# 3. Economic Calendar (Macro Event Proximity)
# ═══════════════════════════════════════════════════

# Known recurring macro events that move crypto markets.
# We check proximity to these dates to warn the LLM.
# Updated periodically — these are approximate recurring schedules.
MACRO_EVENTS_2026 = [
    # FOMC meetings (approximate - Fed meets ~8x/year)
    {"name": "FOMC Rate Decision", "dates": ["2026-01-28", "2026-03-18", "2026-05-06", "2026-06-17", "2026-07-29", "2026-09-16", "2026-11-04", "2026-12-16"], "impact": "critical"},
    # US CPI (monthly, ~13th of each month)
    {"name": "US CPI Data", "dates": ["2026-01-13", "2026-02-12", "2026-03-12", "2026-04-14", "2026-05-13", "2026-06-11", "2026-07-14", "2026-08-12", "2026-09-11", "2026-10-14", "2026-11-12", "2026-12-10"], "impact": "high"},
    # US Non-Farm Payrolls (first Friday of each month)
    {"name": "US Non-Farm Payrolls", "dates": ["2026-01-09", "2026-02-06", "2026-03-06", "2026-04-03", "2026-05-08", "2026-06-05", "2026-07-02", "2026-08-07", "2026-09-04", "2026-10-02", "2026-11-06", "2026-12-04"], "impact": "high"},
    # US PPI
    {"name": "US PPI Data", "dates": ["2026-01-14", "2026-02-13", "2026-03-13", "2026-04-15", "2026-05-14", "2026-06-12", "2026-07-15", "2026-08-13", "2026-09-14", "2026-10-15", "2026-11-13", "2026-12-11"], "impact": "medium"},
]


def check_macro_event_proximity(hours_window: int = 12) -> Optional[Dict]:
    """
    Check if any major macro event is within the next N hours.
    Returns: {"event": "FOMC Rate Decision", "hours_until": 5.2, "impact": "critical"}
    or None if no events nearby.
    """
    now = datetime.now(timezone.utc)
    nearest = None
    nearest_hours = float("inf")

    # Detect calendar expiry: warn if no events exist for the current year
    current_year = str(now.year)
    has_current_year_events = any(
        current_year in d for eg in MACRO_EVENTS_2026 for d in eg["dates"]
    )
    if not has_current_year_events:
        logger.warning(
            f"[Sentiment] Macro calendar has no events for {current_year}. "
            f"Please update MACRO_EVENTS_2026 in sentiment.py with {current_year} dates."
        )

    for event_group in MACRO_EVENTS_2026:
        for date_str in event_group["dates"]:
            try:
                # Events typically release at 8:30 AM ET (13:30 UTC) or 2:00 PM ET (19:00 UTC)
                event_dt = datetime.strptime(date_str, "%Y-%m-%d").replace(
                    hour=13, minute=30, tzinfo=timezone.utc
                )
                hours_until = (event_dt - now).total_seconds() / 3600.0

                # Check both before and after (events can have delayed impact)
                if -2 <= hours_until <= hours_window:
                    abs_hours = abs(hours_until)
                    if abs_hours < nearest_hours:
                        nearest_hours = abs_hours
                        nearest = {
                            "event": event_group["name"],
                            "hours_until": round(hours_until, 1),
                            "impact": event_group["impact"],
                            "date": date_str
                        }
            except Exception:
                continue

    return nearest


# ═══════════════════════════════════════════════════
# 4. Crypto News Headlines
# ═══════════════════════════════════════════════════

def fetch_crypto_news(max_items: int = 5) -> Optional[List[Dict]]:
    """
    Fetch recent crypto news headlines from CoinTelegraph and CoinDesk RSS feeds.
    Returns: [{"title": "...", "source": "...", "published": "...", "currencies": []}]
    """
    cached = _cache_get("crypto_news")
    if cached:
        return cached

    news = []
    
    # List of RSS feeds to try
    feeds = [
        {"url": "https://cointelegraph.com/rss", "source": "CoinTelegraph"},
        {"url": "https://www.coindesk.com/arc/outboundfeeds/rss/", "source": "CoinDesk"}
    ]

    for feed in feeds:
        try:
            import urllib.request
            import xml.etree.ElementTree as ET
            from email.utils import parsedate_to_datetime

            url = feed["url"]
            req = urllib.request.Request(
                url, 
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                xml_data = resp.read()
                root = ET.fromstring(xml_data)
                items = root.findall('.//item')
                
                for item in items:
                    title_elem = item.find('title')
                    pub_elem = item.find('pubDate')
                    
                    if title_elem is not None:
                        title = title_elem.text.strip()
                        
                        # Format the publish time
                        published_str = ""
                        if pub_elem is not None and pub_elem.text:
                            try:
                                dt = parsedate_to_datetime(pub_elem.text)
                                published_str = dt.strftime("%Y-%m-%d %H:%M")
                            except Exception:
                                published_str = pub_elem.text[:16]
                                
                        news.append({
                            "title": title[:120],
                            "source": feed["source"],
                            "published": published_str,
                            "currencies": []
                        })
                        
                        if len(news) >= max_items:
                            break
            
            if len(news) >= max_items:
                break
        except Exception as e:
            logger.debug(f"[Sentiment] RSS fetch failed for {feed['source']}: {e}")

    if news:
        _cache_set("crypto_news", news)
        return news

    return None


# ═══════════════════════════════════════════════════
# 5. Aggregate: Build Market Context for LLM
# ═══════════════════════════════════════════════════

def build_market_context(symbols: List[str] = None, exchange_id: str = "binance") -> Dict[str, Any]:
    """
    Aggregate all sentiment/news data into a single context dict
    that gets injected into the LLM payload.

    Returns a dict like:
    {
        "fear_greed": {"value": 72, "label": "Greed", "trend": "rising"},
        "funding_rates": {"BTC/USDT": 0.0001, ...},
        "macro_event": {"event": "FOMC", "hours_until": 5.2, "impact": "critical"},
        "news_headlines": [{"title": "...", ...}],
        "risk_level": "elevated",  # "low" / "normal" / "elevated" / "extreme"
        "trading_bias": "cautious"  # "aggressive" / "normal" / "cautious" / "stand_aside"
    }
    """
    if symbols is None:
        symbols = ["BTC/USDT", "ETH/USDT"]

    context = {}

    # 1. Fear & Greed
    fng = fetch_fear_greed_index()
    if fng:
        context["fear_greed"] = fng

    # 2. Funding Rates
    rates = fetch_funding_rates(symbols, exchange_id)  # Fetch for all monitored symbols
    if rates:
        context["funding_rates"] = rates

    # 3. Macro Event Proximity
    macro = check_macro_event_proximity(hours_window=24)
    if macro:
        context["macro_event"] = macro

    # 4. News Headlines
    news = fetch_crypto_news(max_items=5)
    if news:
        context["news_headlines"] = news

    # 5. Compute overall risk level and trading bias
    risk_score = 0  # Higher = more dangerous

    # Fear & Greed extremes
    if fng:
        val = fng["value"]
        if val >= 90 or val <= 10:
            risk_score += 3  # Extreme
        elif val >= 80 or val <= 20:
            risk_score += 2  # Elevated
        elif val >= 70 or val <= 30:
            risk_score += 1  # Mild caution

    # Macro event proximity
    if macro:
        if macro["impact"] == "critical" and abs(macro["hours_until"]) < 6:
            risk_score += 4  # FOMC within 6h = very dangerous
        elif macro["impact"] == "critical":
            risk_score += 2
        elif macro["impact"] == "high" and abs(macro["hours_until"]) < 4:
            risk_score += 2
        elif macro["impact"] == "high":
            risk_score += 1

    # Funding rate extremes (> 0.05% or < -0.05% per 8h = overheated)
    if rates:
        for sym, rate in rates.items():
            if abs(rate) > 0.0005:
                risk_score += 1
                break

    # Map risk score to actionable labels
    if risk_score >= 6:
        context["risk_level"] = "extreme"
        context["trading_bias"] = "stand_aside"
    elif risk_score >= 4:
        context["risk_level"] = "elevated"
        context["trading_bias"] = "cautious"
    elif risk_score >= 2:
        context["risk_level"] = "normal"
        context["trading_bias"] = "normal"
    else:
        context["risk_level"] = "low"
        context["trading_bias"] = "aggressive"

    return context
