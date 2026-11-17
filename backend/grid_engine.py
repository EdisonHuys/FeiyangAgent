"""
GridEngine: Quantitative Arithmetic Grid Trading & Reduce-Only Recovery Engine
Inspired by WGALL-ZJ005 with native CCXT exchange integration, post-only Maker execution,
two-stage authoritative reconciliation, and crash-safe state persistence.
"""

import os
import json
import time
import math
import logging
import threading
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger("GridEngine")

# Helper math functions
def round_to(val: float, precision: int = 8) -> float:
    return round(float(val), precision)

def round2(val: float) -> float:
    return round(float(val), 2)

def get_symbol_precisions(symbol: str, price: float = 0.0) -> Tuple[int, int]:
    """
    Return (price_precision, amount_precision) dynamically based on symbol and price scale.
    """
    sym = symbol.upper()
    if "BTC" in sym:
        return 1, 3
    elif "ETH" in sym:
        return 2, 3
    elif "BNB" in sym or "SOL" in sym:
        return 2, 2
    elif "NEAR" in sym or "AVAX" in sym or "ADA" in sym or "DOGE" in sym or "PEPE" in sym or "SHIB" in sym or "BONK" in sym or "FLOKI" in sym:
        p_p = 3 if ("NEAR" in sym or "AVAX" in sym) else (4 if "ADA" in sym else (5 if "DOGE" in sym else 7))
        return p_p, 0
    elif "SUI" in sym or "XRP" in sym:
        return 4, 1
    elif "LINK" in sym:
        return 3, 2
    
    if price <= 0:
        return 4, 2
    
    if price >= 1000:
        return 1, 3
    elif price >= 100:
        return 2, 2
    elif price >= 1.0:
        return 4, 2
    elif price >= 0.01:
        return 6, 1
    elif price >= 0.0001:
        return 8, 0
    else:
        return 10, 0

def build_grid(lower: float, upper: float, grid_count: int, symbol: str = "BTC/USDT") -> Dict[str, Any]:
    """
    Build an arithmetic (equal-spacing) grid with symbol-specific tick precision.
    levels[0] = lower ... levels[grid_count] = upper.
    """
    if not (upper > lower):
        raise ValueError("upper 必须大于 lower")
    if not (grid_count >= 2):
        raise ValueError("grid_count 至少为 2")

    prec, _ = get_symbol_precisions(symbol, lower)
    min_step = 10 ** (-prec) if prec > 0 else 1.0
    if (upper - lower) < min_step * grid_count:
        upper = round_to(lower + min_step * grid_count, prec)

    spacing = max(min_step, round_to((upper - lower) / float(grid_count), prec))
    levels = [round_to(lower + i * spacing, prec) for i in range(grid_count + 1)]
    return {
        "levels": levels,
        "spacing": round_to(spacing, prec),
        "count": grid_count,
        "lower": lower,
        "upper": levels[-1]
    }

def is_reduce_only(side: str, mode: str) -> bool:
    """
    Reduce-only flag for an order side under a given mode.
    - long grid: sell orders only close longs (reduce-only)
    - short grid: buy orders only close shorts (reduce-only)
    - neutral grid: both sides can open
    """
    side = side.lower()
    mode = mode.lower()
    if mode == "long":
        return side == "sell"
    if mode == "short":
        return side == "buy"
    return False

def compute_level_size(level_idx: int, total_levels: int, price: float, levels: List[float], base_size: float, mode: str, sizing_mode: str = "pyramid") -> Tuple[float, float]:
    """
    Compute order quantity and multiplier for a given grid rung.
    - uniform mode: 1.0x base_size
    - pyramid mode:
        - long mode: deep buys below market price scale from 0.8x near price to 1.5x at bottom support.
        - short mode: deep sells above market price scale from 0.8x near price to 1.5x at top resistance.
        - neutral mode: scale from 0.8x near price to 1.4x at outer bounds.
    Returns: (computed_size, multiplier)
    """
    if sizing_mode != "pyramid" or total_levels <= 1:
        return base_size, 1.0

    closest_idx = min(range(len(levels)), key=lambda k: abs(levels[k] - price))
    
    if mode == "long":
        denom = max(1, closest_idx)
        mult = 1.5 - 0.7 * (min(level_idx, denom) / denom)
    elif mode == "short":
        span = max(1, (total_levels - 1) - closest_idx)
        dist = max(0, level_idx - closest_idx)
        mult = 0.8 + 0.7 * (dist / span)
    else:  # neutral
        if level_idx < closest_idx:
            denom = max(1, closest_idx)
            mult = 1.4 - 0.6 * (min(level_idx, denom) / denom)
        else:
            span = max(1, (total_levels - 1) - closest_idx)
            dist = max(0, level_idx - closest_idx)
            mult = 0.8 + 0.6 * (dist / span)

    mult = max(0.5, min(2.5, mult))
    computed_size = round_to(base_size * mult, 6)
    return computed_size, round2(mult)

def seed_orders(levels: List[float], price: float, mode: str, spacing: float, base_size: float = 0.001, sizing_mode: str = "pyramid", skip_band: float = 0.25) -> List[Dict[str, Any]]:
    """
    Initial orders to place given current price and mode with pyramid / uniform sizing.
    Supports bi-directional grid orders with initial position coverage.
    - long mode: buys below price (dip-buy), sells above price (take-profit reduce-only)
    - short mode: sells above price (high-sell), buys below price (take-profit reduce-only)
    - neutral mode: buys below price, sells above price
    """
    band = spacing * skip_band
    orders = []
    total_lvls = len(levels)
    for i, lvl in enumerate(levels):
        if abs(lvl - price) < band:
            continue
        order_size, mult = compute_level_size(i, total_lvls, price, levels, base_size, mode, sizing_mode)
        if lvl < price:
            side = "buy"
            p_side = "LONG" if mode in ("long", "neutral") else "SHORT"
            orders.append({
                "level_index": i,
                "price": lvl,
                "side": side,
                "pos_side": p_side,
                "size_base": order_size,
                "size_multiplier": mult,
                "reduce_only": is_reduce_only(side, mode)
            })
        elif lvl > price:
            side = "sell"
            p_side = "SHORT" if mode in ("short", "neutral") else "LONG"
            orders.append({
                "level_index": i,
                "price": lvl,
                "side": side,
                "pos_side": p_side,
                "size_base": order_size,
                "size_multiplier": mult,
                "reduce_only": is_reduce_only(side, mode)
            })
    return orders

def replacement_for(filled: Dict[str, Any], levels: List[float], mode: str) -> Optional[Dict[str, Any]]:
    """
    Given a filled order, return the replacement order to place one rung away:
    - Buy filled at i -> Sell at i + 1 (locks in one grid of profit), matching the filled size.
    - Sell filled at i -> Buy at i - 1.
    Preserves exact pos_side for Binance Hedge (dual-side position) mode.
    """
    level_index = filled.get("level_index", 0)
    side = filled.get("side", "").lower()
    size_base = filled.get("size_base", 0.001)
    size_multiplier = filled.get("size_multiplier", 1.0)
    pos_side = filled.get("pos_side")
    
    if side == "buy":
        j = level_index + 1
        if j > len(levels) - 1:
            return None
        target_pos_side = pos_side if pos_side else ("LONG" if mode in ("long", "neutral") else "SHORT")
        return {
            "level_index": j,
            "price": levels[j],
            "side": "sell",
            "pos_side": target_pos_side,
            "size_base": size_base,
            "size_multiplier": size_multiplier,
            "reduce_only": (target_pos_side == "LONG" and mode != "short") or is_reduce_only("sell", mode)
        }
    else:
        j = level_index - 1
        if j < 0:
            return None
        target_pos_side = pos_side if pos_side else ("SHORT" if mode in ("short", "neutral") else "LONG")
        return {
            "level_index": j,
            "price": levels[j],
            "side": "buy",
            "pos_side": target_pos_side,
            "size_base": size_base,
            "size_multiplier": size_multiplier,
            "reduce_only": (target_pos_side == "SHORT" and mode != "long") or is_reduce_only("buy", mode)
        }

def get_exchange_market_symbol(exchange, symbol: str) -> str:
    """Resolve symbol to exact market symbol recognized by exchange."""
    if not exchange:
        return f"{symbol}:USDT" if ":" not in symbol else symbol
        
    markets = getattr(exchange, 'markets', {}) or {}
    if not markets:
        try:
            markets = exchange.load_markets()
        except Exception:
            pass
            
    # 1. Exact swap contract format: e.g. "1000PEPE/USDT:USDT" or "BTC/USDT:USDT"
    candidate1 = f"{symbol}:USDT" if ":" not in symbol else symbol
    if candidate1 in markets and markets[candidate1].get("swap", True):
        return candidate1
        
    # 2. Try adding "1000" prefix for small unit tokens: e.g. PEPE -> 1000PEPE
    parts = symbol.split(":")[0].split("/")
    base = parts[0]
    quote = parts[1] if len(parts) > 1 else "USDT"
    if not base.startswith("1000"):
        thousand_sym = f"1000{base}/{quote}:USDT"
        if thousand_sym in markets:
            return thousand_sym
        thousand_raw = f"1000{base}/{quote}"
        if thousand_raw in markets and markets[thousand_raw].get("swap", False):
            return thousand_raw
            
    # 3. Try stripping "1000" prefix:
    if base.startswith("1000"):
        stripped_sym = f"{base[4:]}/{quote}:USDT"
        if stripped_sym in markets:
            return stripped_sym
        stripped_raw = f"{base[4:]}/{quote}"
        if stripped_raw in markets and markets[stripped_raw].get("swap", False):
            return stripped_raw

    # 4. Raw format: "BTC/USDT" or "1000PEPE/USDT"
    candidate2 = symbol.split(":")[0]
    if candidate2 in markets and markets[candidate2].get("swap", False):
        return candidate2
            
    return candidate1

def is_matching_symbol(sym1: str, sym2: str, ccxt_sym: Optional[str] = None) -> bool:
    """
    Check if two symbol representations refer to the same trading pair.
    Handles Binance / CCXT format discrepancies:
    e.g. "SOL/USDT", "SOL/USDT:USDT", "SOLUSDT" -> all match each other.
    """
    if not sym1 or not sym2:
        return False
    if sym1 == sym2 or (ccxt_sym and sym1 == ccxt_sym):
        return True
    norm = lambda s: s.split(":")[0].replace("/", "").replace("-", "").replace("_", "").upper()
    return norm(sym1) == norm(sym2)

def format_price_amount(exchange, symbol: str, price: float, amount: float) -> Tuple[float, float]:
    """Format price and amount according to exchange market precision."""
    try:
        ccxt_sym = get_exchange_market_symbol(exchange, symbol)
        if hasattr(exchange, 'price_to_precision') and hasattr(exchange, 'amount_to_precision') and hasattr(exchange, 'markets') and ccxt_sym in exchange.markets:
            clean_p = float(exchange.price_to_precision(ccxt_sym, price))
            clean_a = float(exchange.amount_to_precision(ccxt_sym, amount))
            return clean_p, clean_a
        
        market = exchange.market(ccxt_sym) if hasattr(exchange, 'market') and hasattr(exchange, 'markets') and ccxt_sym in exchange.markets else {}
        prec = market.get('precision', {})
        p_prec = prec.get('price')
        if isinstance(p_prec, int):
            formatted_price = round(price, p_prec)
        elif isinstance(p_prec, float) and p_prec > 0:
            formatted_price = round(round(price / p_prec) * p_prec, 8)
        else:
            p_p, _ = get_symbol_precisions(symbol, price)
            formatted_price = round(price, p_p)
            
        a_prec = prec.get('amount')
        if isinstance(a_prec, int):
            formatted_amount = round(amount, a_prec)
        elif isinstance(a_prec, float) and a_prec > 0:
            formatted_amount = round(round(amount / a_prec) * a_prec, 8)
        else:
            _, a_p = get_symbol_precisions(symbol, price)
            formatted_amount = round(amount, a_p)
            
        return formatted_price, formatted_amount
    except Exception:
        p_p, a_p = get_symbol_precisions(symbol, price)
        return round(price, p_p), round(amount, a_p)

def ensure_min_notional_amount(exchange, symbol: str, price: float, amount: float, min_notional: float = 5.20) -> float:
    """
    Ensure that the formatted amount produces an order notional (price * amount) >= min_notional.
    Binance Futures requires >= $5.00 USDT for all maker limit orders.
    If below min_notional, step up amount according to exchange market lot precision.
    """
    if price <= 0 or amount <= 0:
        return amount

    clean_price, clean_amount = format_price_amount(exchange, symbol, price, amount)
    min_cost = min_notional
    a_step = 1.0

    if exchange and hasattr(exchange, 'markets') and exchange.markets:
        ccxt_sym = get_exchange_market_symbol(exchange, symbol)
        mkt = exchange.market(ccxt_sym) if ccxt_sym in exchange.markets else {}
        ex_min_cost = (mkt.get('limits', {}) or {}).get('cost', {}).get('min')
        if ex_min_cost and float(ex_min_cost) > 0:
            min_cost = max(float(ex_min_cost) * 1.04, min_notional)

        prec = (mkt.get('precision', {}) or {}).get('amount')
        if isinstance(prec, (int, float)) and prec > 0:
            a_step = float(prec)
        elif isinstance(prec, int) and prec < 0:
            a_step = 10 ** abs(prec)
        else:
            _, a_p = get_symbol_precisions(symbol, clean_price)
            a_step = 10 ** (-a_p) if a_p > 0 else 1.0
    else:
        _, a_p = get_symbol_precisions(symbol, clean_price)
        a_step = 10 ** (-a_p) if a_p > 0 else 1.0

    if clean_price * clean_amount < min_cost:
        needed = min_cost / clean_price
        bumped = math.ceil(needed / a_step) * a_step
        _, clean_amount = format_price_amount(exchange, symbol, clean_price, bumped)
        if clean_price * clean_amount < min_cost:
            bumped += a_step
            _, clean_amount = format_price_amount(exchange, symbol, clean_price, bumped)

    return clean_amount

