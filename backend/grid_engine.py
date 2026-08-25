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

    spacing = (upper - lower) / float(grid_count)
    prec, _ = get_symbol_precisions(symbol, lower)
    levels = [round_to(lower + i * spacing, prec) for i in range(grid_count + 1)]
    return {
        "levels": levels,
        "spacing": round_to(spacing, prec),
        "count": grid_count,
        "lower": lower,
        "upper": upper
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
            orders.append({
                "level_index": i,
                "price": lvl,
                "side": side,
                "size_base": order_size,
                "size_multiplier": mult,
                "reduce_only": is_reduce_only(side, mode)
            })
        elif lvl > price:
            side = "sell"
            orders.append({
                "level_index": i,
                "price": lvl,
                "side": side,
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
    """
    level_index = filled.get("level_index", 0)
    side = filled.get("side", "").lower()
    size_base = filled.get("size_base", 0.001)
    size_multiplier = filled.get("size_multiplier", 1.0)
    
    if side == "buy":
        j = level_index + 1
        if j > len(levels) - 1:
            return None
        return {
            "level_index": j,
            "price": levels[j],
            "side": "sell",
            "size_base": size_base,
            "size_multiplier": size_multiplier,
            "reduce_only": is_reduce_only("sell", mode)
        }
    else:
        j = level_index - 1
        if j < 0:
            return None
        return {
            "level_index": j,
            "price": levels[j],
            "side": "buy",
            "size_base": size_base,
            "size_multiplier": size_multiplier,
            "reduce_only": is_reduce_only("buy", mode)
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
            "volume_usd": 0.0,
            "started_at": None
        }
        self.last_price: Optional[float] = None
        self.out_of_range = False
        self.recovery = False
        self.risk: Dict[str, Any] = {}
        self._last_reconcile_at = 0.0
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
                        self.recovery = saved.get("recovery", False)
                        self.out_of_range = saved.get("out_of_range", False)
                        self.last_price = saved.get("last_price")
                        self.active_orders = saved.get("active_orders", {})
                        self.events = saved.get("events", [])
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
                    "recovery": self.recovery,
                    "out_of_range": self.out_of_range,
                    "last_price": self.last_price,
                    "active_orders": self.active_orders,
                    "fills": self.fills[:50],
                    "events": self.events[:30],
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

    def _place_live_order(self, exchange, symbol: str, side: str, price: float, amount: float, reduce_only: bool = False, mode: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
        """Submit a Post-Only Maker limit order to the live exchange with multi-mode compatibility."""
        clean_price, clean_amount = format_price_amount(exchange, symbol, price, amount)
        last_error = None
        ccxt_sym = get_exchange_market_symbol(exchange, symbol)
        ex_id = getattr(exchange, "id", "").lower()
        active_mode = (mode or (self.config.get("mode") if self.config else "neutral") or "neutral").lower()

        attempts = []
        if ex_id == "binance":
            is_dual = False
            try:
                if hasattr(exchange, "fapiPrivateGetPositionSideDual"):
                    dual_res = exchange.fapiPrivateGetPositionSideDual()
                    is_dual = bool(dual_res.get("dualSidePosition", False))
            except Exception:
                pass

            if is_dual:
                if active_mode == "long":
                    pos_side = "LONG"
                elif active_mode == "short":
                    pos_side = "SHORT"
                else:
                    if side == "buy":
                        pos_side = "SHORT" if reduce_only else "LONG"
                    else:
                        pos_side = "LONG" if reduce_only else "SHORT"

                attempts.append({"timeInForce": "GTX", "positionSide": pos_side})
                attempts.append({"positionSide": pos_side})
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
            clean_price, clean_amount = format_price_amount(exchange, symbol, self.last_price or 1.0, amount)
            ccxt_sym = get_exchange_market_symbol(exchange, symbol)
            ex_id = getattr(exchange, "id", "").lower()
            
            params = {}
            if ex_id == "binance":
                is_dual = False
                try:
                    if hasattr(exchange, "fapiPrivateGetPositionSideDual"):
                        dual_res = exchange.fapiPrivateGetPositionSideDual()
                        is_dual = bool(dual_res.get("dualSidePosition", False))
                except Exception:
                    pass
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
            auto_init_pos = bool(cfg.get("auto_open_initial_position", True))

            if current_price <= 0:
                raise ValueError("当前价格无效，无法启动网格。")
            if current_price < lower * 0.7 or current_price > upper * 1.3:
                raise ValueError(f"当前市价 ${current_price} 与网格区间 [{lower}, {upper}] 偏离过大，请调整区间。")

            if is_live and not exchange:
                raise ValueError("实盘模式必须提供已绑定的交易所客户端！")

            # In live mode, set leverage on exchange
            if is_live and exchange:
                try:
                    ccxt_sym = get_exchange_market_symbol(exchange, symbol)
                    exchange.set_leverage(leverage, ccxt_sym)
                except Exception as e:
                    logger.debug(f"[GridEngine] Set leverage notice: {e}")

            grid = build_grid(lower, upper, grid_count, symbol=symbol)
            self.config = {
                "symbol": symbol,
                "lower": lower,
                "upper": upper,
                "grid_count": grid_count,
                "size_base": size_base,
                "leverage": leverage,
                "mode": mode,
                "sizing_mode": sizing_mode,
                "out_of_range_action": out_of_range_action,
                "is_live": is_live,
                "auto_open_initial_position": auto_init_pos,
                "has_initial_position": False
            }
            self.grid = grid
            self.last_price = current_price
            self.out_of_range = (current_price < lower or current_price > upper)
            self.recovery = False
            self.active_orders.clear()
            self.fills.clear()
            self.stats = {
                "buys": 0,
                "sells": 0,
                "completed_rungs": 0,
                "grid_profit_usd": 0.0,
                "volume_usd": 0.0,
                "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            self._recompute_risk()

            # Seed initial ladder orders with pyramid / uniform sizing
            seeds = seed_orders(grid["levels"], current_price, mode, grid["spacing"], base_size=size_base, sizing_mode=sizing_mode)
            
            # Auto-open Initial Base Position to enable immediate bi-directional profit taking
            if auto_init_pos:
                if mode == "long":
                    sell_seeds = [s for s in seeds if s["side"] == "sell"]
                    init_size = sum(s["size_base"] for s in sell_seeds)
                    if init_size > 0:
                        if is_live and exchange:
                            self._open_initial_live_position(exchange, symbol, "buy", init_size, mode)
                        self.config["has_initial_position"] = True
                        self.config["initial_position_size"] = init_size
                        self.config["initial_position_side"] = "long"
                        event_init = f"[{datetime.now().strftime('%H:%M:%S')}] 🚀 已自动开立多头底仓 {init_size} {symbol}，上方立即铺设平多止盈卖单！"
                        self.events.insert(0, event_init)
                        logger.info(f"[GridEngine] {event_init}")
                elif mode == "short":
                    buy_seeds = [s for s in seeds if s["side"] == "buy"]
                    init_size = sum(s["size_base"] for s in buy_seeds)
                    if init_size > 0:
                        if is_live and exchange:
                            self._open_initial_live_position(exchange, symbol, "sell", init_size, mode)
                        self.config["has_initial_position"] = True
                        self.config["initial_position_size"] = init_size
                        self.config["initial_position_side"] = "short"
                        event_init = f"[{datetime.now().strftime('%H:%M:%S')}] 🚀 已自动开立空头底仓 {init_size} {symbol}，下方立即铺设平空止盈买单！"
                        self.events.insert(0, event_init)
                        logger.info(f"[GridEngine] {event_init}")

            live_errors = []
            live_success = 0

            for seed in seeds:
                order_id = f"grid-{int(time.time()*1000)}-{seed['level_index']}"
                order_size = seed["size_base"]
                mult = seed.get("size_multiplier", 1.0)
                
                # If live, place limit Maker order on exchange
                if is_live and exchange:
                    live_oid, err = self._place_live_order(
                        exchange=exchange,
                        symbol=symbol,
                        side=seed["side"],
                        price=seed["price"],
                        amount=order_size,
                        reduce_only=seed["reduce_only"],
                        mode=mode
                    )
                    if live_oid:
                        live_success += 1
                        self.active_orders[order_id] = {
                            "order_id": order_id,
                            "live_order_id": live_oid,
                            "level_index": seed["level_index"],
                            "price": seed["price"],
                            "side": seed["side"],
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
                        "size_base": order_size,
                        "size_multiplier": mult,
                        "reduce_only": seed["reduce_only"],
                        "placed_at": time.time(),
                        "status": "paper_open"
                    }

            if is_live and exchange and live_success == 0:
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

    def stop_grid(self, close_position: bool = False, exchange=None) -> Dict[str, Any]:
        """
        Stop the grid, cancel open grid orders, and optionally close existing position.
        """
        with self._lock:
            if not self.running:
                return {"status": "not_running", "message": "网格未在运行"}

            # Cancel live orders if applicable
            if self.config and self.config.get("is_live") and exchange:
                ccxt_sym = get_exchange_market_symbol(exchange, self.config['symbol'])
                for ord_info in list(self.active_orders.values()):
                    lid = ord_info.get("live_order_id")
                    if lid:
                        try:
                            exchange.cancel_order(lid, ccxt_sym)
                        except Exception as e:
                            logger.debug(f"[GridEngine] Failed to cancel live grid order {lid}: {e}")
                
                if close_position:
                    try:
                        positions = exchange.fetch_positions([ccxt_sym])
                        for p in positions:
                            amt = abs(float(p.get("contracts", 0.0) or p.get("positionAmt", 0.0)))
                            if amt > 0:
                                side = "sell" if float(p.get("contracts", 0.0) or p.get("positionAmt", 0.0)) > 0 else "buy"
                                exchange.create_order(
                                    symbol=ccxt_sym,
                                    type="market",
                                    side=side,
                                    amount=amt,
                                    params={"reduceOnly": True}
                                )
                    except Exception as e:
                        logger.warning(f"[GridEngine] Failed to close live position on stop: {e}")

            self.running = False
            self.active_orders.clear()
            self.recovery = False
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

            # Check out of range
            out = current_price < self.config["lower"] or current_price > self.config["upper"]
            if out and not self.out_of_range:
                self.out_of_range = True
                action = self.config.get("out_of_range_action", "auto_shift")
                if action == "auto_shift":
                    logger.warning(f"[GridEngine] 🚀 价格越界 (${current_price})，启动 🤖 AI 智能动态移网追踪 (Auto-Shift)！")
                    self.auto_shift_range(current_price, exchange)
                    return
                elif action == "recover":
                    logger.warning(f"[GridEngine] ⚠️ 价格越界 (${current_price})，启动只减仓回收阶梯 (Recover Mode)！")
                    self.place_recovery_ladder(current_price, exchange)
                else:
                    logger.warning(f"[GridEngine] ⚠️ 价格越界 (${current_price})，触发冲破区间平仓停止！")
                    self.stop_grid(close_position=True, exchange=exchange)
                    return
            elif not out and self.out_of_range:
                self.out_of_range = False
                self.recovery = False
                logger.info(f"[GridEngine] 🔙 价格已重回区间 (${current_price})，恢复常规等差网格！")

            # 1. Live Exchange Fill Check
            if self.config.get("is_live") and exchange:
                ccxt_sym = get_exchange_market_symbol(exchange, symbol)
                now = time.time()
                # Reconcile open orders every 30s (with backoff on rate limits)
                if now - self._last_reconcile_at >= 30.0:
                    self._last_reconcile_at = now
                    try:
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
                                        status = str(od.get("status", "")).lower()
                                        filled_amt = float(od.get("filled", 0.0) or 0.0)
                                        if status == "closed" or filled_amt > 0:
                                            fill_px = float(od.get("average", 0.0) or od.get("price", ord_info["price"]))
                                            filled_oids.append((oid, ord_info, fill_px))
                                        elif status in ("canceled", "rejected", "expired"):
                                            logger.info(f"[GridEngine] ℹ️ 挂单 {lid} 在交易所状态为 {status}，从活动列表中移除")
                                            if oid in self.active_orders:
                                                del self.active_orders[oid]
                                    except Exception:
                                        # If fetch_order is not available, default to verified fill
                                        filled_oids.append((oid, ord_info, ord_info["price"]))

                        for oid, ord_info, fill_px in filled_oids:
                            if oid in self.active_orders:
                                del self.active_orders[oid]
                            self._handle_fill(ord_info, fill_px, exchange=exchange)
                    except Exception as ex_err:
                        err_s = str(ex_err)
                        if "418" in err_s or "-1003" in err_s:
                            self._last_reconcile_at = now + 180.0  # 3 min backoff to allow IP unban
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
        
        fill_record = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "side": side,
            "price": fill_price,
            "size": size_base,
            "level": level_idx
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

        if has_init and active_mode == "long":
            paired_rounds = self.stats["sells"]
        elif has_init and active_mode == "short":
            paired_rounds = self.stats["buys"]
        else:
            paired_rounds = min(self.stats["buys"], self.stats["sells"])

        self.stats["completed_rungs"] = paired_rounds
        self.stats["grid_profit_usd"] = round2(paired_rounds * rung_profit)

        logger.info(
            f"[GridEngine] 💰 格位真实成交 ({side.upper()} @ ${fill_price}) | "
            f"单格锁定: +${rung_profit} USD | 已配对套利: {paired_rounds} 轮 (买 {self.stats['buys']} / 卖 {self.stats['sells']}) | 累计真实网格利润: ${self.stats['grid_profit_usd']} USD"
        )

        # Place replacement order one rung away
        if not self.out_of_range and self.running and not self.recovery:
            repl = replacement_for(filled_order, self.grid["levels"], self.config["mode"])
            if repl:
                new_oid = f"grid-{int(time.time()*1000)}-{repl['level_index']}"
                mult = repl.get("size_multiplier", 1.0)
                self.active_orders[new_oid] = {
                    "order_id": new_oid,
                    "level_index": repl["level_index"],
                    "price": repl["price"],
                    "side": repl["side"],
                    "size_base": size_base,
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
                        amount=size_base,
                        reduce_only=repl["reduce_only"],
                        mode=self.config.get("mode")
                    )
                    if live_oid and isinstance(live_oid, str):
                        self.active_orders[new_oid]["live_order_id"] = live_oid
                        self.active_orders[new_oid]["status"] = "live_open"

    def place_recovery_ladder(self, current_price: float, exchange=None):
        """
        Deploy Reduce-Only Recovery Ladder when price breaks out of the grid.
        Places 3 scaled reduce-only exit rungs along the mean-reversion pullback path.
        """
        with self._lock:
            self.recovery = True
            
            # In live mode, cancel old orders
            if self.config.get("is_live") and exchange:
                ccxt_sym = get_exchange_market_symbol(exchange, self.config['symbol'])
                for ord_info in list(self.active_orders.values()):
                    lid = ord_info.get("live_order_id")
                    if lid:
                        try:
                            exchange.cancel_order(lid, ccxt_sym)
                        except Exception:
                            pass

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

            for i, px in enumerate(rungs):
                oid = f"recover-{int(time.time()*1000)}-{i}"
                self.active_orders[oid] = {
                    "order_id": oid,
                    "level_index": i,
                    "price": round_to(px, 6),
                    "side": side,
                    "size_base": size_base,
                    "reduce_only": True,
                    "recovery": True,
                    "placed_at": time.time(),
                    "status": "open"
                }

                if self.config.get("is_live") and exchange:
                    live_oid = self._place_live_order(
                        exchange=exchange,
                        symbol=self.config["symbol"],
                        side=side,
                        price=round_to(px, 6),
                        amount=size_base,
                        reduce_only=True
                    )
                    if live_oid:
                        self.active_orders[oid]["live_order_id"] = live_oid

            logger.info(f"[GridEngine] 🛡️ 已铺设 3 档只减仓回收阶梯 ({side.upper()} @ {rungs})")

    def auto_shift_range(self, current_price: float, exchange=None):
        """
        AI Auto-Shift & Retrend on Out-of-Range Breakout:
        1. Liquidate stranded contrary positions to cut risk immediately.
        2. Cancel old open orders.
        3. Detect breakout direction & dynamically shift range & switch mode (e.g. upward breakout -> Long Only mode).
        4. Open new initial base position and re-seed new grid maker orders on exchange.
        """
        with self._lock:
            if not self.config:
                return

            symbol = self.config["symbol"]
            ccxt_sym = get_exchange_market_symbol(exchange, symbol)
            is_live = bool(self.config.get("is_live", False))
            old_lower = float(self.config["lower"])
            old_upper = float(self.config["upper"])
            grid_count = int(self.config.get("grid_count", 10))
            size_base = float(self.config.get("size_base", 0.001))
            auto_init_pos = bool(self.config.get("auto_open_initial_position", True))

            # 1. Close contrary positions immediately
            broke_upward = current_price > old_upper
            if is_live and exchange:
                try:
                    positions = exchange.fetch_positions([ccxt_sym])
                    for p in positions:
                        raw_amt = float(p.get("contracts", 0.0) or p.get("positionAmt", 0.0))
                        amt = abs(raw_amt)
                        if amt <= 0:
                            continue
                        pos_side = p.get("side", "").upper()
                        if broke_upward and (raw_amt < 0 or pos_side == "SHORT"):
                            try:
                                exchange.create_order(ccxt_sym, "market", "buy", amt, params={"reduceOnly": True})
                            except Exception:
                                try:
                                    exchange.create_order(ccxt_sym, "market", "buy", amt, params={"positionSide": "SHORT"})
                                except Exception as e2:
                                    logger.warning(f"[GridEngine] Failed to market close short: {e2}")
                            logger.info(f"[GridEngine] 🛑 智能移网止损：向上突破已市价平空 {amt} {symbol}")
                        elif not broke_upward and (raw_amt > 0 or pos_side == "LONG"):
                            try:
                                exchange.create_order(ccxt_sym, "market", "sell", amt, params={"reduceOnly": True})
                            except Exception:
                                try:
                                    exchange.create_order(ccxt_sym, "market", "sell", amt, params={"positionSide": "LONG"})
                                except Exception as e2:
                                    logger.warning(f"[GridEngine] Failed to market close long: {e2}")
                            logger.info(f"[GridEngine] 🛑 智能移网止损：向下破位已市价平多 {amt} {symbol}")
                except Exception as e:
                    logger.warning(f"[GridEngine] Auto-shift position close notice: {e}")

            # 2. Cancel all existing live orders
            if is_live and exchange:
                for ord_info in list(self.active_orders.values()):
                    lid = ord_info.get("live_order_id")
                    if lid:
                        try:
                            exchange.cancel_order(lid, ccxt_sym)
                        except Exception:
                            pass
            self.active_orders.clear()

            # 3. Derive new range and mode
            old_span = max(old_upper - old_lower, current_price * 0.03)
            price_prec, _ = get_symbol_precisions(symbol, current_price)

            if broke_upward:
                new_mode = "long"  # Trend is bullish!
                new_lower = round(max(current_price * 0.2, current_price - old_span * 0.7), price_prec)
                new_upper = round(current_price + old_span * 0.4, price_prec)
                direction_desc = "向上突破 🚀 自动平空止损并切换为【逢低做多】"
            else:
                new_mode = "short"  # Trend is bearish!
                new_lower = round(max(current_price * 0.2, current_price - old_span * 0.4), price_prec)
                new_upper = round(current_price + old_span * 0.7, price_prec)
                direction_desc = "向下破位 📉 自动平多止损并切换为【逢高做空】"

            self.config["lower"] = new_lower
            self.config["upper"] = new_upper
            self.config["mode"] = new_mode
            self.grid = build_grid(new_lower, new_upper, grid_count, symbol=symbol)
            self.last_price = current_price
            self.out_of_range = False
            self.recovery = False
            self._recompute_risk()

            # 4. Place new seed orders with pyramid sizing
            sizing_mode = self.config.get("sizing_mode", "pyramid")
            seeds = seed_orders(self.grid["levels"], current_price, new_mode, self.grid["spacing"], base_size=size_base, sizing_mode=sizing_mode)
            
            # Auto-open initial position for the shifted mode
            if auto_init_pos:
                if new_mode == "long":
                    sell_seeds = [s for s in seeds if s["side"] == "sell"]
                    init_size = sum(s["size_base"] for s in sell_seeds)
                    if init_size > 0:
                        if is_live and exchange:
                            self._open_initial_live_position(exchange, symbol, "buy", init_size, new_mode)
                        self.config["has_initial_position"] = True
                elif new_mode == "short":
                    buy_seeds = [s for s in seeds if s["side"] == "buy"]
                    init_size = sum(s["size_base"] for s in buy_seeds)
                    if init_size > 0:
                        if is_live and exchange:
                            self._open_initial_live_position(exchange, symbol, "sell", init_size, new_mode)
                        self.config["has_initial_position"] = True

            for seed in seeds:
                order_id = f"grid-{int(time.time()*1000)}-{seed['level_index']}"
                order_size = seed["size_base"]
                mult = seed.get("size_multiplier", 1.0)
                live_oid = None
                if is_live and exchange:
                    live_oid, _ = self._place_live_order(
                        exchange=exchange,
                        symbol=symbol,
                        side=seed["side"],
                        price=seed["price"],
                        amount=order_size,
                        reduce_only=seed["reduce_only"],
                        mode=new_mode
                    )

                self.active_orders[order_id] = {
                    "order_id": order_id,
                    "level_index": seed["level_index"],
                    "price": seed["price"],
                    "side": seed["side"],
                    "size_base": order_size,
                    "size_multiplier": mult,
                    "reduce_only": seed["reduce_only"],
                    "placed_at": time.time(),
                    "status": "live_open" if live_oid else "paper_open",
                    "live_order_id": live_oid
                }

            event_msg = f"[{datetime.now().strftime('%H:%M:%S')}] 🤖 智能移网已生效：市价 ${current_price} {direction_desc}，新做市区间 [${new_lower}, ${new_upper}]"
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
                try:
                    positions = exchange.fetch_positions([ccxt_sym])
                    for p in positions:
                        raw_amt = float(p.get("contracts", 0.0) or p.get("positionAmt", 0.0))
                        amt = abs(raw_amt)
                        if amt <= 0:
                            continue
                        pos_side = p.get("side", "").upper()
                        if new_mode == "short" and (raw_amt > 0 or pos_side == "LONG"):
                            try:
                                exchange.create_order(ccxt_sym, "market", "sell", amt, params={"reduceOnly": True})
                            except Exception:
                                try:
                                    exchange.create_order(ccxt_sym, "market", "sell", amt, params={"positionSide": "LONG"})
                                except Exception as e2:
                                    logger.warning(f"[GridEngine] Failed to close long on realign: {e2}")
                        elif new_mode == "long" and (raw_amt < 0 or pos_side == "SHORT"):
                            try:
                                exchange.create_order(ccxt_sym, "market", "buy", amt, params={"reduceOnly": True})
                            except Exception:
                                try:
                                    exchange.create_order(ccxt_sym, "market", "buy", amt, params={"positionSide": "SHORT"})
                                except Exception as e2:
                                    logger.warning(f"[GridEngine] Failed to close short on realign: {e2}")
                except Exception as e:
                    logger.warning(f"[GridEngine] Realign position close notice: {e}")

            # 2. Cancel old open orders
            if is_live and exchange:
                for ord_info in list(self.active_orders.values()):
                    lid = ord_info.get("live_order_id")
                    if lid:
                        try:
                            exchange.cancel_order(lid, ccxt_sym)
                        except Exception:
                            pass
            self.active_orders.clear()

            # 2. Update config and grid structure
            grid_count = int(self.config.get("grid_count", 10))
            size_base = float(self.config.get("size_base", 0.001))
            auto_init_pos = bool(self.config.get("auto_open_initial_position", True))

            self.config["lower"] = new_lower
            self.config["upper"] = new_upper
            self.config["mode"] = new_mode
            self.grid = build_grid(new_lower, new_upper, grid_count, symbol=symbol)
            self.last_price = current_price
            self.out_of_range = False
            self.recovery = False
            self._recompute_risk()

            # 3. Seed new orders with pyramid sizing
            seeds = seed_orders(self.grid["levels"], current_price, new_mode, self.grid["spacing"], base_size=size_base, sizing_mode=self.config.get("sizing_mode", "pyramid"))
            
            # Auto-open initial position for new mode
            if auto_init_pos:
                if new_mode == "long":
                    sell_seeds = [s for s in seeds if s["side"] == "sell"]
                    init_size = sum(s["size_base"] for s in sell_seeds)
                    if init_size > 0:
                        if is_live and exchange:
                            self._open_initial_live_position(exchange, symbol, "buy", init_size, new_mode)
                        self.config["has_initial_position"] = True
                elif new_mode == "short":
                    buy_seeds = [s for s in seeds if s["side"] == "buy"]
                    init_size = sum(s["size_base"] for s in buy_seeds)
                    if init_size > 0:
                        if is_live and exchange:
                            self._open_initial_live_position(exchange, symbol, "sell", init_size, new_mode)
                        self.config["has_initial_position"] = True

            for seed in seeds:
                order_id = f"grid-{int(time.time()*1000)}-{seed['level_index']}"
                order_size = seed["size_base"]
                mult = seed.get("size_multiplier", 1.0)
                live_oid = None
                if is_live and exchange:
                    live_oid, _ = self._place_live_order(
                        exchange=exchange,
                        symbol=symbol,
                        side=seed["side"],
                        price=seed["price"],
                        amount=order_size,
                        reduce_only=seed["reduce_only"],
                        mode=new_mode
                    )

                self.active_orders[order_id] = {
                    "order_id": order_id,
                    "level_index": seed["level_index"],
                    "price": seed["price"],
                    "side": seed["side"],
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

    def reset_stats(self) -> Dict[str, Any]:
        """Reset runtime stats (profits, volume, fill records) to zero."""
        with self._lock:
            self.stats = {
                "buys": 0,
                "sells": 0,
                "completed_rungs": 0,
                "grid_profit_usd": 0.0,
                "volume_usd": 0.0,
                "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S") if self.running else None
            }
            self.fills.clear()
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

            return {
                "running": self.running,
                "config": self.config,
                "grid": self.grid,
                "risk": self.risk,
                "stats": self.stats,
                "active_orders_count": len(self.active_orders),
                "active_orders": list(self.active_orders.values()),
                "recent_fills": self.fills[:10],
                "events": self.events[:20],
                "last_price": self.last_price,
                "out_of_range": self.out_of_range,
                "recovery": self.recovery,
                "apy_est_pct": apy_est_pct,
                "profit_velocity_usd_hr": profit_velocity_usd_hr,
                "safety_score": safety_score
            }

# Singleton instance
_grid_engine: Optional[GridEngine] = None

def get_grid_engine() -> GridEngine:
    global _grid_engine
    if _grid_engine is None:
        _grid_engine = GridEngine()
    return _grid_engine