def parse_exchange_position(p: Dict[str, Any]) -> Tuple[float, str]:
    """
    Safely and accurately parse (contracts_amount, side) from a CCXT position dictionary.
    Handles Binance One-Way and Dual-Side modes, where CCXT 'contracts' is unsigned positive.
    Returns (amount, "LONG" | "SHORT" | "").
    """
    if not p:
        return 0.0, ""

    info = p.get("info") or {}
    signed_amt = 0.0
    for source in (info, p):
        for key in ("positionAmt", "size", "currentQty", "contracts"):
            val = source.get(key)
            if val is not None:
                try:
                    s_val = float(val)
                    if s_val != 0:
                        signed_amt = s_val
                        break
                except (ValueError, TypeError):
                    pass
        if signed_amt != 0:
            break

    raw_contracts = p.get("contracts")
    contracts_amt = 0.0
    if raw_contracts is not None:
        try:
            contracts_amt = abs(float(raw_contracts))
        except (ValueError, TypeError):
            pass

    amt = contracts_amt if contracts_amt > 0 else abs(signed_amt)
    if amt <= 0:
        return 0.0, ""

    # Accurate side detection
    side_field = str(p.get("side") or "").upper()
    pos_side_field = str(p.get("positionSide") or info.get("positionSide") or "").upper()

    if side_field in ("LONG", "SHORT"):
        pos_side = side_field
    elif pos_side_field in ("LONG", "SHORT"):
        pos_side = pos_side_field
    elif signed_amt < 0:
        pos_side = "SHORT"
    elif signed_amt > 0:
        pos_side = "LONG"
    else:
        pos_side = "LONG"

    return amt, pos_side


class GridEngine:
    """
    Full-featured multi-currency Grid Trading & Recovery Ladder engine.
    Supports paper simulation and live CCXT execution with Maker post-only orders.
    """
    def __init__(self, state_file: Optional[str] = None):
        self._lock = threading.RLock()
        self.state_file = state_file or os.path.expanduser("~/Library/Application Support/FeiyangAgent/grid_state.json")
        self.running = False
        self.config: Optional[Dict[str, Any]] = None
        self.grid: Optional[Dict[str, Any]] = None
        self.active_orders: Dict[str, Dict[str, Any]] = {}  # order_id -> order_info
        self.fills: List[Dict[str, Any]] = []
        self.events: List[str] = []
        self.stats = {
            "buys": 0,
            "sells": 0,
            "completed_rungs": 0,
            "grid_profit_usd": 0.0,
            "realized_position_pnl_usd": 0.0,
            "total_fees_usd": 0.0,
            "net_grid_profit_usd": 0.0,
            "volume_usd": 0.0,
            "started_at": None
        }
        self.last_price: Optional[float] = None
        self.out_of_range = False
        self.recovery = False
        self.risk: Dict[str, Any] = {}
        self.drawdown_stats: Dict[str, Any] = {
            "peak_equity_usd": 0.0,
            "current_drawdown_usd": 0.0,
            "current_drawdown_pct": 0.0,
            "max_drawdown_usd": 0.0,
            "max_drawdown_pct": 0.0,
            "unrealized_pnl_usd": 0.0,
            "net_profit_usd": 0.0,
            "est_liq_price": 0.0,
            "liq_distance_pct": 100.0,
            "circuit_breaker_tripped": False,
            "circuit_breaker_tripped_at": 0.0,
            "circuit_breaker_cooldown_sec": 900.0,
            "auto_resume_after_breaker": False
        }
        self.funding_rate_info: Dict[str, Any] = {
            "rate_8h_pct": 0.0,
            "est_apr_pct": 0.0,
            "favorable": True
        }
        self._last_reconcile_at = 0.0
        self._breakout_started_at = 0.0
        self._last_fr_check_at = 0.0
        self._last_live_pos_fetch_at = 0.0
        self._cached_live_pos: Optional[List[Dict[str, Any]]] = None
        self._last_mode_flip_ts = 0.0
        self._avg_entry_price = 0.0
        self._force_regime_check = False
        self.latest_regime_info: Optional[Dict[str, Any]] = None
        self._load_state()

    def _load_state(self):
        with self._lock:
            try:
                if os.path.exists(self.state_file):
                    with open(self.state_file, "r", encoding="utf-8") as f:
                        saved = json.load(f)
                        self.running = saved.get("running", False)
                        self.config = saved.get("config")
                        self.stats = saved.get("stats", self.stats)
                        if "realized_position_pnl_usd" not in self.stats:
                            self.stats["realized_position_pnl_usd"] = 0.0
                        self.drawdown_stats = saved.get("drawdown_stats", self.drawdown_stats)
                        self.recovery = saved.get("recovery", False)
                        self.out_of_range = saved.get("out_of_range", False)
                        self.last_price = saved.get("last_price")
                        self.active_orders = saved.get("active_orders", {})
                        self.fills = saved.get("fills", [])
                        self.events = saved.get("events", [])
                        self._avg_entry_price = float(saved.get("_avg_entry_price", 0.0) or 0.0)
                        if self.config:
                            try:
                                self.grid = build_grid(
                                    self.config["lower"],
                                    self.config["upper"],
                                    self.config["grid_count"],
                                    symbol=self.config.get("symbol", "BTC/USDT")
                                )
                                self._recompute_risk()
                            except Exception:
                                pass
            except Exception as e:
                logger.warning(f"[GridEngine] Failed to load grid state: {e}")

    def _save_state(self):
        with self._lock:
            try:
                os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
                tmp_file = f"{self.state_file}.tmp"
                state = {
                    "running": self.running,
                    "config": self.config,
                    "stats": self.stats,
                    "drawdown_stats": self.drawdown_stats,
                    "recovery": self.recovery,
                    "out_of_range": self.out_of_range,
                    "last_price": self.last_price,
                    "active_orders": self.active_orders,
                    "fills": self.fills[:50],
                    "events": self.events[:30],
                    "_avg_entry_price": getattr(self, "_avg_entry_price", 0.0),
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(state, f, indent=2, ensure_ascii=False)
                os.replace(tmp_file, self.state_file)
            except Exception as e:
                logger.error(f"[GridEngine] Failed to save grid state: {e}")

    def _recompute_risk(self):
        if not self.grid or not self.config:
            return
        mid = (self.config["lower"] + self.config["upper"]) / 2.0
        base_size = float(self.config.get("size_base", 0.001))
        lev = float(self.config.get("leverage", 20))
        sizing_mode = self.config.get("sizing_mode", "pyramid")
        
        # In pyramid mode, deep rungs have higher sizing multiplier (avg ~1.15x)
        avg_mult = 1.15 if sizing_mode == "pyramid" else 1.0
        notional = self.grid["count"] * (base_size * avg_mult) * mid
        required_margin = notional / lev if lev > 0 else notional
        per_rung_profit = self.grid["spacing"] * base_size
        spacing_pct = (self.grid["spacing"] / mid) * 100.0 if mid > 0 else 0.0
        
        self.risk = {
            "leverage": lev,
            "sizing_mode": sizing_mode,
            "notional_usd": round2(notional),
            "required_margin_usd": round2(required_margin),
            "per_rung_profit_usd": round_to(per_rung_profit, 4),
            "spacing_pct": round2(spacing_pct)
        }

    def _is_dual_position_mode(self, exchange) -> bool:
        """Cache dual-side position mode check to avoid redundant API weight hits."""
        if not exchange or getattr(exchange, "id", "").lower() != "binance":
            return False
        now = time.time()
        cached = getattr(self, "_cached_dual_mode", None)
        cached_at = getattr(self, "_cached_dual_mode_at", 0.0)
        if cached is not None and (now - cached_at) < 300.0:
            return cached
        try:
            if hasattr(exchange, "fapiPrivateGetPositionSideDual"):
                dual_res = exchange.fapiPrivateGetPositionSideDual()
                is_dual = bool(dual_res.get("dualSidePosition", False))
                self._cached_dual_mode = is_dual
                self._cached_dual_mode_at = now
                return is_dual
        except Exception:
            pass
        return False

    def _close_live_position_safe(self, exchange, symbol: str, position_side_filter: Optional[str] = None) -> int:
        """
        Safely and completely close any live positions for the symbol on the exchange.
        Handles both One-Way and Hedge (dual-side) modes on Binance and other exchanges,
        with strict LOT_SIZE/amount precision formatting.
        position_side_filter: None (close all), or "LONG" / "SHORT"
        Returns number of positions closed.
        """
        if not exchange:
            return 0
        ccxt_sym = get_exchange_market_symbol(exchange, symbol)
        closed_count = 0
        try:
            try:
                positions = exchange.fetch_positions([ccxt_sym])
            except Exception:
                positions = exchange.fetch_positions()
            for p in positions:
                p_sym = p.get("symbol", "")
                if p_sym and not is_matching_symbol(p_sym, symbol, ccxt_sym):
                    continue
                amt, pos_side = parse_exchange_position(p)
                if amt <= 0:
                    continue

                if position_side_filter and pos_side != position_side_filter.upper():
                    continue

                # To close a SHORT position, we must BUY. To close a LONG position, we must SELL.
                order_side = "buy" if pos_side == "SHORT" else "sell"
                target_pos_side = pos_side

                # Precision format the closing amount to avoid LOT_SIZE error
                _, clean_amt = format_price_amount(exchange, symbol, self.last_price or 1.0, amt)
                if clean_amt <= 0:
                    continue

                is_dual = self._is_dual_position_mode(exchange)
                if is_dual:
                    attempts = [
                        {"positionSide": target_pos_side},
                        {"positionSide": target_pos_side, "reduceOnly": True},
                        {"reduceOnly": True},
                        {}
                    ]
                else:
                    attempts = [
                        {"reduceOnly": True},
                        {"positionSide": target_pos_side},
                        {}
                    ]
                closed = False
                for params in attempts:
                    try:
                        exchange.create_order(
                            symbol=ccxt_sym,
                            type="market",
                            side=order_side,
                            amount=clean_amt,
                            params=params
                        )
                        entry_px = float(p.get("entryPrice") or getattr(self, "_avg_entry_price", 0.0) or self.last_price or 1.0)
                        exit_px = float(self.last_price or entry_px)
                        if pos_side == "LONG":
                            realized_pos_loss = (exit_px - entry_px) * clean_amt
                        else:
                            realized_pos_loss = (entry_px - exit_px) * clean_amt
                        taker_fee = round_to(exit_px * clean_amt * 0.0005, 4)
                        self.stats["realized_position_pnl_usd"] = round2(self.stats.get("realized_position_pnl_usd", 0.0) + realized_pos_loss)
                        self.stats["total_fees_usd"] = round_to(self.stats.get("total_fees_usd", 0.0) + taker_fee, 4)
                        grid_p = float(self.stats.get("grid_profit_usd", 0.0))
                        self.stats["net_grid_profit_usd"] = round2(grid_p + self.stats["realized_position_pnl_usd"] - self.stats["total_fees_usd"])
                        logger.info(f"[GridEngine] 🛑 成功市价平仓: {order_side.upper()} {clean_amt} {symbol} ({pos_side}仓位, 成本: ${entry_px}, 平仓价: ${exit_px}, 实现损益: ${round2(realized_pos_loss)} USD, 手续费: ${taker_fee} USD)")
                        closed = True
                        closed_count += 1
                        break
                    except Exception as e:
                        logger.debug(f"[GridEngine] 平仓尝试失败 (params={params}): {e}")

                if not closed:
                    logger.warning(f"[GridEngine] ⚠️ 无法完全市价平仓 {symbol} {pos_side} (数量: {clean_amt})")
        except Exception as e:
            logger.warning(f"[GridEngine] 查询或平掉实盘持仓异常: {e}")
        return closed_count

    def _place_live_order(self, exchange, symbol: str, side: str, price: float, amount: float, reduce_only: bool = False, mode: Optional[str] = None, pos_side: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
        """Submit a Post-Only Maker limit order to the live exchange with multi-mode compatibility."""
        clamped_amount = ensure_min_notional_amount(exchange, symbol, price, amount)
        clean_price, clean_amount = format_price_amount(exchange, symbol, price, clamped_amount)
        if clean_price <= 0 or clean_amount <= 0:
            return None, f"无效的下单价格或数量 (price={clean_price}, amount={clean_amount})"
        if clean_amount != amount:
            logger.info(f"[GridEngine] 🛡️ 实盘下单名义价值自动校准: {symbol} 下单量由 {amount} 自动提升至 {clean_amount} (名义价值 ${round(clean_price * clean_amount, 2)} USDT >= 交易所门槛)")
        last_error = None
        ccxt_sym = get_exchange_market_symbol(exchange, symbol)
        ex_id = getattr(exchange, "id", "").lower()
        active_mode = (mode or (self.config.get("mode") if self.config else "neutral") or "neutral").lower()

        attempts = []
        if ex_id == "binance":
            is_dual = self._is_dual_position_mode(exchange)
            if is_dual:
                if pos_side:
                    p_side = pos_side.upper()
                elif active_mode == "long":
                    p_side = "LONG"
                elif active_mode == "short":
                    p_side = "SHORT"
                else:
                    if side == "buy":
                        p_side = "SHORT" if reduce_only else "LONG"
                    else:
                        p_side = "LONG" if reduce_only else "SHORT"

                attempts.append({"timeInForce": "GTX", "positionSide": p_side})
                attempts.append({"positionSide": p_side})
            else:
                if reduce_only:
                    attempts.append({"timeInForce": "GTX", "reduceOnly": True})
                    attempts.append({"timeInForce": "GTX"})
                    attempts.append({"reduceOnly": True})
                else:
                    attempts.append({"timeInForce": "GTX"})
                    attempts.append({})
        elif ex_id == "okx":
            attempts.append({"postOnly": True})
            attempts.append({})
        elif ex_id == "bybit":
            attempts.append({"timeInForce": "PostOnly"})
            attempts.append({})
        else:
            attempts.append({"postOnly": True})
            attempts.append({})

        for p_try in attempts:
            try:
                ord = exchange.create_order(
                    symbol=ccxt_sym,
                    type="limit",
                    side=side,
                    amount=clean_amount,
                    price=clean_price,
                    params=p_try
                )
                if ord and ord.get("id"):
                    return str(ord["id"]), None
            except Exception as e:
                last_error = str(e)
                logger.debug(f"[GridEngine] Order attempt failed ({side} {clean_amount} @ {clean_price}, params={p_try}): {e}")

        return None, last_error

    def _open_initial_live_position(self, exchange, symbol: str, side: str, amount: float, mode: str) -> Tuple[bool, Optional[str]]:
        """
        Open the initial base position on the exchange to enable instant take-profit orders on the other side.
        """
        try:
            target_px = self.last_price or 1.0
            clean_amt = ensure_min_notional_amount(exchange, symbol, target_px, amount)
            clean_price, clean_amount = format_price_amount(exchange, symbol, target_px, clean_amt)
            if clean_amount <= 0:
                logger.warning(f"[GridEngine] 初始底仓数量过小 ({amount} -> {clean_amount})，跳过开立")
                return False, "初始底仓数量过小（精度截断后为 0）"
            ccxt_sym = get_exchange_market_symbol(exchange, symbol)
            ex_id = getattr(exchange, "id", "").lower()
            
            params = {}
            if ex_id == "binance":
                is_dual = self._is_dual_position_mode(exchange)
                if is_dual:
                    params["positionSide"] = "LONG" if side == "buy" else "SHORT"
            
            ord = exchange.create_order(
                symbol=ccxt_sym,
                type="market",
                side=side,
                amount=clean_amount,
                params=params
            )
            logger.info(f"[GridEngine] 🚀 实盘成功建立初始底仓: {side.upper()} {clean_amount} {symbol} (Order #{ord.get('id')})")
            return True, None
        except Exception as e:
            err = str(e)
            logger.warning(f"[GridEngine] ⚠️ 建立初始底仓提示: {err}")
            return False, err

    def _force_cancel_all_live_orders(self, exchange, symbol: str) -> int:
        """
        Safely and thoroughly cancel live open orders on the exchange for the given symbol.
        If Sniper has an active trade on this symbol, protects Sniper's stop-loss/take-profit algo orders
        by skipping bulk deletion and only cancelling Grid maker orders.
        """
        if not exchange:
            return 0
        ccxt_sym = get_exchange_market_symbol(exchange, symbol)
        raw_symbol = ccxt_sym.replace(":USDT", "").replace("/", "")
        total_cancelled = 0

        # Check if Sniper has active trades on this symbol
        sniper_active_on_symbol = False
        try:
            from app import sniper_engine
            if sniper_engine and hasattr(sniper_engine, "state"):
                trades = sniper_engine.state.get("trades", [])
                for t in trades:
                    if t.get("status") in ("open", "tp1_hit") and is_matching_symbol(t.get("symbol", ""), symbol):
                        sniper_active_on_symbol = True
                        break
        except Exception:
            pass

        if not sniper_active_on_symbol:
            # 1. Bulk cancel regular orders via Binance bulk endpoint
            try:
                if hasattr(exchange, "fapiPrivateDeleteAllOpenOrders"):
                    exchange.fapiPrivateDeleteAllOpenOrders({"symbol": raw_symbol})
                    total_cancelled += 1
                    logger.info(f"[GridEngine] 🧹 成功批量撤销交易所普通挂单: {ccxt_sym}")
            except Exception:
                pass

            # 2. Bulk cancel ALGO orders (STOP_MARKET, TAKE_PROFIT)
            try:
                if hasattr(exchange, "fapiPrivateDeleteAlgoOpenOrders"):
                    exchange.fapiPrivateDeleteAlgoOpenOrders({"symbol": raw_symbol})
                    total_cancelled += 1
                    logger.info(f"[GridEngine] 🧹 成功批量撤销交易所条件/算法挂单: {ccxt_sym}")
            except Exception:
                pass
        else:
            logger.info(f"[GridEngine] 🛡️ 监测到狙击引擎在 {symbol} 上存在活跃持仓，跳过全量算法单撤销，仅撤销网格做市单")

        # 3. Standard fallback / specific order cancel
        try:
            if not sniper_active_on_symbol and hasattr(exchange, "cancel_all_orders"):
                try:
                    exchange.cancel_all_orders(ccxt_sym)
                except Exception:
                    pass
            open_orders = exchange.fetch_open_orders(ccxt_sym)
            grid_live_ids = set()
            for o in self.active_orders.values():
                lid = o.get("live_order_id")
                if lid:
                    grid_live_ids.add(str(lid))

            for ord_item in open_orders:
                o_sym = ord_item.get("symbol")
                if o_sym and not is_matching_symbol(o_sym, symbol, ccxt_sym):
                    continue
                oid = str(ord_item.get("id", ""))
                client_oid = str(ord_item.get("clientOrderId", ""))
                if sniper_active_on_symbol:
                    is_grid_order = (oid in grid_live_ids) or ("grid" in client_oid.lower())
                    if not is_grid_order:
                        continue
                if oid:
                    try:
                        exchange.cancel_order(oid, ccxt_sym)
                        total_cancelled += 1
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"[GridEngine] Standard cancel open orders notice: {e}")

        return total_cancelled

    def start_grid(self, cfg: Dict[str, Any], current_price: float, exchange=None) -> Dict[str, Any]:
        """
        Start an arithmetic grid with Pyramid or Uniform sizing and instant Initial Position auto-entry.
        cfg keys: symbol, lower, upper, grid_count, size_base, leverage, mode ('neutral'|'long'|'short'), sizing_mode ('pyramid'|'uniform'), out_of_range_action ('auto_shift'|'recover'|'close'), is_live, auto_open_initial_position (bool)
        """
        with self._lock:
            if self.running:
                raise ValueError("网格机器人已在运行中，请先停止再重新启动。")
            
            symbol = cfg.get("symbol", "BTC/USDT")
            lower = float(cfg["lower"])
            upper = float(cfg["upper"])
            grid_count = int(cfg.get("grid_count", 10))
            size_base = float(cfg.get("size_base", 0.001))
            leverage = int(cfg.get("leverage", 20))
            mode = cfg.get("mode", "neutral").lower()
            sizing_mode = cfg.get("sizing_mode", "pyramid").lower()
            out_of_range_action = cfg.get("out_of_range_action", "auto_shift").lower()
            is_live = bool(cfg.get("is_live", False))
            auto_init_pos = bool(cfg.get("auto_open_initial_position", not is_live))
            self._cached_live_pos = None
            self._last_live_pos_fetch_at = 0.0

            if current_price <= 0:
                raise ValueError("当前价格无效，无法启动网格。")
            if current_price < lower * 0.7 or current_price > upper * 1.3:
                raise ValueError(f"当前市价 ${current_price} 与网格区间 [{lower}, {upper}] 偏离过大，请调整区间。")

            if is_live and not exchange:
                raise ValueError("实盘模式必须提供已绑定的交易所客户端！")

            # In live mode, set leverage on exchange and wipe any stale open orders
            if is_live and exchange:
                try:
                    ccxt_sym = get_exchange_market_symbol(exchange, symbol)
                    exchange.set_leverage(leverage, ccxt_sym)
                except Exception as e:
                    logger.debug(f"[GridEngine] Set leverage notice: {e}")
                
                # Wipe all stale open orders on exchange before seeding new grid
                self._force_cancel_all_live_orders(exchange, symbol)

            grid = build_grid(lower, upper, grid_count, symbol=symbol)
            max_dd_limit = float(cfg.get("max_drawdown_limit_pct", 15.0) or 15.0)
            auto_compound = bool(cfg.get("auto_compound", True))
            self.config = {
                "symbol": symbol,
                "lower": grid["lower"],
                "upper": grid["upper"],
                "grid_count": grid_count,
                "size_base": size_base,
                "base_size_orig": size_base,
                "leverage": leverage,
                "mode": mode,
                "sizing_mode": sizing_mode,
                "out_of_range_action": out_of_range_action,
                "is_live": is_live,
                "auto_open_initial_position": auto_init_pos,
                "has_initial_position": False,
                "entry_price": current_price,
                "max_drawdown_limit_pct": max_dd_limit,
                "auto_compound": auto_compound
            }
            if cfg.get("target_rung_spacing_pct") is not None:
                self.config["target_rung_spacing_pct"] = float(cfg["target_rung_spacing_pct"])
            self.grid = grid
            self.last_price = current_price
            self._avg_entry_price = current_price
            self.out_of_range = (current_price < lower or current_price > upper)
            self.recovery = False
            self._force_regime_check = False
            self._last_mode_flip_ts = 0.0
            self._breakout_started_at = 0.0
            self.active_orders.clear()
            self.fills.clear()
            self.stats = {
                "buys": 0,
                "sells": 0,
                "completed_rungs": 0,
                "grid_profit_usd": 0.0,
                "realized_position_pnl_usd": 0.0,
                "total_fees_usd": 0.0,
                "net_grid_profit_usd": 0.0,
                "volume_usd": 0.0,
                "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            self._recompute_risk()
            explicit_init_cap = cfg.get("initial_capital_usd")
            if explicit_init_cap is not None and float(explicit_init_cap) > 0:
                self.config["initial_capital_usd"] = round2(float(explicit_init_cap))
                self.config["initial_capital_explicit"] = True
            else:
                self.config["initial_capital_explicit"] = False
                live_init_cap = 0.0
                if is_live and exchange:
                    try:
                        if hasattr(exchange, "fapiPrivateV2GetAccount"):
                            acct = exchange.fapiPrivateV2GetAccount()
                            if isinstance(acct, dict):
                                live_init_cap = float(acct.get("totalWalletBalance", 0.0) or 0.0)
                    except Exception:
                        pass
                    if live_init_cap <= 0:
                        try:
                            bal = exchange.fetch_balance({"type": "swap"})
                            if isinstance(bal, dict):
                                usdt_info = bal.get("USDT", {})
                                if isinstance(usdt_info, dict):
                                    live_init_cap = float(usdt_info.get("total") or usdt_info.get("free") or 0.0)
                        except Exception:
                            pass
                if live_init_cap > 0:
                    self.config["initial_capital_usd"] = round2(live_init_cap)
                else:
                    self.config["initial_capital_usd"] = float((self.risk or {}).get("required_margin_usd", 10.0))

            initial_equity = max(1.0, float(self.config["initial_capital_usd"]))
            self.drawdown_stats = {
                "peak_equity_usd": initial_equity,
                "current_drawdown_usd": 0.0,
                "current_drawdown_pct": 0.0,
                "max_drawdown_usd": 0.0,
                "max_drawdown_pct": 0.0,
                "unrealized_pnl_usd": 0.0,
                "net_profit_usd": 0.0,
                "est_liq_price": 0.0,
                "liq_distance_pct": 100.0,
                "circuit_breaker_tripped": False,
                "circuit_breaker_tripped_at": 0.0,
                "circuit_breaker_cooldown_sec": float(cfg.get("circuit_breaker_cooldown_sec", 900.0) or 900.0),
                "auto_resume_after_breaker": bool(cfg.get("auto_resume_after_breaker", False))
            }

            # Seed initial ladder orders with pyramid / uniform sizing
            seeds = seed_orders(grid["levels"], current_price, mode, grid["spacing"], base_size=size_base, sizing_mode=sizing_mode)
            
            # Auto-open Initial Base Position to enable immediate bi-directional profit taking
            opened_initial_here = False
            if auto_init_pos:
                if mode == "long":
                    sell_seeds = [s for s in seeds if s["side"] == "sell"]
                    init_size = sum(s["size_base"] for s in sell_seeds)
                    if init_size > 0:
                        existing_pos = 0.0
                        if is_live and exchange:
                            try:
                                ccxt_sym = get_exchange_market_symbol(exchange, symbol)
                                pos_list = exchange.fetch_positions([ccxt_sym])
                                for p in pos_list:
                                    p_sym = p.get("symbol", "")
                                    if p_sym and not is_matching_symbol(p_sym, symbol, ccxt_sym):
                                        continue
                                    amt, p_side = parse_exchange_position(p)
                                    if p_side == "LONG":
                                        existing_pos += amt
                            except Exception:
                                pass

                        needed_to_open = max(0.0, init_size - existing_pos)
                        _, clean_needed = format_price_amount(exchange, symbol, current_price, needed_to_open)
                        if clean_needed > 0:
                            if is_live and exchange:
                                success, err = self._open_initial_live_position(exchange, symbol, "buy", clean_needed, mode)
                                if not success:
                                    raise ValueError(f"交易所建立初始底仓失败: {err}。请点击【✨ AI 智能配置全部参数】重新匹配资金！")
                                opened_initial_here = True
                        self.config["has_initial_position"] = True
                        self.config["initial_position_size"] = max(existing_pos, init_size)
                        self.config["initial_position_side"] = "long"
                        if existing_pos >= init_size:
                            event_init = f"[{datetime.now().strftime('%H:%M:%S')}] 🚀 检测到已有持仓 {round(existing_pos, 4)} {symbol}，直接复用作为底仓，上方立即铺设平多止盈卖单！"
                        elif existing_pos > 0:
                            event_init = f"[{datetime.now().strftime('%H:%M:%S')}] 🚀 检测到已有持仓 {round(existing_pos, 4)} {symbol}，补足开立 {clean_needed}，上方立即铺设平多止盈卖单！"
                        else:
                            event_init = f"[{datetime.now().strftime('%H:%M:%S')}] 🚀 已自动开立多头底仓 {clean_needed} {symbol}，上方立即铺设平多止盈卖单！"
                        self.events.insert(0, event_init)
                        logger.info(f"[GridEngine] {event_init}")
                elif mode == "short":
                    buy_seeds = [s for s in seeds if s["side"] == "buy"]
                    init_size = sum(s["size_base"] for s in buy_seeds)
                    if init_size > 0:
                        existing_pos = 0.0
                        if is_live and exchange:
                            try:
                                ccxt_sym = get_exchange_market_symbol(exchange, symbol)
                                pos_list = exchange.fetch_positions([ccxt_sym])
                                for p in pos_list:
                                    p_sym = p.get("symbol", "")
                                    if p_sym and not is_matching_symbol(p_sym, symbol, ccxt_sym):
                                        continue
                                    amt, p_side = parse_exchange_position(p)
                                    if p_side == "SHORT":
                                        existing_pos += amt
                            except Exception:
                                pass

                        needed_to_open = max(0.0, init_size - existing_pos)
                        _, clean_needed = format_price_amount(exchange, symbol, current_price, needed_to_open)
                        if clean_needed > 0:
                            if is_live and exchange:
                                success, err = self._open_initial_live_position(exchange, symbol, "sell", clean_needed, mode)
                                if not success:
                                    raise ValueError(f"交易所建立初始底仓失败: {err}。请点击【✨ AI 智能配置全部参数】重新匹配资金！")
                                opened_initial_here = True
                        self.config["has_initial_position"] = True
                        self.config["initial_position_size"] = max(existing_pos, init_size)
                        self.config["initial_position_side"] = "short"
                        if existing_pos >= init_size:
                            event_init = f"[{datetime.now().strftime('%H:%M:%S')}] 🚀 检测到已有空头持仓 {round(existing_pos, 4)} {symbol}，直接复用作为底仓，下方立即铺设平空止盈买单！"
                        elif existing_pos > 0:
                            event_init = f"[{datetime.now().strftime('%H:%M:%S')}] 🚀 检测到已有空头持仓 {round(existing_pos, 4)} {symbol}，补足开立 {clean_needed}，下方立即铺设平空止盈买单！"
                        else:
                            event_init = f"[{datetime.now().strftime('%H:%M:%S')}] 🚀 已自动开立空头底仓 {clean_needed} {symbol}，下方立即铺设平空止盈买单！"
                        self.events.insert(0, event_init)
                        logger.info(f"[GridEngine] {event_init}")
            else:
                # 100% Pure Maker Grid: No naked market entries on startup!
                # Checks if user already has an existing position on exchange to adopt
                existing_pos = 0.0
                if is_live and exchange:
                    try:
                        ccxt_sym = get_exchange_market_symbol(exchange, symbol)
                        pos_list = exchange.fetch_positions([ccxt_sym])
                        for p in pos_list:
                            p_sym = p.get("symbol", "")
                            if p_sym and not is_matching_symbol(p_sym, symbol, ccxt_sym):
                                continue
                            amt, p_side = parse_exchange_position(p)
                            if (mode == "long" and p_side == "LONG") or (mode == "short" and p_side == "SHORT"):
                                existing_pos += amt
                    except Exception:
                        pass

                if existing_pos > 0:
                    self.config["has_initial_position"] = True
                    self.config["initial_position_size"] = existing_pos
                    self.config["initial_position_side"] = mode
                    event_init = f"[{datetime.now().strftime('%H:%M:%S')}] 🚀 检测到实盘已有底仓 {round(existing_pos, 4)} {symbol}，直接复用并铺设 Maker 止盈网格！"
                else:
                    self.config["has_initial_position"] = False
                    if mode == "long":
                        seeds = [s for s in seeds if s["side"] == "buy"]
                        event_init = f"[{datetime.now().strftime('%H:%M:%S')}] 🛡️ 纯 Maker 逢低做多网格已启动：低位挂单待回踩建仓，拒绝追高市价开仓！"
                    elif mode == "short":
                        seeds = [s for s in seeds if s["side"] == "sell"]
                        event_init = f"[{datetime.now().strftime('%H:%M:%S')}] 🛡️ 纯 Maker 逢高做空网格已启动：高位挂单待反弹建仓，拒绝杀跌市价开空！"
                    else:
                        event_init = f"[{datetime.now().strftime('%H:%M:%S')}] 🛡️ 纯 Maker 中性网格启动：上下双向限价铺单！"
                self.events.insert(0, event_init)
                logger.info(f"[GridEngine] {event_init}")

            live_errors = []
            live_success = 0

            for seed in seeds:
                order_id = f"grid-{int(time.time()*1000)}-{seed['level_index']}"
                order_size = seed["size_base"]
                mult = seed.get("size_multiplier", 1.0)
                p_side = seed.get("pos_side")
                
                # If live, place limit Maker order on exchange
                if is_live and exchange:
                    order_size = ensure_min_notional_amount(exchange, symbol, seed["price"], order_size)
                    live_oid, err = self._place_live_order(
                        exchange=exchange,
                        symbol=symbol,
                        side=seed["side"],
                        price=seed["price"],
                        amount=order_size,
                        reduce_only=seed["reduce_only"],
                        mode=mode,
                        pos_side=p_side
                    )
                    if live_oid:
                        live_success += 1
                        self.active_orders[order_id] = {
                            "order_id": order_id,
                            "live_order_id": live_oid,
                            "level_index": seed["level_index"],
                            "price": seed["price"],
                            "side": seed["side"],
                            "pos_side": p_side,
                            "size_base": order_size,
                            "size_multiplier": mult,
                            "reduce_only": seed["reduce_only"],
                            "placed_at": time.time(),
                            "status": "live_open"
                        }
                    else:
                        if err:
                            live_errors.append(err)
                else:
                    # Paper mode
                    self.active_orders[order_id] = {
                        "order_id": order_id,
                        "level_index": seed["level_index"],
                        "price": seed["price"],
                        "side": seed["side"],
                        "pos_side": p_side,
                        "size_base": order_size,
                        "size_multiplier": mult,
                        "reduce_only": seed["reduce_only"],
                        "placed_at": time.time(),
                        "status": "paper_open"
                    }

            if is_live and exchange and live_success == 0:
                if opened_initial_here:
                    try:
                        self._close_live_position_safe(exchange, symbol)
                        logger.warning(f"[GridEngine] ⚠️ 挂单全部失败，已自动市价平仓刚刚开立的底仓，彻底杜绝孤儿持仓风险！")
                    except Exception as rb_err:
                        logger.error(f"[GridEngine] 初始底仓回滚失败: {rb_err}")
                err_msg = live_errors[0] if live_errors else "未知错误"
                if "MIN_NOTIONAL" in err_msg or "Margin is insufficient" in err_msg or "notional" in err_msg.lower() or "-4140" in err_msg or "larger than" in err_msg:
                    raise ValueError(f"交易所拒绝下单：{err_msg}。请点击【✨ 智能推荐区间】自动适配该币种合约门槛！")
                elif "Precision" in err_msg or "LOT_SIZE" in err_msg or "-1111" in err_msg:
                    raise ValueError(f"交易所拒绝下单：价格或数量精度不符（{err_msg[:60]}）。请点击【✨ 智能推荐区间】自动校准。")
                elif "418" in err_msg or "-1003" in err_msg:
                    raise ValueError(f"交易所暂时限流保护中，请等待 1~2 分钟后再试。")
                else:
                    raise ValueError(f"实盘向交易所挂单全部失败！原因：{err_msg}")

            self.running = True
            self._save_state()
            mode_label = f"🔴 实盘 (成功挂单 {live_success} 笔)" if is_live else f"🟢 模拟盘 (挂单 {len(seeds)} 笔)"
            logger.info(f"[GridEngine] 🚀 成功启动网格: {mode_label} {symbol} [{lower}, {upper}], {grid_count}格 (双向铺单+底仓就绪)")
            return self.get_state()

    def stop_grid(self, close_position: bool = False, exchange=None, from_circuit_breaker: bool = False) -> Dict[str, Any]:
        """
        Stop the grid, cancel open grid orders, and optionally close existing position.
        """
        with self._lock:
            if not self.running:
                return {"status": "not_running", "message": "网格未在运行"}

            if not from_circuit_breaker:
                # If explicitly stopped by user/API, clear breaker tripped flags so it doesn't auto-resume unexpectedly
                self.drawdown_stats["circuit_breaker_tripped"] = False
                self.drawdown_stats["circuit_breaker_tripped_at"] = 0.0

            # Cancel all live orders on exchange thoroughly
            if self.config and self.config.get("is_live"):
                if not exchange:
                    try:
                        from app import sniper_engine
                        exchange, _ = sniper_engine._init_live_ccxt()
                    except Exception:
                        pass
                if exchange:
                    symbol = self.config.get("symbol", "BTC/USDT")
                    self._force_cancel_all_live_orders(exchange, symbol)
                    
                    if close_position:
                        self._close_live_position_safe(exchange, symbol)

            if close_position:
                if self.config:
                    if not self.config.get("is_live") and self.config.get("has_initial_position"):
                        init_size = float(self.config.get("initial_position_size", 0.0))
                        entry_px = float(self.config.get("entry_price") or getattr(self, "_avg_entry_price", 0.0) or (self.last_price or 1.0))
                        old_mode = (self.config.get("mode") or "").lower()
                        curr_px = float(self.last_price or entry_px)
                        if init_size > 0:
                            pnl = (curr_px - entry_px) * init_size if old_mode == "long" else (entry_px - curr_px) * init_size
                            fee = round_to(curr_px * init_size * 0.0005, 4)
                            self.stats["realized_position_pnl_usd"] = round2(self.stats.get("realized_position_pnl_usd", 0.0) + pnl)
                            self.stats["total_fees_usd"] = round_to(self.stats.get("total_fees_usd", 0.0) + fee, 4)
                            grid_p = float(self.stats.get("grid_profit_usd", 0.0))
                            self.stats["net_grid_profit_usd"] = round2(grid_p + self.stats["realized_position_pnl_usd"] - self.stats["total_fees_usd"])
                    self.config["initial_position_size"] = 0.0
                    self.config["has_initial_position"] = False
                self.drawdown_stats["unrealized_pnl_usd"] = 0.0
                self._avg_entry_price = 0.0

            self.running = False
            self.active_orders.clear()
            self.recovery = False
            self._breakout_started_at = 0.0
            self._cached_live_pos = None
            self._last_live_pos_fetch_at = 0.0
            self._save_state()
            logger.info("[GridEngine] 🛑 网格已安全停止并撤销全部挂单。")
            return self.get_state()

    def on_price_tick(self, symbol: str, current_price: float, high: float = 0.0, low: float = 0.0, exchange=None):
        """
        Evaluate price against active grid levels, process fills, generate replacement orders,
        and trigger recovery ladder if price breaks out of the grid boundaries.
        """
        with self._lock:
            if not self.running or not self.config or not self.grid:
                return
            if self.config.get("symbol") != symbol:
                return

            self.last_price = current_price
            high_px = high if high > 0 else current_price
            low_px = low if low > 0 else current_price

            # --- Dynamic Unrealized PnL, Net PnL, High-Water Mark & Max Drawdown Calculation ---
            try:
                base_size = float(self.config.get("size_base", 0.001))
                leverage = max(1.0, float(self.config.get("leverage", 20)))
                mode = (self.config.get("mode") or "neutral").lower()
                entry_px = float(self.config.get("entry_price") or self.last_price or current_price)
                pos_size = 0.0
                unrealized_pnl = 0.0
                est_liq_price = 0.0
                liq_dist_pct = 100.0
                grid_profit = float(self.stats.get("grid_profit_usd", 0.0))
                init_margin = max(1.0, float(self.config.get("initial_capital_usd") or (self.risk or {}).get("required_margin_usd", 10.0)))
                available_equity = max(1.0, init_margin + grid_profit)
                mmr = 0.01  # Standard maintenance margin rate (1.0% on Binance USDT-M)

                live_synced = False
                live_wallet_bal = 0.0
                live_avail_bal = 0.0
                now_pos_ts = time.time()
                if self.config.get("is_live") and exchange:
                    if (now_pos_ts - getattr(self, "_last_live_pos_fetch_at", 0.0) >= 5.0) or getattr(self, "_cached_live_pos", None) is None:
                        try:
                            ccxt_sym = get_exchange_market_symbol(exchange, symbol)
                            self._cached_live_pos = exchange.fetch_positions([ccxt_sym])
                            try:
                                if hasattr(exchange, 'fapiPrivateV2GetAccount'):
                                    acc = exchange.fapiPrivateV2GetAccount()
                                    if isinstance(acc, dict):
                                        self._cached_live_bal = {
                                            "totalWalletBalance": float(acc.get("totalWalletBalance", 0.0) or 0.0),
                                            "availableBalance": float(acc.get("availableBalance", 0.0) or 0.0),
                                            "totalUnrealizedProfit": float(acc.get("totalUnrealizedProfit", 0.0) or 0.0)
                                        }
                                        if not self._cached_live_pos and acc.get("positions"):
                                            self._cached_live_pos = acc.get("positions")
                                    else:
                                        self._cached_live_bal = None
                                else:
                                    b = exchange.fetch_balance()
                                    u = b.get("USDT", {}) if isinstance(b, dict) else {}
                                    self._cached_live_bal = {
                                        "totalWalletBalance": float(u.get("total", 0.0) or u.get("free", 0.0) or 0.0),
                                        "availableBalance": float(u.get("free", 0.0) or 0.0),
                                        "totalUnrealizedProfit": 0.0
                                    }
                            except Exception:
                                self._cached_live_bal = None
                            self._last_live_pos_fetch_at = now_pos_ts
                        except Exception:
                            pass
                    
                    cached_bal = getattr(self, "_cached_live_bal", None)
                    if cached_bal:
                        live_wallet_bal = float(cached_bal.get("totalWalletBalance", 0.0) or 0.0)
                        live_avail_bal = float(cached_bal.get("availableBalance", 0.0) or 0.0)

                    live_positions = getattr(self, "_cached_live_pos", None)
                    if live_positions:
                        try:
                            ccxt_sym = get_exchange_market_symbol(exchange, symbol)
                            total_live_amt = 0.0
                            total_live_pnl = 0.0
                            for lp in live_positions:
                                lp_sym = lp.get("symbol", "")
                                if lp_sym and not is_matching_symbol(lp_sym, symbol, ccxt_sym):
                                    continue
                                lp_amt = abs(float(lp.get("contracts", 0.0) or lp.get("positionAmt", 0.0)))
                                if lp_amt > 0:
                                    total_live_amt += lp_amt
                                    lp_entry = float(lp.get("entryPrice", 0.0) or 0.0)
                                    if lp_entry > 0:
                                        entry_px = lp_entry
                                    lp_pnl = float(lp.get("unrealizedPnl", 0.0) or lp.get("unrealizedProfit", 0.0) or 0.0)
                                    total_live_pnl += lp_pnl
                                    lp_liq = float(lp.get("liquidationPrice", 0.0) or 0.0)
                                    if lp_liq > 0:
                                        lp_dist = max(0.0, abs(current_price - lp_liq) / current_price * 100.0) if current_price > 0 else 100.0
                                        if lp_dist < liq_dist_pct:
                                            est_liq_price = lp_liq
                                            liq_dist_pct = lp_dist
                            if total_live_amt > 0:
                                pos_size = total_live_amt
                                unrealized_pnl = total_live_pnl
                                live_synced = True
                            elif live_wallet_bal > 0:
                                live_synced = True
                        except Exception:
                            pass

                if not live_synced:
                    if getattr(self, "_avg_entry_price", 0.0) > 0:
                        entry_px = float(self._avg_entry_price)

                    if mode == "long":
                        init_size = float(self.config.get("initial_position_size", 0.0))
                        pos_size = max(0.0, init_size + (self.stats["buys"] - self.stats["sells"]) * base_size)
                        if pos_size > 0:
                            unrealized_pnl = (current_price - entry_px) * pos_size
                            notional_entry = entry_px * pos_size
                            if available_equity >= notional_entry:
                                est_liq_price = 0.0
                            else:
                                est_liq_price = max(0.0, (notional_entry - available_equity) / (pos_size * (1.0 - mmr)))
                            liq_dist_pct = max(0.0, (current_price - est_liq_price) / current_price * 100.0) if current_price > 0 else 100.0
                    elif mode == "short":
                        init_size = float(self.config.get("initial_position_size", 0.0))
                        pos_size = max(0.0, init_size + (self.stats["sells"] - self.stats["buys"]) * base_size)
                        if pos_size > 0:
                            unrealized_pnl = (entry_px - current_price) * pos_size
                            notional_entry = entry_px * pos_size
                            est_liq_price = (notional_entry + available_equity) / (pos_size * (1.0 + mmr))
                            liq_dist_pct = max(0.0, (est_liq_price - current_price) / current_price * 100.0) if current_price > 0 else 100.0
                    else:  # Neutral
                        net_delta = (self.stats["buys"] - self.stats["sells"]) * base_size
                        pos_size = abs(net_delta)
                        if net_delta > 0:
                            unrealized_pnl = (current_price - entry_px) * net_delta
                            notional_entry = entry_px * net_delta
                            if available_equity >= notional_entry:
                                est_liq_price = 0.0
                            else:
                                est_liq_price = max(0.0, (notional_entry - available_equity) / (net_delta * (1.0 - mmr)))
                            liq_dist_pct = max(0.0, (current_price - est_liq_price) / current_price * 100.0) if current_price > 0 else 100.0
                        elif net_delta < 0:
                            unrealized_pnl = (entry_px - current_price) * pos_size
                            notional_entry = entry_px * pos_size
                            est_liq_price = (notional_entry + available_equity) / (pos_size * (1.0 + mmr))
                            liq_dist_pct = max(0.0, (est_liq_price - current_price) / current_price * 100.0) if current_price > 0 else 100.0

                init_cap = float(self.config.get("initial_capital_usd", 0.0) or (self.risk or {}).get("required_margin_usd", 10.0))
                realized_pos_pnl = float(self.stats.get("realized_position_pnl_usd", 0.0))
                total_fees = float(self.stats.get("total_fees_usd", 0.0))

                if (
                    self.config.get("is_live")
                    and live_wallet_bal > 0
                    and not self.config.get("initial_capital_explicit")
                    and self.stats.get("buys", 0) == 0
                    and self.stats.get("sells", 0) == 0
                    and abs(grid_profit) < 1e-6
                    and abs(realized_pos_pnl) < 1e-6
                    and abs(unrealized_pnl) < 1e-6
                ):
                    init_cap = round2(live_wallet_bal)
                    self.config["initial_capital_usd"] = init_cap
                    self.config["initial_capital_explicit"] = True
                    self.drawdown_stats["peak_equity_usd"] = init_cap

                if self.config.get("is_live") and live_wallet_bal > 0 and init_cap > 0:
                    current_equity = round2(live_wallet_bal + unrealized_pnl)
                    net_profit = round2(current_equity - init_cap)
                    base_eq = init_cap
                else:
                    net_profit = round2(grid_profit + realized_pos_pnl - total_fees + unrealized_pnl)
                    current_equity = round2(init_margin + net_profit)
                    base_eq = init_margin

                peak_equity = max(float(self.drawdown_stats.get("peak_equity_usd", base_eq)), current_equity, base_eq)
                current_dd_usd = max(0.0, round2(peak_equity - current_equity))
                current_dd_pct = round2((current_dd_usd / peak_equity) * 100.0) if peak_equity > 0 else 0.0

                max_dd_usd = max(float(self.drawdown_stats.get("max_drawdown_usd", 0.0)), current_dd_usd)
                max_dd_pct = max(float(self.drawdown_stats.get("max_drawdown_pct", 0.0)), current_dd_pct)

                self.drawdown_stats.update({
                    "peak_equity_usd": round2(peak_equity),
                    "current_drawdown_usd": current_dd_usd,
                    "current_drawdown_pct": current_dd_pct,
                    "max_drawdown_usd": round2(max_dd_usd),
                    "max_drawdown_pct": current_dd_pct if current_dd_pct > max_dd_pct else max_dd_pct,
                    "unrealized_pnl_usd": round2(unrealized_pnl),
                    "net_profit_usd": net_profit,
                    "grid_profit_usd": float(self.stats.get("grid_profit_usd", 0.0)),
                    "realized_position_pnl_usd": round2(realized_pos_pnl),
                    "total_fees_usd": round_to(total_fees, 4),
                    "live_wallet_balance_usd": round2(live_wallet_bal) if live_wallet_bal > 0 else None,
                    "live_available_balance_usd": round2(live_avail_bal) if live_avail_bal > 0 else None,
                    "live_equity_usd": round2(current_equity) if live_wallet_bal > 0 else None,
                    "est_liq_price": round_to(est_liq_price, 4),
                    "liq_distance_pct": round2(liq_dist_pct)
                })

                # --- Institutional Emergency Circuit Breaker & Breakout Protection ---
                max_limit = float(self.config.get("max_drawdown_limit_pct", 15.0) or 15.0)
                out_of_range_cb = current_price < float(self.config["lower"]) or current_price > float(self.config["upper"])

                # Dynamic Liquidation Buffer Danger Threshold:
                # With high leverage (e.g. 20x~25x), the mathematical liquidation distance at entry is ~3.1%~4.0%.
                # A hardcoded static threshold like <= 3.5% will FALSELY trip immediately at startup (t=0) on entry!
                # Real exchange danger occurs when liquidation distance drops to <= 1.0%~1.2% (close to margin call).
                # Dynamically calculate threshold based on leverage clearance:
                initial_liq_buffer = max(1.0, (100.0 / leverage) - 1.0)
                liq_danger_threshold = min(2.0, max(0.8, initial_liq_buffer * 0.35))
                is_imminent_liq = (liq_dist_pct <= liq_danger_threshold and pos_size > 0)

                # Core Quantitative Rule:
                # Inside the grid [lower, upper], floating drawdown is expected and normal as the grid accumulates dips.
                # A hard market-dump circuit breaker MUST ONLY trigger if:
                # 1) Imminent liquidation threat (liq_dist_pct <= liq_danger_threshold with open position), OR
                # 2) Range is breached (price is out of range) AND floating drawdown >= max_limit.
                should_trip_cb = (is_imminent_liq or (out_of_range_cb and current_dd_pct >= max_limit))

                if should_trip_cb and not self.drawdown_stats.get("circuit_breaker_tripped"):
                    now_ts_cb = time.time()
                    cooldown_sec = float(self.config.get("circuit_breaker_cooldown_sec", 900.0) or 900.0)
                    self.drawdown_stats["circuit_breaker_tripped"] = True
                    self.drawdown_stats["circuit_breaker_tripped_at"] = now_ts_cb
                    self.drawdown_stats["circuit_breaker_cooldown_sec"] = cooldown_sec
                    cooldown_min = int(cooldown_sec / 60)
                    if is_imminent_liq:
                        trip_msg = f"[{datetime.now().strftime('%H:%M:%S')}] 🚨 触发爆仓防范极速硬熔断风控（预估强平距离 {round(liq_dist_pct, 2)}% <= 危险警戒线 {round(liq_danger_threshold, 2)}%）！已紧急市价全平持仓规避穿仓！系统进入 {cooldown_min} 分钟冷静保护期..."
                    else:
                        trip_msg = f"[{datetime.now().strftime('%H:%M:%S')}] 🚨 触发破位越界硬熔断风控（价格已冲破做市区间且回撤 {current_dd_pct}% >= 警戒阈值 {max_limit}%）！已紧急市价全平持仓！系统进入 {cooldown_min} 分钟冷静保护期，将在冷静期结束后由 AI 多周期重新研判并自动重启做市！"
                    self.events.insert(0, trip_msg)
                    logger.critical(f"[GridEngine] {trip_msg}")
                    self.stop_grid(close_position=True, exchange=exchange, from_circuit_breaker=True)
                    return
            except Exception as dd_err:
                logger.debug(f"[GridEngine] Drawdown calc notice: {dd_err}")

            # --- Anti-Whipsaw Filtered Out-of-Range & Breakout Check ---
            out = current_price < self.config["lower"] or current_price > self.config["upper"]
            now_ts = time.time()
            spacing = float((self.grid or {}).get("spacing", 0.0) or 0.0)
            lower_b = float(self.config["lower"])
            upper_b = float(self.config["upper"])

            # ── Intra-Grid Real-Time Adverse Excursion Trigger ──
            # If market direction turned around while still inside the range,
            # detect whether the active position is taking adverse drawdown:
            if pos_size > 0 and entry_px > 0:
                adverse_pct = 0.0
                if mode == "long" and current_price < entry_px:
                    adverse_pct = (entry_px - current_price) / entry_px * 100.0
                elif mode == "short" and current_price > entry_px:
                    adverse_pct = (current_price - entry_px) / entry_px * 100.0
                
                # If adverse move exceeds 1.8%, immediately signal fast background loop to re-evaluate MTF
                if adverse_pct >= 1.8:
                    self._force_regime_check = True

            if out:
                if self._breakout_started_at == 0.0:
                    self._breakout_started_at = now_ts

                # Check breakout magnitude vs grid spacing
                dist_outside = (lower_b - current_price) if current_price < lower_b else (current_price - upper_b)
                is_deep_breakout = spacing > 0 and dist_outside >= (0.8 * spacing)
                elapsed_breakout = now_ts - self._breakout_started_at

                # Trigger shift only after 6.0s persistence or if it's a deep instant breakout (>0.8x spacing)
                if (elapsed_breakout >= 6.0 or is_deep_breakout) and not self.out_of_range:
                    self.out_of_range = True
                    action = self.config.get("out_of_range_action", "auto_shift")
                    if action == "auto_shift":
                        logger.warning(f"[GridEngine] 🚀 价格持续越界 (${current_price}，持续 {round(elapsed_breakout, 1)}s)，启动 🤖 AI 智能动态移网追踪 (Auto-Shift)！")
                        self.auto_shift_range(current_price, exchange)
                        return
                    elif action == "recover":
                        logger.warning(f"[GridEngine] ⚠️ 价格越界 (${current_price})，启动只减仓回收阶梯 (Recover Mode)！")
                        self.place_recovery_ladder(current_price, exchange)
                    else:
                        logger.warning(f"[GridEngine] ⚠️ 价格越界 (${current_price})，触发冲破区间平仓停止！")
                        self.stop_grid(close_position=True, exchange=exchange)
                        return
            else:
                if self._breakout_started_at > 0.0:
                    if not self.out_of_range:
                        logger.info(f"[GridEngine] 🛡️ Anti-Whipsaw 成功过滤假插针洗盘！价格已迅速收回区间 (${current_price})")
                    self._breakout_started_at = 0.0

                if self.out_of_range:
                    self.out_of_range = False
                    self.recovery = False
                    logger.info(f"[GridEngine] 🔙 价格已重回区间 (${current_price})，恢复常规等差网格！")

            # Periodic 8H Funding Rate Check (every 60s)
            if self.config.get("is_live") and exchange and hasattr(exchange, "fetch_funding_rate"):
                if now_ts - getattr(self, "_last_fr_check_at", 0.0) >= 60.0:
                    self._last_fr_check_at = now_ts
                    try:
                        ccxt_sym = get_exchange_market_symbol(exchange, symbol)
                        fr = exchange.fetch_funding_rate(ccxt_sym)
                        if fr and "fundingRate" in fr and fr["fundingRate"] is not None:
                            r_8h = float(fr["fundingRate"]) * 100.0
                            mode = (self.config.get("mode") or "neutral").lower()
                            fav = (mode == "long" and r_8h <= 0.02) or (mode == "short" and r_8h >= -0.02) or mode == "neutral"
                            self.funding_rate_info = {
                                "rate_8h_pct": round_to(r_8h, 4),
                                "est_apr_pct": round2(r_8h * 3.0 * 365.0),
                                "favorable": fav
                            }
                    except Exception as fr_err:
                        logger.debug(f"[GridEngine] Funding rate check notice: {fr_err}")

            # 1. Live Exchange Fill Check
            if self.config.get("is_live") and exchange:
                ccxt_sym = get_exchange_market_symbol(exchange, symbol)
                now = time.time()
                # Fast reconciliation every 5s (with backoff to 180s on rate limits)
                if now - self._last_reconcile_at >= 5.0:
                    self._last_reconcile_at = now
                    try:
                        # 0. Retry any pending replacement orders
                        for p_oid, p_info in list(self.active_orders.items()):
                            if p_info.get("status") == "pending_retry":
                                retries = p_info.get("retry_count", 0)
                                if retries < 5:
                                    p_info["retry_count"] = retries + 1
                                    retry_size = ensure_min_notional_amount(
                                        exchange,
                                        self.config["symbol"],
                                        p_info["price"],
                                        p_info["size_base"]
                                    )
                                    p_info["size_base"] = retry_size
                                    r_oid, r_err = self._place_live_order(
                                        exchange=exchange,
                                        symbol=self.config["symbol"],
                                        side=p_info["side"],
                                        price=p_info["price"],
                                        amount=retry_size,
                                        reduce_only=p_info["reduce_only"],
                                        mode=self.config.get("mode"),
                                        pos_side=p_info.get("pos_side")
                                    )
                                    if r_oid and isinstance(r_oid, str):
                                        p_info["live_order_id"] = r_oid
                                        p_info["status"] = "live_open"
                                        logger.info(f"[GridEngine] ✅ 止盈补单重试成功: {r_oid} ({p_info['side'].upper()} @ ${p_info['price']})")
                                else:
                                    del self.active_orders[p_oid]
                                    err_msg_clean = f"⚠️ 补单连续重试 5 次受阻，已安全清理: {p_oid} ({p_info.get('side', '').upper()} @ ${p_info.get('price', 0)})"
                                    logger.warning(f"[GridEngine] {err_msg_clean}")
                                    self.events.insert(0, f"[{datetime.now().strftime('%H:%M:%S')}] {err_msg_clean}")

                        open_orders = exchange.fetch_open_orders(ccxt_sym)
                        open_live_ids = {str(o["id"]) for o in open_orders if o.get("id")}
                        
                        filled_oids = []
                        for oid, ord_info in list(self.active_orders.items()):
                            lid = ord_info.get("live_order_id")
                            if lid and isinstance(lid, str):
                                if lid not in open_live_ids:
                                    # Verify with fetch_order that it was genuinely filled on exchange
                                    try:
                                        od = exchange.fetch_order(lid, ccxt_sym)
                                        if not isinstance(od, dict):
                                            continue
                                        status = str(od.get("status", "")).lower()
                                        filled_amt = float(od.get("filled", 0.0) or 0.0)
                                        if status == "closed" or filled_amt > 0:
                                            fill_px = float(od.get("average", 0.0) or od.get("price", ord_info["price"]))
                                            filled_oids.append((oid, ord_info, fill_px))
                                        elif status in ("canceled", "rejected", "expired"):
                                            logger.info(f"[GridEngine] ℹ️ 挂单 {lid} 在交易所状态为 {status}，从活动列表中移除")
                                            if oid in self.active_orders:
                                                del self.active_orders[oid]
                                    except Exception as fo_err:
                                        err_msg = str(fo_err).lower()
                                        if any(k in err_msg for k in ["timeout", "network", "connection", "econnreset", "429", "418", "rate limit", "overclock", "too many requests", "timestamp", "nonce", "internal"]):
                                            logger.warning(f"[GridEngine] ⚠️ 访问交易所受阻暂未确认订单 {lid} 成交状态 ({fo_err})，将在下个周期复查")
                                        elif "not found" in err_msg or "does not exist" in err_msg or "-2013" in err_msg:
                                            # Order is definitely gone from open orders and not in active records -> treat as filled
                                            filled_oids.append((oid, ord_info, ord_info["price"]))
                                        else:
                                            logger.warning(f"[GridEngine] ⚠️ 挂单查询遇到未知响应 ({fo_err})，暂不假定成交以防重复下单，下周期复查")

                        for oid, ord_info, fill_px in filled_oids:
                            if oid in self.active_orders:
                                del self.active_orders[oid]
                            self._handle_fill(ord_info, fill_px, exchange=exchange)
                    except Exception as ex_err:
                        err_s = str(ex_err).lower()
                        if "418" in err_s or "-1003" in err_s:
                            self._last_reconcile_at = now + 180.0  # 3 min backoff to allow IP unban
                        elif "429" in err_s or "rate limit" in err_s or "too many requests" in err_s:
                            self._last_reconcile_at = now + 60.0   # 1 min backoff to avoid IP ban
                        logger.debug(f"[GridEngine] Live order check notice: {ex_err}")

            # 2. Paper / Simulation Fill Check (ONLY when NOT in live mode)
            elif not self.config.get("is_live"):
                filled_ids = []
                for oid, ord_info in list(self.active_orders.items()):
                    price = ord_info["price"]
                    side = ord_info["side"]
                    is_filled = False
                    
                    if side == "buy" and low_px <= price:
                        is_filled = True
                    elif side == "sell" and high_px >= price:
                        is_filled = True

                    if is_filled:
                        filled_ids.append(oid)
                        self._handle_fill(ord_info, price, exchange=None)

                for fid in filled_ids:
                    if fid in self.active_orders:
                        del self.active_orders[fid]

            self._save_state()

    def _handle_fill(self, filled_order: Dict[str, Any], fill_price: float, exchange=None):
        """
        Record fill statistics and place the replacement order one rung away.
        """
        side = filled_order["side"]
        size_base = filled_order.get("size_base", self.config["size_base"])
        level_idx = filled_order.get("level_index", 0)
        
        if side == "buy":
            self.stats["buys"] += 1
        else:
            self.stats["sells"] += 1
            
        fill_val = round2(fill_price * size_base)
        self.stats["volume_usd"] = round2(self.stats["volume_usd"] + fill_val)
        fill_fee = round_to(fill_val * 0.0002, 4)
        self.stats["total_fees_usd"] = round_to(self.stats.get("total_fees_usd", 0.0) + fill_fee, 4)
        self._cached_live_pos = None
        self._last_live_pos_fetch_at = 0.0

        fill_record = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "side": side,
            "price": fill_price,
            "size": size_base,
            "level": level_idx,
            "fee_usd": fill_fee
        }
        self.fills.insert(0, fill_record)
        if len(self.fills) > 50:
            self.fills.pop()

        # Calculate captured rung profit based on completed paired rounds
        # In grid trading with initial position:
        # - Long mode: every SELL is a completed paired profit round
        # - Short mode: every BUY is a completed paired profit round
        # - Neutral / Standard mode: min(buys, sells)
        spacing = self.grid.get("spacing", 0.0) if self.grid else 0.0
        rung_profit = round_to(spacing * size_base, 4)
        has_init = bool(self.config.get("has_initial_position", False)) if self.config else False
        active_mode = (self.config.get("mode", "neutral") if self.config else "neutral").lower()

        is_paired_profit = False
        if has_init and active_mode == "long":
            paired_rounds = self.stats["sells"]
            if side == "sell":
                is_paired_profit = True
        elif has_init and active_mode == "short":
            paired_rounds = self.stats["buys"]
            if side == "buy":
                is_paired_profit = True
        else:
            paired_rounds = min(self.stats["buys"], self.stats["sells"])
            if (side == "sell" and self.stats["sells"] <= self.stats["buys"]) or (side == "buy" and self.stats["buys"] <= self.stats["sells"]):
                is_paired_profit = True

        self.stats["completed_rungs"] = paired_rounds
        if is_paired_profit:
            self.stats["grid_profit_usd"] = round2(self.stats.get("grid_profit_usd", 0.0) + rung_profit)
        elif self.stats.get("grid_profit_usd", 0.0) == 0.0 and paired_rounds > 0:
            self.stats["grid_profit_usd"] = round2(paired_rounds * rung_profit)

        total_profit = float(self.stats.get("grid_profit_usd", 0.0))
        total_fees = float(self.stats.get("total_fees_usd", 0.0))
        self.stats["net_grid_profit_usd"] = round2(total_profit - total_fees)

        logger.info(
            f"[GridEngine] 💰 格位真实成交 ({side.upper()} @ ${fill_price}) | "
            f"单格锁定: +${rung_profit} USD (手续费: ${fill_fee}) | 已配对套利: {paired_rounds} 轮 (买 {self.stats['buys']} / 卖 {self.stats['sells']}) | 累计净网格利润: ${self.stats['net_grid_profit_usd']} USD"
        )

        # Update volume-weighted average entry price for paper simulation & fallback
        curr_avg = getattr(self, "_avg_entry_price", fill_price) or fill_price
        mode = (self.config.get("mode", "neutral") if self.config else "neutral").lower()

        if mode == "long":
            init_size = float(self.config.get("initial_position_size", 0.0))
            current_pos = max(0.0, init_size + (self.stats["buys"] - self.stats["sells"]) * size_base)
            if side == "buy" and current_pos > 0:
                prev_pos = max(0.0, current_pos - size_base)
                self._avg_entry_price = round_to((prev_pos * curr_avg + size_base * fill_price) / current_pos, 8)
        elif mode == "short":
            init_size = float(self.config.get("initial_position_size", 0.0))
            current_pos = max(0.0, init_size + (self.stats["sells"] - self.stats["buys"]) * size_base)
            if side == "sell" and current_pos > 0:
                prev_pos = max(0.0, current_pos - size_base)
                self._avg_entry_price = round_to((prev_pos * curr_avg + size_base * fill_price) / current_pos, 8)
        else:  # Neutral
            net_delta = (self.stats["buys"] - self.stats["sells"]) * size_base
            if side == "buy":
                prev_delta = net_delta - size_base
                if prev_delta >= 0 and net_delta > 0:
                    self._avg_entry_price = round_to((prev_delta * curr_avg + size_base * fill_price) / net_delta, 8)
                elif prev_delta < 0 and net_delta > 0:
                    self._avg_entry_price = fill_price
            elif side == "sell":
                prev_delta = net_delta + size_base
                if prev_delta <= 0 and abs(net_delta) > 0:
                    self._avg_entry_price = round_to((abs(prev_delta) * curr_avg + size_base * fill_price) / abs(net_delta), 8)
                elif prev_delta > 0 and net_delta < 0:
                    self._avg_entry_price = fill_price

        # Place replacement order one rung away
        if not self.out_of_range and self.running and not self.recovery:
            repl = replacement_for(filled_order, self.grid["levels"], self.config["mode"])
            if repl:
                new_oid = f"grid-{int(time.time()*1000)}-{repl['level_index']}"
                mult = repl.get("size_multiplier", 1.0)
                p_side = repl.get("pos_side")
                repl_size = ensure_min_notional_amount(exchange, self.config["symbol"], repl["price"], size_base) if (self.config.get("is_live") and exchange) else size_base
                self.active_orders[new_oid] = {
                    "order_id": new_oid,
                    "level_index": repl["level_index"],
                    "price": repl["price"],
                    "side": repl["side"],
                    "pos_side": p_side,
                    "size_base": repl_size,
                    "size_multiplier": mult,
                    "reduce_only": repl["reduce_only"],
                    "placed_at": time.time(),
                    "status": "open",
                    "live_order_id": None
                }

                # If live, immediately place replacement order on exchange
                if self.config.get("is_live") and exchange:
                    live_oid, err = self._place_live_order(
                        exchange=exchange,
                        symbol=self.config["symbol"],
                        side=repl["side"],
                        price=repl["price"],
                        amount=repl_size,
                        reduce_only=repl["reduce_only"],
                        mode=self.config.get("mode"),
                        pos_side=p_side
                    )
                    if live_oid and isinstance(live_oid, str):
                        self.active_orders[new_oid]["live_order_id"] = live_oid
                        self.active_orders[new_oid]["status"] = "live_open"
                    else:
                        self.active_orders[new_oid]["status"] = "pending_retry"
                        self.active_orders[new_oid]["retry_count"] = 0
                        err_desc = f"⚠️ 实盘对冲挂单暂未成功 ({repl['side'].upper()} @ ${repl['price']}): {err or '网络超时'}，已自动加入 5 秒重试队列"
                        logger.warning(f"[GridEngine] {err_desc}")
                        self.events.insert(0, f"[{datetime.now().strftime('%H:%M:%S')}] {err_desc}")

    def place_recovery_ladder(self, current_price: float, exchange=None):
        """
        Deploy Reduce-Only Recovery Ladder when price breaks out of the grid.
        Places 3 scaled reduce-only exit rungs along the mean-reversion pullback path.
        """
        with self._lock:
            self.recovery = True
            
            # In live mode, cancel old orders thoroughly
            if self.config.get("is_live") and exchange:
                self._force_cancel_all_live_orders(exchange, self.config['symbol'])

            self.active_orders.clear()
            size_base = self.config["size_base"]
            spacing = self.grid["spacing"]
            
            # Determine direction of stranded position
            if current_price < self.config["lower"]:
                # Long position stranded -> place reduce-only sell ladder above current price
                rungs = [
                    current_price + spacing * 0.5,
                    current_price + spacing * 1.0,
                    current_price + spacing * 1.5,
                ]
                side = "sell"
            else:
                # Short position stranded -> place reduce-only buy ladder below current price
                rungs = [
                    current_price - spacing * 0.5,
                    current_price - spacing * 1.0,
                    current_price - spacing * 1.5,
                ]
                side = "buy"

            pos_side = "LONG" if side == "sell" else "SHORT"
            sym = self.config.get("symbol", "BTC/USDT")
            price_prec, _ = get_symbol_precisions(sym, current_price)
            for i, px in enumerate(rungs):
                oid = f"recover-{int(time.time()*1000)}-{i}"
                ladder_size = ensure_min_notional_amount(exchange, self.config["symbol"], clean_px, size_base) if (self.config.get("is_live") and exchange) else size_base
                live_oid = None
                if self.config.get("is_live") and exchange:
                    live_oid, _ = self._place_live_order(
                        exchange=exchange,
                        symbol=self.config["symbol"],
                        side=side,
                        price=clean_px,
                        amount=ladder_size,
                        reduce_only=True,
                        mode=self.config.get("mode"),
                        pos_side=pos_side
                    )

                self.active_orders[oid] = {
                    "order_id": oid,
                    "level_index": i,
                    "price": clean_px,
                    "side": side,
                    "pos_side": pos_side,
                    "size_base": ladder_size,
                    "size_multiplier": 1.0,
                    "reduce_only": True,
                    "recovery": True,
                    "placed_at": time.time(),
                    "status": "live_open" if live_oid else "paper_open",
                    "live_order_id": live_oid
                }

            logger.info(f"[GridEngine] 🛡️ 已铺设 3 档只减仓回收阶梯 ({side.upper()} @ {rungs})")

    def _compute_compound_scale(self) -> float:
        """
        Compute true net equity-based compounding multiplier.
        - If account is in profit (equity > initial_capital): scale up conservatively (max 2.5x).
        - If account is in drawdown (equity < initial_capital): scale down (de-leverage down to 0.4x)
          to protect capital from being wiped out.
        """
        if not (self.config or {}).get("auto_compound", True):
            return 1.0
        init_cap = float((self.config or {}).get("initial_capital_usd") or (self.risk or {}).get("required_margin_usd", 10.0))
        if init_cap <= 0:
            return 1.0

        live_bal = getattr(self, "_cached_live_bal", None)
        if live_bal and isinstance(live_bal, dict) and float(live_bal.get("totalWalletBalance", 0.0) or 0.0) > 0:
            current_equity = float(live_bal.get("totalWalletBalance", 0.0) or 0.0) + float(live_bal.get("totalUnrealizedProfit", 0.0) or 0.0)
        else:
            net_profit = float((self.drawdown_stats or {}).get("net_profit_usd") or (self.stats or {}).get("grid_profit_usd") or 0.0)
            current_equity = init_cap + net_profit

        ratio = current_equity / init_cap
        if ratio >= 1.0:
            return round2(min(2.5, ratio))
        else:
            return round2(max(0.4, ratio))

    def auto_shift_range(self, current_price: float, exchange=None):
        """
        Wide-Channel Adaptive Grid Shift on Out-of-Range:
        1. Pure Maker Limit architecture: ZERO panic market orders, ZERO market order flipping!
        2. If currently LONG and price broke upward:
           All limit sell rungs took profit. Smoothly shift the grid range higher with Maker limit BUY orders below.
        3. If currently LONG and price broke downward:
           DO NOT market sell at the lowest price! Keep inventory, widen safety support band, and place limit sell recovery orders.
        4. Symmetrically safe for SHORT mode.
        5. Compounding dynamically scales down (de-leverages) if account is in drawdown.
        """
        with self._lock:
            if not self.config:
                return

            symbol = self.config["symbol"]
            ccxt_sym = get_exchange_market_symbol(exchange, symbol)
            is_live = bool(self.config.get("is_live", False))
            old_lower = float(self.config["lower"])
            old_upper = float(self.config["upper"])
            old_mode = (self.config.get("mode") or "neutral").lower()
            grid_count = int(self.config.get("grid_count", 10))
            price_prec, a_prec = get_symbol_precisions(symbol, current_price)

            broke_upward = current_price > old_upper

            target_rung_spacing_pct = float(self.config.get("target_rung_spacing_pct", 0.0050))
            compact_span = current_price * target_rung_spacing_pct * grid_count

            if broke_upward:
                new_mode = "long"
                new_lower = round(max(current_price * 0.2, current_price - compact_span * 0.65), price_prec)
                new_upper = round(current_price + compact_span * 0.35, price_prec)
                direction_desc = "向上突破 🚀 顺势切换为偏多做市网格，平滑上移定轴（纯挂单待回踩接多）"
            else:
                new_mode = "short"
                new_lower = round(max(current_price * 0.2, current_price - compact_span * 0.35), price_prec)
                new_upper = round(current_price + compact_span * 0.65, price_prec)
                direction_desc = "向下破位 📉 顺势切换为偏空做市网格，平滑下移定轴（纯挂单待反弹挂空）"

            if new_upper <= new_lower:
                min_step = 10 ** (-price_prec) if price_prec > 0 else 1.0
                new_upper = round(new_lower + max(min_step * grid_count, current_price * 0.05), price_prec)

            # Cancel old maker limit orders
            if is_live and exchange:
                self._force_cancel_all_live_orders(exchange, symbol)
            self.active_orders.clear()

            # Net equity-based compounding
            scale_ratio = self._compute_compound_scale()
            orig_size = float(self.config.get("base_size_orig", self.config.get("size_base", 0.001)))
            new_size = round(orig_size * scale_ratio, a_prec) if a_prec > 0 else math.ceil(orig_size * scale_ratio)
            size_base = max(new_size, 10 ** (-a_prec) if a_prec > 0 else 1.0)
            self.config["size_base"] = size_base

            self.config["mode"] = new_mode
            self.config["entry_price"] = current_price
            self._last_mode_flip_ts = time.time()
            self.grid = build_grid(new_lower, new_upper, grid_count, symbol=symbol)
            self.config["lower"] = self.grid["lower"]
            self.config["upper"] = self.grid["upper"]
            self.last_price = current_price
            self.out_of_range = False
            self.recovery = False
            self._recompute_risk()

            # Seed new 100% Maker Limit orders only (NO market orders!)
            sizing_mode = self.config.get("sizing_mode", "pyramid")
            seeds = seed_orders(self.grid["levels"], current_price, new_mode, self.grid["spacing"], base_size=size_base, sizing_mode=sizing_mode)

            # Check existing position on exchange
            existing_pos = 0.0
            if is_live and exchange:
                try:
                    pos_list = exchange.fetch_positions([ccxt_sym])
                    for p in pos_list:
                        p_sym = p.get("symbol", "")
                        if p_sym and not is_matching_symbol(p_sym, symbol, ccxt_sym):
                            continue
                        amt, p_side = parse_exchange_position(p)
                        if (new_mode == "long" and p_side == "LONG") or (new_mode == "short" and p_side == "SHORT"):
                            existing_pos += amt
                except Exception:
                    pass

            if existing_pos > 0:
                self.config["has_initial_position"] = True
                self.config["initial_position_size"] = existing_pos
                self.config["initial_position_side"] = new_mode
            else:
                self.config["has_initial_position"] = False
                if new_mode == "long":
                    seeds = [s for s in seeds if s["side"] == "buy"]
                elif new_mode == "short":
                    seeds = [s for s in seeds if s["side"] == "sell"]

            for seed in seeds:
                order_id = f"grid-{int(time.time()*1000)}-{seed['level_index']}"
                order_size = seed["size_base"]
                mult = seed.get("size_multiplier", 1.0)
                p_side = seed.get("pos_side")
                live_oid = None
                if is_live and exchange:
                    order_size = ensure_min_notional_amount(exchange, symbol, seed["price"], order_size)
                    live_oid, _ = self._place_live_order(
                        exchange=exchange,
                        symbol=symbol,
                        side=seed["side"],
                        price=seed["price"],
                        amount=order_size,
                        reduce_only=seed["reduce_only"],
                        mode=new_mode,
                        pos_side=p_side
                    )

                self.active_orders[order_id] = {
                    "order_id": order_id,
                    "level_index": seed["level_index"],
                    "price": seed["price"],
                    "side": seed["side"],
                    "pos_side": p_side,
                    "size_base": order_size,
                    "size_multiplier": mult,
                    "reduce_only": seed["reduce_only"],
                    "placed_at": time.time(),
                    "status": "live_open" if live_oid else "paper_open",
                    "live_order_id": live_oid
                }

            event_msg = f"[{datetime.now().strftime('%H:%M:%S')}] 🤖 宽幅自适应移网完成：市价 ${current_price} {direction_desc}，新做市区间 [${new_lower}, ${new_upper}] (100% Maker)"
            self.events.insert(0, event_msg)
            if len(self.events) > 30:
                self.events.pop()
            self._save_state()
            logger.info(f"[GridEngine] {event_msg}")

    def realign_grid_with_ai(self, symbol: str, new_mode: str, new_lower: float, new_upper: float, current_price: float, exchange=None, reason: str = ""):
        """
        Dynamically realign grid when AI/LLM periodic diagnosis detects a regime shift.
        """
        with self._lock:
            if not self.running or not self.config:
                return
            if self.config.get("symbol") != symbol:
                return
            
            logger.info(f"[GridEngine] 🤖 AI/大模型智能调网触发: 切换至【{new_mode.upper()}】，新区间 [${new_lower}, ${new_upper}] (原因: {reason})")
            
            is_live = bool(self.config.get("is_live", False))
            ccxt_sym = get_exchange_market_symbol(exchange, symbol)
            
            # 1. Close contrary positions if mode flipped
            old_mode = (self.config.get("mode") or "").lower()
            if is_live and exchange and old_mode and old_mode != new_mode:
                filter_side = "LONG" if new_mode == "short" else ("SHORT" if new_mode == "long" else None)
                self._close_live_position_safe(exchange, symbol, position_side_filter=filter_side)
            elif not is_live and old_mode and old_mode != new_mode and self.config.get("has_initial_position"):
                init_size = float(self.config.get("initial_position_size", 0.0))
                entry_px = float(self.config.get("entry_price") or getattr(self, "_avg_entry_price", 0.0) or current_price)
                if init_size > 0:
                    pnl = (current_price - entry_px) * init_size if old_mode == "long" else (entry_px - current_price) * init_size
                    fee = round_to(current_price * init_size * 0.0005, 4)
                    self.stats["realized_position_pnl_usd"] = round2(self.stats.get("realized_position_pnl_usd", 0.0) + pnl)
                    self.stats["total_fees_usd"] = round_to(self.stats.get("total_fees_usd", 0.0) + fee, 4)
                    grid_p = float(self.stats.get("grid_profit_usd", 0.0))
                    self.stats["net_grid_profit_usd"] = round2(grid_p + self.stats["realized_position_pnl_usd"] - self.stats["total_fees_usd"])

            # 2. Cancel all old open orders thoroughly on exchange
            if is_live and exchange:
                self._force_cancel_all_live_orders(exchange, symbol)
            self.active_orders.clear()
            self._last_mode_flip_ts = time.time()

            # 2. Update config and grid structure
            grid_count = int(self.config.get("grid_count", 10))
            size_base = float(self.config.get("size_base", 0.001))
            auto_init_pos = bool(self.config.get("auto_open_initial_position", True))

            # Dynamic Auto-Compounding size scaling (Net equity based)
            scale_ratio = self._compute_compound_scale()
            orig_size = float(self.config.get("base_size_orig", size_base))
            _, a_prec = get_symbol_precisions(symbol, current_price)
            new_size = round(orig_size * scale_ratio, a_prec) if a_prec > 0 else math.ceil(orig_size * scale_ratio)
            size_base = max(new_size, 10 ** (-a_prec) if a_prec > 0 else 1.0)
            self.config["size_base"] = size_base

            if new_upper <= new_lower:
                price_prec, _ = get_symbol_precisions(symbol, current_price)
                min_step = 10 ** (-price_prec) if price_prec > 0 else 1.0
                new_upper = round(new_lower + max(min_step * grid_count, current_price * 0.05), price_prec)

            self.config["mode"] = new_mode
            self.config["entry_price"] = current_price
            self.grid = build_grid(new_lower, new_upper, grid_count, symbol=symbol)
            self.config["lower"] = self.grid["lower"]
            self.config["upper"] = self.grid["upper"]
            if current_price > 0 and grid_count > 0:
                self.config["target_rung_spacing_pct"] = round(
                    max(0.012, (self.grid["upper"] - self.grid["lower"]) / (current_price * grid_count)), 6
                )
            self.last_price = current_price
            self._avg_entry_price = current_price
            self.out_of_range = False
            self.recovery = False
            self._recompute_risk()

            # 3. Seed new orders with pyramid sizing - 100% Pure Maker Limits (NO market orders!)
            seeds = seed_orders(self.grid["levels"], current_price, new_mode, self.grid["spacing"], base_size=size_base, sizing_mode=self.config.get("sizing_mode", "pyramid"))

            # Check existing position on exchange to adopt without market orders
            existing_pos = 0.0
            if is_live and exchange:
                try:
                    pos_list = exchange.fetch_positions([ccxt_sym])
                    for p in pos_list:
                        p_sym = p.get("symbol", "")
                        if p_sym and not is_matching_symbol(p_sym, symbol, ccxt_sym):
                            continue
                        amt, p_side = parse_exchange_position(p)
                        if (new_mode == "long" and p_side == "LONG") or (new_mode == "short" and p_side == "SHORT"):
                            existing_pos += amt
                except Exception:
                    pass

            if existing_pos > 0:
                self.config["has_initial_position"] = True
                self.config["initial_position_size"] = existing_pos
                self.config["initial_position_side"] = new_mode
                logger.info(f"[GridEngine] 🚀 AI调网直接复用已有持仓 {round(existing_pos, 4)} {symbol}，无需市价加仓！")
            else:
                self.config["has_initial_position"] = False
                if new_mode == "long":
                    seeds = [s for s in seeds if s["side"] == "buy"]
                elif new_mode == "short":
                    seeds = [s for s in seeds if s["side"] == "sell"]

            for seed in seeds:
                order_id = f"grid-{int(time.time()*1000)}-{seed['level_index']}"
                order_size = seed["size_base"]
                mult = seed.get("size_multiplier", 1.0)
                p_side = seed.get("pos_side")
                live_oid = None
                if is_live and exchange:
                    order_size = ensure_min_notional_amount(exchange, symbol, seed["price"], order_size)
                    live_oid, _ = self._place_live_order(
                        exchange=exchange,
                        symbol=symbol,
                        side=seed["side"],
                        price=seed["price"],
                        amount=order_size,
                        reduce_only=seed["reduce_only"],
                        mode=new_mode,
                        pos_side=p_side
                    )

                self.active_orders[order_id] = {
                    "order_id": order_id,
                    "level_index": seed["level_index"],
                    "price": seed["price"],
                    "side": seed["side"],
                    "pos_side": p_side,
                    "size_base": order_size,
                    "size_multiplier": mult,
                    "reduce_only": seed["reduce_only"],
                    "placed_at": time.time(),
                    "status": "live_open" if live_oid else "paper_open",
                    "live_order_id": live_oid
                }

            event_msg = f"[{datetime.now().strftime('%H:%M:%S')}] 🤖 AI/大模型智能巡检自适应调网：切换为【{new_mode.upper()}】模式，新区间 [${new_lower}, ${new_upper}]"
            self.events.insert(0, event_msg)
            if len(self.events) > 30:
                self.events.pop()

            self._save_state()
            logger.info(f"[GridEngine] {event_msg}")

    def resume_after_circuit_breaker(self, current_price: float, mtf_info: Dict[str, Any], span: float, exchange=None) -> Dict[str, Any]:
        """
        Safely re-evaluates market regime, computes new wide-band ATR bounds, and restarts grid market-making
        after user-requested or verified cooldown period. Never places market orders.
        """
        with self._lock:
            if self.running or not self.config:
                return self.get_state()

            symbol = self.config.get("symbol", "BTC/USDT")
            grid_count = int(self.config.get("grid_count", 10))
            size_base = float(self.config.get("size_base", 0.001))
            leverage = int(self.config.get("leverage", 20))
            is_live = bool(self.config.get("is_live", False))
            price_prec, amount_prec = get_symbol_precisions(symbol, current_price)

            new_mode = mtf_info.get("recommended_mode", "neutral")
            
            # Anchor channel width to ATR or minimum 14%~22% width to avoid narrow whipsaw breaks
            raw_span = float(span or 0.0)
            min_half_span = current_price * 0.07  # at least 14% total width
            effective_span = max(raw_span, min_half_span)

            if new_mode == "long":
                new_lower = round(max(current_price * 0.1, current_price - 1.3 * effective_span), price_prec)
                new_upper = round(current_price + 0.7 * effective_span, price_prec)
            elif new_mode == "short":
                new_lower = round(max(current_price * 0.1, current_price - 0.7 * effective_span), price_prec)
                new_upper = round(current_price + 1.3 * effective_span, price_prec)
            else:
                new_lower = round(max(current_price * 0.1, current_price - 1.0 * effective_span), price_prec)
                new_upper = round(current_price + 1.0 * effective_span, price_prec)

            if new_upper <= new_lower:
                min_step = 10 ** (-price_prec) if price_prec > 0 else 1.0
                new_upper = round(new_lower + max(min_step * grid_count, current_price * 0.01), price_prec)

            # Re-scale size_base with net-equity-grounded compounding (scales down on drawdowns, up on profit)
            if self.config.get("auto_compound", True):
                scale_ratio = self._compute_compound_scale()
                orig_size = float(self.config.get("base_size_orig", size_base))
                new_size = round(orig_size * scale_ratio, amount_prec) if amount_prec > 0 else math.ceil(orig_size * scale_ratio)
                if new_size > 0:
                    size_base = new_size

            cfg = dict(self.config)
            target_rung_spacing_pct = max(
                0.012,
                (new_upper - new_lower) / (current_price * max(1, grid_count)) if current_price > 0 else 0.015
            )
            cfg.update({
                "lower": new_lower,
                "upper": new_upper,
                "mode": new_mode,
                "size_base": size_base,
                "entry_price": current_price,
                "target_rung_spacing_pct": round(target_rung_spacing_pct, 6),
                "has_initial_position": False,
                "auto_open_initial_position": False
            })

            # Clear circuit breaker tripped flags before restarting
            self.drawdown_stats["circuit_breaker_tripped"] = False
            self.drawdown_stats["circuit_breaker_tripped_at"] = 0.0

            # Force wipe all live open orders on exchange before restarting to guarantee zero order collisions
            if is_live and exchange:
                self._force_cancel_all_live_orders(exchange, symbol)

            self.start_grid(cfg, current_price, exchange=exchange)

            event_msg = f"[{datetime.now().strftime('%H:%M:%S')}] 🔄 熔断冷静期重置完成！AI 宽幅重新研判：模式【{new_mode.upper()}】({mtf_info.get('regime_title')})，已纯挂单(100% Maker)平滑重启网格做市！"
            self.events.insert(0, event_msg)
            logger.info(f"[GridEngine] {event_msg}")

            return self.get_state()

    def reset_stats(self) -> Dict[str, Any]:
        """Reset runtime stats (profits, volume, fill records, drawdowns) to zero."""
        with self._lock:
            self.stats = {
                "buys": 0,
                "sells": 0,
                "completed_rungs": 0,
                "grid_profit_usd": 0.0,
                "realized_position_pnl_usd": 0.0,
                "total_fees_usd": 0.0,
                "net_grid_profit_usd": 0.0,
                "volume_usd": 0.0,
                "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S") if self.running else None
            }
            live_bal = getattr(self, "_cached_live_bal", None)
            live_w_bal = float((live_bal or {}).get("totalWalletBalance", 0.0) or 0.0) if isinstance(live_bal, dict) else 0.0
            if (self.config or {}).get("is_live") and live_w_bal > 0:
                initial_equity = round2(live_w_bal)
                self.config["initial_capital_usd"] = initial_equity
                self.config["initial_capital_explicit"] = True
            else:
                initial_equity = max(1.0, float((self.config or {}).get("initial_capital_usd") or (self.risk or {}).get("required_margin_usd", 10.0)))
            self.drawdown_stats = {
                "peak_equity_usd": initial_equity,
                "current_drawdown_usd": 0.0,
                "current_drawdown_pct": 0.0,
                "max_drawdown_usd": 0.0,
                "max_drawdown_pct": 0.0,
                "unrealized_pnl_usd": 0.0,
                "net_profit_usd": 0.0,
                "est_liq_price": 0.0,
                "liq_distance_pct": 100.0,
                "circuit_breaker_tripped": False,
                "circuit_breaker_tripped_at": 0.0,
                "circuit_breaker_cooldown_sec": 900.0,
                "auto_resume_after_breaker": False
            }
            self.fills.clear()
            self._avg_entry_price = self.last_price or 0.0
            self._save_state()
            logger.info("[GridEngine] 🧹 已成功清空重置网格统计数据与历史成交记录")
            return self.get_state()

    def get_state(self) -> Dict[str, Any]:
        """Return the complete runtime state of the grid engine with commercial metrics."""
        with self._lock:
            # Dynamic APY and Velocity calculation
            apy_est_pct = 0.0
            profit_velocity_usd_hr = 0.0
            if self.running and self.stats.get("started_at") and self.stats.get("grid_profit_usd", 0.0) > 0:
                try:
                    start_dt = datetime.strptime(self.stats["started_at"], "%Y-%m-%d %H:%M:%S")
                    elapsed_hours = max(0.05, (datetime.now() - start_dt).total_seconds() / 3600.0)
                    profit = float(self.stats["grid_profit_usd"])
                    profit_velocity_usd_hr = round_to(profit / elapsed_hours, 4)
                    margin_base = max(1.0, float((self.risk or {}).get("required_margin_usd", 5.0)))
                    apy_est_pct = round2((profit_velocity_usd_hr * 24.0 * 365.0 / margin_base) * 100.0)
                except Exception:
                    pass

            # Margin Safety Score (0-100)
            safety_score = 95
            if self.out_of_range:
                safety_score = 60
            elif self.recovery:
                safety_score = 75
            
            # Reduce safety score if drawdown is elevated
            curr_dd = float((self.drawdown_stats or {}).get("current_drawdown_pct", 0.0))
            if curr_dd > 10.0:
                safety_score = max(30, safety_score - int(curr_dd * 2.5))

            # Auto-Compounding Scale Ratio grounded in true net equity
            compound_scale = self._compute_compound_scale()

            # Circuit Breaker Cooldown Calculation
            tripped = bool((self.drawdown_stats or {}).get("circuit_breaker_tripped", False))
            tripped_at = float((self.drawdown_stats or {}).get("circuit_breaker_tripped_at", 0.0))
            cooldown_sec = float((self.drawdown_stats or {}).get("circuit_breaker_cooldown_sec", 900.0))
            remaining_cooldown = 0
            if tripped and tripped_at > 0:
                elapsed = time.time() - tripped_at
                remaining_cooldown = max(0, int(cooldown_sec - elapsed))

            return {
                "running": self.running,
                "config": self.config,
                "grid": self.grid,
                "risk": self.risk,
                "stats": self.stats,
                "drawdown_stats": self.drawdown_stats,
                "funding_rate_info": self.funding_rate_info,
                "compound_scale_ratio": compound_scale,
                "circuit_breaker_cooldown_remaining_sec": remaining_cooldown,
                "active_orders_count": len(self.active_orders),
                "active_orders": list(self.active_orders.values()),
                "recent_fills": self.fills[:10],
                "events": self.events[:20],
                "last_price": self.last_price,
                "out_of_range": self.out_of_range,
                "recovery": self.recovery,
                "apy_est_pct": apy_est_pct,
                "profit_velocity_usd_hr": profit_velocity_usd_hr,
                "safety_score": safety_score,
                "latest_regime_info": getattr(self, "latest_regime_info", None)
            }

# Singleton instance with thread-safe double-checked locking
_grid_engine: Optional[GridEngine] = None
_grid_engine_lock = threading.Lock()

def get_grid_engine() -> GridEngine:
    global _grid_engine
    if _grid_engine is None:
        with _grid_engine_lock:
            if _grid_engine is None:
                _grid_engine = GridEngine()
    return _grid_engine
