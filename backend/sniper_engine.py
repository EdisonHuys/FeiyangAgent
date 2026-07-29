import os
import json
import time
import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

# Perpetual futures funding settles every 8 hours (UTC 00:00 / 08:00 / 16:00)
FUNDING_EPOCH_SECONDS = 8 * 3600

def dynamic_round_price(v):
    if v is None:
        return 0.0
    val = float(v)
    abs_val = abs(val)
    if abs_val >= 1000:
        return round(val, 2)
    elif abs_val >= 100:
        return round(val, 3)
    elif abs_val >= 1:
        return round(val, 4)
    elif abs_val >= 0.1:
        return round(val, 5)
    else:
        return round(val, 6)

class SniperEngine:
    def __init__(self, root_dir):
        self.root_dir = root_dir
        self.state_file = os.path.join(root_dir, "trades.json")
        # Guards all state mutations: the 10s price loop, hourly LLM loop and
        # API handlers (manual close / config update) run on different threads.
        self._lock = threading.RLock()
        self.state = self._load_state()
        self._live_positions_cache = None
        self._live_positions_cache_time = 0
        self._last_positions_error = None
        self._live_balance_cache = None
        self._live_balance_cache_time = 0
        # Tombstone: symbols manually closed by user; excluded from position list for 90s
        # to avoid re-appearing due to exchange processing lag
        self._closed_external_symbols = {}  # {symbol: expiry_timestamp}

    def _load_state(self):
        default_state = {
            "config": {
                "mode": "paper",  # "off", "paper", "live"
                "paper_account_balance": 10000.0,
                "live_account_balance": 10.0,
                "initial_balance": 10000.0,
                "risk_per_trade_percent": 2.0,
                "max_active_trades": 3,
                "min_confidence": 7,
                "leverage_mode": "smart",  # "smart" or "fixed"
                "min_leverage": 20,
                "max_leverage": 50,
                "fixed_leverage": 30,
                "margin_mode": "smart",  # "smart", "account_percent", "fixed_amount"
                "margin_percent": 5.0,
                "fixed_margin_amount": 20.0,
                "max_profit_drawdown_percent": 30.0,
                "enable_exchange_sl": True,
                "live_exchange": "binance",
                "live_api_key": "",
                "live_secret": "",
                "live_passphrase": "",
                "live_trading_mode": "swap",
                # Fee & slippage model (paper-mode accounting realism).
                "taker_fee_rate": 0.0005,   # market orders / stop exits
                "maker_fee_rate": 0.0002,   # resting limit entries
                "slippage_rate": 0.0005,    # adverse price slip on market exits
                # Daily drawdown circuit breaker
                "circuit_breaker_enabled": True,
                "daily_max_loss_percent": 5.0,
                # Pending orders older than this many hours are auto-cancelled
                "pending_ttl_hours": 24.0,
                # Perpetual funding fee charged on notional every 8 hours
                "funding_rate_per_8h": 0.0001,
                # Time-based stop: close positions held longer than this without TP1
                "max_hold_hours": 72.0,
                # Per-symbol volatility multipliers (higher = wider stops for volatile alts)
                "symbol_volatility_mult": {
                    "BTC/USDT": 1.0,
                    "ETH/USDT": 1.1,
                    "ZEC/USDT": 1.3,
                    "DOGE/USDT": 1.4,
                    "HYPE/USDT": 1.5,
                    "ZAMA/USDT": 1.6
                }
            },
            "trades": []
        }
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for k, v in default_state["config"].items():
                        if k not in data.get("config", {}):
                            data.setdefault("config", {})[k] = v
                    return data
            except Exception as e:
                # A truncated/corrupt trades.json (e.g. crash mid-write) used to
                # silently wipe ALL trade history and config. Back it up first.
                backup_path = f"{self.state_file}.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                try:
                    os.replace(self.state_file, backup_path)
                    logger.error(f"trades.json is corrupt ({e}); backed up to {backup_path} and re-initialized with defaults.")
                except Exception as be:
                    logger.error(f"trades.json is corrupt ({e}) and backup failed ({be}); re-initializing with defaults.")
        return default_state

    def _save_state(self):
        # Atomic write: serialize to a temp file then os.replace, so a crash
        # mid-write can never leave a truncated trades.json behind.
        tmp_path = f"{self.state_file}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.state_file)
        except Exception as e:
            logger.error(f"Failed to save trades.json: {e}")
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass

    def _fee_rates(self):
        """Return (taker_fee_rate, maker_fee_rate, slippage_rate) from config."""
        cfg = self.state.get("config", {})
        return (
            float(cfg.get("taker_fee_rate", 0.0005)),
            float(cfg.get("maker_fee_rate", 0.0002)),
            float(cfg.get("slippage_rate", 0.0005)),
        )

    def _record_fee(self, trade, notional_usd, rate):
        """Accumulate a fee (USD) onto the trade record. Returns the fee."""
        fee = round(notional_usd * rate, 4)
        trade["fees_usd"] = round(trade.get("fees_usd", 0.0) + fee, 4)
        return fee

    # --- Daily drawdown circuit breaker --------------------------------
    def _current_balance(self):
        cfg = self.state.get("config", {})
        if cfg.get("mode") == "live":
            return float(cfg.get("live_account_balance", 0.0))
        return float(cfg.get("paper_account_balance", 10000.0))

    def is_halted(self):
        """True when today's circuit breaker has been triggered for the CURRENT mode."""
        mode = self.state.get("config", {}).get("mode", "paper")
        daily = self.state.get("daily") or {}
        return bool(daily.get(f"halted_{mode}")) and daily.get("date") == datetime.now().strftime("%Y-%m-%d")

    def _check_circuit_breaker(self):
        """
        Roll the daily tracker and halt trading if today's realized loss
        breaches daily_max_loss_percent of the day-start balance.

        Tracking is PER MODE: paper and live are different accounts with
        different balances. The previous single start_balance compared
        balances ACROSS modes — e.g. day start recorded at the $8.45 paper
        balance, then evaluated against the $0.01 live balance after a mode
        switch, producing a phantom -$8.44 "loss" and a false trigger.

        On trigger: cancel pending orders of the CURRENT mode only and push
        one alert per mode per day. Auto-resets on the next calendar day.
        Returns True if trading is currently halted (for the current mode).
        """
        cfg = self.state.get("config", {})
        if not cfg.get("circuit_breaker_enabled", True):
            return False

        mode = cfg.get("mode", "paper")
        today = datetime.now().strftime("%Y-%m-%d")
        daily = self.state.get("daily") or {}
        if daily.get("date") != today:
            # New day: drop all per-mode halt flags and baselines
            daily = {"date": today}
            self.state["daily"] = daily
            self._save_state()

        bal_key = f"start_balance_{mode}"
        if bal_key not in daily:
            # First time this mode is seen today: baseline at current balance
            daily[bal_key] = self._current_balance()
            self.state["daily"] = daily
            self._save_state()

        halted_key = f"halted_{mode}"
        if daily.get(halted_key):
            return True

        limit_pct = float(cfg.get("daily_max_loss_percent", 6.0))
        start_bal = float(daily.get(bal_key) or 0.0)
        if start_bal <= 0 or limit_pct <= 0:
            return False

        day_pnl = self._current_balance() - start_bal
        if day_pnl <= -start_bal * limit_pct / 100.0:
            daily[halted_key] = True
            self.state["daily"] = daily
            self._cancel_all_pending(reason="🚨 日内回撤熔断触发，挂单自动撤销")
            notified_key = f"notified_{mode}"
            if not daily.get(notified_key):
                daily[notified_key] = True
                self.state["daily"] = daily
                self._save_state()
                self._send_notification(
                    "🚨 日内回撤熔断已触发",
                    f"🚨 *【风控熔断通知】({mode.upper()} 模式)*\n今日已实现亏损 ${round(day_pnl, 2)} USD，触及日内最大亏损阈值 {limit_pct}%。\n已停止开新单并撤销全部挂单，明日自动复位。君子不立危墙之下——请复盘今日策略！"
                )
            logger.warning(f"[SniperEngine] DAILY CIRCUIT BREAKER TRIGGERED ({mode}): day PnL ${round(day_pnl, 2)} (limit {limit_pct}%). Trading halted until tomorrow.")
            return True
        return False

    def reset_circuit_breaker(self):
        """
        Manual reset: clear today's halt flag for the CURRENT mode and
        re-baseline its day-start balance at the current balance (the user
        acknowledges the loss; day PnL counts from zero again).
        """
        with self._lock:
            mode = self.state.get("config", {}).get("mode", "paper")
            today = datetime.now().strftime("%Y-%m-%d")
            daily = self.state.get("daily") or {}
            if daily.get("date") != today:
                daily = {"date": today}
            daily[f"halted_{mode}"] = False
            daily[f"notified_{mode}"] = False
            daily[f"start_balance_{mode}"] = self._current_balance()
            self.state["daily"] = daily
            self._save_state()
            logger.info(f"[SniperEngine] Circuit breaker manually reset ({mode}); day baseline re-set to ${self._current_balance()}.")
            return {"status": "success", "message": f"熔断已解除（{mode.upper()} 模式），今日盈亏基准已重置为当前余额。"}

    def _cancel_all_pending(self, reason=""):
        """Cancel pending orders of the CURRENT mode only (paper XOR live)."""
        mode = self.state.get("config", {}).get("mode", "paper")
        for t in self.state.get("trades", []):
            if t.get("status") != "pending":
                continue
            # Only cancel orders belonging to the halted mode's account
            if (mode == "live") != bool(t.get("is_live")):
                continue
            if t.get("is_live") and t.get("live_order_id"):
                try:
                    exchange, ex_id = self._init_live_ccxt()
                    ccxt_symbol = f"{t['symbol']}:USDT" if ":" not in t['symbol'] else t['symbol']
                    exchange.cancel_order(t["live_order_id"], ccxt_symbol)
                    # Clean up SL/TP conditional orders attached to the cancelled pending order
                    self._cancel_all_conditional_orders_for_symbol(t["symbol"])
                except Exception as e:
                    # Keep it tracked: fills are blocked by the halt guard, and
                    # the live-order sync keeps watching the exchange order.
                    logger.warning(f"[SniperEngine] Failed to cancel live pending order during halt: {e} — 保持 pending 跟踪")
                    continue
            t["status"] = "cancelled"
            t["close_reason"] = reason or "系统批量撤销挂单"

    def sync_watchlist_symbols(self, active_symbols):
        """Cancel pending orders for symbols removed from the watchlist."""
        with self._lock:
            trades = self.state.get("trades", [])
            updated = False
            for t in trades:
                if t.get("status") == "pending" and t.get("symbol") not in active_symbols:
                    if t.get("is_live") and t.get("live_order_id"):
                        try:
                            exchange, ex_id = self._init_live_ccxt()
                            ccxt_symbol = f"{t['symbol']}:USDT" if ":" not in t['symbol'] else t['symbol']
                            exchange.cancel_order(t["live_order_id"], ccxt_symbol)
                            # Clean up SL/TP conditional orders attached to the cancelled pending order
                            self._cancel_all_conditional_orders_for_symbol(t["symbol"])
                        except Exception as cancel_e:
                            logger.warning(f"[LiveSniper] Cancel order for removed symbol {t['symbol']} failed: {cancel_e}")
                    t["status"] = "cancelled"
                    t["close_reason"] = f"🗑️ 币种 {t['symbol']} 已从自选监控列表中删除，未成交挂单自动撤销作废"
                    updated = True
                    logger.info(f"[SniperEngine] Cancelled pending trade for removed symbol {t['symbol']}.")
            if updated:
                self._save_state()

    # --- Exchange-side protective stop (live mode safety net) -----------
    def _place_live_protective_sl(self, exchange, ex_id, symbol, sig_type, amount, stop_loss):
        """
        Best-effort exchange-side STOP-MARKET order after a live fill.
        The local 10s double-insurance only works while this app is alive;
        an exchange-side stop keeps the position protected through app
        crashes, sleep and network drops. Failures trigger a loud push so
        the user can set the stop manually.
        """
        if not stop_loss or amount <= 0:
            return None

        cfg = self.state.get("config", {})
        if not cfg.get("enable_exchange_sl", True):
            logger.info(f"[LiveSniper] 交易所侧同步挂止损单已根据设置关闭 ({symbol})")
            return None

        ccxt_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
        close_side = "sell" if sig_type == "long" else "buy"
        pos_side = "LONG" if sig_type == "long" else "SHORT"
        try:
            if ex_id == "binance":
                # Binance Futures API: positionSide depends on account setting (Hedge vs One-way mode).
                # Retry with 'BOTH' or without positionSide if code -4061 (position side mismatch) is encountered.
                try:
                    res = exchange.create_order(
                        symbol=ccxt_symbol,
                        type="STOP_MARKET",
                        side=close_side,
                        amount=None,
                        params={"positionSide": pos_side, "stopPrice": stop_loss, "closePosition": True}
                    )
                except Exception as b_err:
                    err_str = str(b_err)
                    if "-4061" in err_str or "position side" in err_str.lower():
                        logger.info(f"[LiveSniper] Binance -4061 模式不匹配，尝试按单向持仓模式 (positionSide='BOTH') 重新下止损单...")
                        try:
                            res = exchange.create_order(
                                symbol=ccxt_symbol,
                                type="STOP_MARKET",
                                side=close_side,
                                amount=None,
                                params={"positionSide": "BOTH", "stopPrice": stop_loss, "closePosition": True}
                            )
                        except Exception as b_err2:
                            if "-4061" in str(b_err2) or "position side" in str(b_err2).lower():
                                logger.info(f"[LiveSniper] Binance -4061 尝试去除 positionSide 参数重新下单...")
                                res = exchange.create_order(
                                    symbol=ccxt_symbol,
                                    type="STOP_MARKET",
                                    side=close_side,
                                    amount=None,
                                    params={"stopPrice": stop_loss, "closePosition": True}
                                )
                            else:
                                raise b_err2
                    else:
                        raise b_err
            else:
                # Other exchanges: validate amount against market minimum
                try:
                    amount = float(exchange.amount_to_precision(ccxt_symbol, amount))
                except Exception:
                    pass
                market = exchange.market(ccxt_symbol)
                min_amount = (market.get("limits") or {}).get("amount", {}).get("min", 0)
                if amount < (min_amount or 0):
                    logger.warning(f"[LiveSniper] ⚠️ {symbol} 保护止损量 {amount} < 最小量 {min_amount}，跳过交易所侧挂单")
                    return None
                res = exchange.create_order(
                    symbol=ccxt_symbol,
                    type="market",
                    side=close_side,
                    amount=amount,
                    params={"triggerPrice": stop_loss, "reduceOnly": True}
                )
            order_id = str(res.get("id"))
            logger.info(f"[LiveSniper] 🛡️ 交易所侧止损保护单已挂设: {ccxt_symbol} trigger=${stop_loss} (#{order_id})")
            return order_id
        except Exception as e:
            logger.error(f"[LiveSniper] ❌ 交易所侧止损保护单挂设失败 ({symbol}): {e}")
            self._send_notification(
                f"⚠️ 实盘止损保护单挂设失败：{symbol}",
                f"⚠️ *【高危提醒】*\n{ex_id.upper()} 交易所侧止损单挂设失败：{e}\n当前 {symbol} 仓位仅依赖本机运行的双保险——若 App 关闭/断网/休眠将完全无保护！\n请立即手动在交易所设置止损：${stop_loss}"
            )
            return None

    def _update_trailing_stop_loss(self, t, current_pnl_pct, actual_entry, sig_type, lev, amount):
        """
        Anti-Suffocation Tiered Trailing Stop (防窒息分段阶梯锁利算法):

        - Peak PnL < 25%: Keep initial technical stop (100% breathing room for early trend incubation).
        - 25% <= Peak PnL < 40%: Move stop loss to actual_entry (breakeven protection, zero-risk trade).
        - 40% <= Peak PnL < 100%: Trailing stop activates, locking 50% of peak gains.
        - Peak PnL >= 100%: High-yield protection, locking 70% of peak gains (max 30% profit giveback).
        - Safety Buffer: Minimum 0.6% price distance from mark price to avoid noise-driven / spread stops.
        """
        ACTIVATION_THRESHOLD = 25.0   # minimum PnL% to move stop loss to breakeven
        TRAILING_THRESHOLD   = 40.0   # minimum PnL% to activate ratio-based trailing stop
        MIN_PEAK_STEP       = 4.0     # min peak advance (%) before updating SL
        MIN_PRICE_SAFETY_BUFFER = 0.006  # 0.6% minimum price safety distance buffer

        cfg = self.state.get("config", {})
        max_drawdown_pct = float(cfg.get("max_profit_drawdown_percent", 30.0) or 30.0)

        # Update the stored peak PnL if we have a new high
        old_peak = t.get("peak_pnl_pct", 0.0)
        if current_pnl_pct > old_peak:
            t["peak_pnl_pct"] = current_pnl_pct

        peak = t.get("peak_pnl_pct", 0.0)

        # 1. Peak PnL < 25%: Keep initial technical stop (give trade 100% breathing room to develop)
        if peak < ACTIVATION_THRESHOLD:
            return False

        # 2. 25% <= Peak PnL < 40%: Move stop loss to breakeven (actual_entry) with zero-risk protection
        if peak < TRAILING_THRESHOLD:
            breakeven_sl = round(actual_entry, 6)
            current_sl = t.get("stop_loss")
            if current_sl is not None and current_sl != "-":
                try:
                    current_sl_f = float(current_sl)
                    if sig_type == "long" and breakeven_sl <= current_sl_f:
                        return False
                    if sig_type == "short" and breakeven_sl >= current_sl_f:
                        return False
                except (TypeError, ValueError):
                    pass

            old_sl = t.get("stop_loss", "-")
            t["stop_loss"] = breakeven_sl
            t["trailing_sl_level"] = peak
            t["locked_pnl_percent"] = 0.0

            logger.info(f"[TrailingStop] 🛡️ {t['symbol']} 保本防守生效: 浮盈={round(peak, 1)}% → 止损 ${old_sl} → ${breakeven_sl} (保本价)")
            self._update_live_sl_order_on_exchange(t, sig_type, amount, breakeven_sl)
            return True

        # 3. Peak PnL >= 40%: Ratio-based trailing stop
        if peak >= 100.0:
            LOCK_RATIO = max(0.65, 1.0 - (max_drawdown_pct / 100.0))
        else:
            LOCK_RATIO = 0.50

        # Only update SL when the peak has advanced meaningfully since last update
        last_peak_at_update = t.get("trailing_sl_level", 0.0)
        if peak - last_peak_at_update < MIN_PEAK_STEP:
            return False

        # New SL locks in calculated ratio of peak gains
        lock_in_pct = peak * LOCK_RATIO
        price_move_ratio = lock_in_pct / 100.0 / lev
        if sig_type == "long":
            raw_sl = actual_entry * (1 + price_move_ratio)
        else:
            raw_sl = actual_entry * (1 - price_move_ratio)

        # Apply Safety Buffer: Ensure SL maintains at least 0.6% price distance from current mark_price
        mark_price = t.get("mark_price", 0.0) or t.get("current_price", 0.0) or actual_entry
        if mark_price > 0:
            if sig_type == "long":
                max_allowed_sl = mark_price * (1.0 - MIN_PRICE_SAFETY_BUFFER)
                if raw_sl > max_allowed_sl:
                    raw_sl = max_allowed_sl
            else:
                min_allowed_sl = mark_price * (1.0 + MIN_PRICE_SAFETY_BUFFER)
                if raw_sl < min_allowed_sl:
                    raw_sl = min_allowed_sl

        new_sl = round(raw_sl, 6)

        # Only move SL in the profitable direction (never worsen it)
        current_sl = t.get("stop_loss")
        if current_sl is not None and current_sl != "-":
            try:
                current_sl_f = float(current_sl)
                if sig_type == "long" and new_sl <= current_sl_f:
                    return False
                if sig_type == "short" and new_sl >= current_sl_f:
                    return False
            except (TypeError, ValueError):
                pass

        old_sl = t.get("stop_loss", "-")
        t["stop_loss"] = new_sl
        t["trailing_sl_level"] = peak   # record peak at the time of this update
        t["locked_pnl_percent"] = round(lock_in_pct, 1)

        logger.info(
            f"[TrailingStop] 🔒 {t['symbol']} 动态阶梯止损更新: "
            f"峰值={round(peak, 1)}% → 锁定 {round(lock_in_pct, 1)}% → "
            f"止损 ${old_sl} → ${new_sl}  (杠杆 {lev}x, 保留物理安全距离)"
        )

        self._update_live_sl_order_on_exchange(t, sig_type, amount, new_sl)

        self._send_notification(
            f"🔒 动态追踪止损激活：{t['symbol']}",
            f"🔒 *【动态止损上移通知】*\n"
            f"币种：{t['symbol']} ({sig_type.upper()})\n"
            f"历史峰值浮盈：{round(peak, 1)}%\n"
            f"已锁定收益：{round(lock_in_pct, 1)}%\n"
            f"止损已从 ${old_sl} 上移至 ${new_sl}"
        )
        return True

    def _update_live_sl_order_on_exchange(self, t, sig_type, amount, new_sl):
        """Helper to update exchange-side protective SL order cleanly."""
        if t.get("is_live") and amount > 0:
            try:
                exchange, ex_id = self._init_live_ccxt()
                symbol = t["symbol"]
                ccxt_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol

                # 1. Cancel all existing trigger/stop orders on the exchange for this symbol
                try:
                    open_orders = exchange.fetch_open_orders(ccxt_symbol)
                    for order in open_orders:
                        o_id = order.get("id")
                        o_type = str(order.get("type", "")).upper()
                        if "STOP" in o_type or order.get("info", {}).get("reduceOnly") == "true" or order.get("info", {}).get("reduceOnly") is True:
                            try:
                                exchange.cancel_order(o_id, ccxt_symbol)
                                logger.info(f"[TrailingStop] 成功清理交易所侧 {symbol} 的历史触发止损单 #{o_id}")
                            except Exception as co_e:
                                logger.warning(f"[TrailingStop] 清理历史触发单 #{o_id} 失败: {co_e}")
                except Exception as fetch_e:
                    logger.warning(f"[TrailingStop] 获取挂单清理失败: {fetch_e}")

                # 2. Cancel old protective SL explicitly if recorded
                old_order_id = t.get("protective_sl_order_id")
                if old_order_id:
                    try:
                        exchange.cancel_order(old_order_id, ccxt_symbol)
                        logger.info(f"[TrailingStop] 已撤销记录止损单 #{old_order_id}")
                    except Exception:
                        pass
                    t["protective_sl_order_id"] = None

                # 3. Place new protective SL order
                new_order_id = self._place_live_protective_sl(exchange, ex_id, symbol, sig_type, amount, new_sl)
                if new_order_id:
                    t["protective_sl_order_id"] = new_order_id

            except Exception as e:
                logger.warning(f"[TrailingStop] 更新实盘止损单失败 ({t['symbol']}): {e}")

    def _cancel_protective_sl(self, trade):
        """Cancel the exchange-side protective stop once the position is closed locally."""
        if not trade.get("is_live"):
            return
        try:
            exchange, ex_id = self._init_live_ccxt()
            symbol = trade["symbol"]
            ccxt_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
            
            # 1. Fetch and cancel all active trigger/stop orders on the exchange for this symbol
            try:
                open_orders = exchange.fetch_open_orders(ccxt_symbol)
                for order in open_orders:
                    o_id = order.get("id")
                    o_type = str(order.get("type", "")).upper()
                    # Cancel if it is a stop/trigger order (e.g. STOP_MARKET, STOP, etc.) or reduceOnly
                    if "STOP" in o_type or order.get("info", {}).get("reduceOnly") == "true" or order.get("info", {}).get("reduceOnly") is True:
                        try:
                            exchange.cancel_order(o_id, ccxt_symbol)
                            logger.info(f"[LiveSniper] 成功清理交易所侧 {symbol} 的遗留触发单 #{o_id}")
                        except Exception as co_e:
                            logger.warning(f"[LiveSniper] 清理遗留触发单 #{o_id} 失败: {co_e}")
            except Exception as fetch_e:
                logger.warning(f"[LiveSniper] 获取交易所挂单列表以清理遗留触发单失败: {fetch_e}")

            # 2. Also try to cancel the stored order ID explicitly if it is still open
            order_id = trade.get("protective_sl_order_id")
            if order_id:
                try:
                    exchange.cancel_order(order_id, ccxt_symbol)
                    logger.info(f"[LiveSniper] 保护性止损单 #{order_id} 已随本地平仓撤销 ({symbol})")
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"[LiveSniper] 撤销保护性止损处理出现异常 ({trade.get('symbol')}): {e}")
        finally:
            trade["protective_sl_order_id"] = None

    def _cancel_all_conditional_orders_for_symbol(self, symbol):
        """
        Cancel ALL conditional/trigger/stop orders on the exchange for a given symbol.
        Called whenever a position exits (manual close, sync-based closure, etc.)
        to prevent orphaned conditional orders from interfering with future positions.
        """
        try:
            exchange, ex_id = self._init_live_ccxt()
            ccxt_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
            open_orders = exchange.fetch_open_orders(ccxt_symbol)
            cancelled_count = 0
            for order in open_orders:
                o_id = order.get("id")
                o_type = str(order.get("type", "")).upper()
                o_info = order.get("info", {})
                o_status = str(o_info.get("status", "")).upper()
                # Cancel any conditional order: STOP_MARKET, TAKE_PROFIT_MARKET,
                # TRAILING_STOP_MARKET, or any order with reduceOnly / trigger price
                is_conditional = (
                    "STOP" in o_type
                    or "TAKE_PROFIT" in o_type
                    or "TRAILING" in o_type
                    or o_info.get("reduceOnly") in ("true", True, "TRUE")
                    or o_info.get("stopPrice") not in (None, "", "0")
                    or o_info.get("triggerPrice") not in (None, "", "0")
                    or "STOP" in o_status
                    or "PENDING" in o_status
                )
                if is_conditional:
                    try:
                        exchange.cancel_order(o_id, ccxt_symbol)
                        cancelled_count += 1
                        logger.info(f"[SniperEngine] 清理 {symbol} 遗留条件委托 #{o_id} (type={o_type})")
                    except Exception as co_e:
                        logger.warning(f"[SniperEngine] 撤销 {symbol} 条件委托 #{o_id} 失败: {co_e}")
            if cancelled_count > 0:
                logger.info(f"[SniperEngine] ✅ {symbol} 已清理 {cancelled_count} 个遗留条件委托")
            return cancelled_count
        except Exception as e:
            logger.warning(f"[SniperEngine] 清理 {symbol} 条件委托时异常: {e}")
            return 0

    def _place_protective_sl_on_exchange(self, symbol, sig_type, sl_price, amount):
        """
        Place a standalone protective stop-loss order on the exchange for the
        remaining position after a TP1 partial close. This restores exchange-side
        protection that was removed by _cancel_all_conditional_orders_for_symbol
        inside _try_live_close.
        """
        try:
            exchange, ex_id = self._init_live_ccxt()
            ccxt_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
            close_side = "sell" if sig_type == "long" else "buy"
            pos_side = "LONG" if sig_type == "long" else "SHORT"

            if ex_id == "binance":
                # closePosition=True closes the full (remaining) position on trigger
                res = exchange.create_order(
                    symbol=ccxt_symbol,
                    type="STOP_MARKET",
                    side=close_side,
                    amount=None,
                    params={"positionSide": pos_side, "stopPrice": sl_price, "closePosition": True}
                )
            else:
                try:
                    amount = float(exchange.amount_to_precision(ccxt_symbol, amount))
                except Exception:
                    pass
                market = exchange.market(ccxt_symbol)
                min_amount = (market.get("limits") or {}).get("amount", {}).get("min", 0)
                if amount < (min_amount or 0):
                    logger.warning(f"[SniperEngine] ⚠️ {symbol} TP1保护止损量 {amount} < 最小量 {min_amount}，跳过")
                    return False
                params = {"triggerPrice": str(sl_price), "reduceOnly": True}
                if ex_id == "okx":
                    params["positionSide"] = pos_side.lower()
                elif ex_id == "bybit":
                    params["positionSide"] = pos_side
                res = exchange.create_order(
                    symbol=ccxt_symbol,
                    type="market",
                    side=close_side,
                    amount=amount,
                    params=params
                )

            logger.info(f"[SniperEngine] ✅ {symbol} TP1后重挂保护性止损成功: {close_side} @ stop={sl_price} (order={res.get('id')})")
            return True
        except Exception as e:
            logger.warning(f"[SniperEngine] ⚠️ {symbol} TP1后重挂保护性止损失败: {e} — 本地引擎仍会监控止损线")
            return False

    def _try_live_close(self, trade, symbol, side, amount, reason, alert_tag, current_price=None):
        """
        Attempt a live market close with failure safety.

        Returns True if the position may be marked closed locally (paper mode
        or live close succeeded). Returns False when the exchange close FAILED:
        the trade keeps its current status (engine keeps tracking and retries
        on the next tick) and one urgent push per trade+leg is sent.

        The previous code ignored the close result and marked positions closed
        even when the exchange order failed — leaving a real, unmanaged naked
        position on the exchange.
        """
        if not trade.get("is_live"):
            return True
        close_id = self._execute_live_market_close(symbol, side, amount, reason=reason)
        if close_id is not None:
            trade.pop(f"live_fail_alerted_{alert_tag}", None)
            self._cancel_protective_sl(trade)
            # Comprehensive cleanup: cancel ALL remaining conditional orders for this symbol
            self._cancel_all_conditional_orders_for_symbol(symbol)
            return True

        flag = f"live_fail_alerted_{alert_tag}"
        if not trade.get(flag):
            trade[flag] = True
            self._send_notification(
                f"🚨 实盘平仓失败高危警告：{symbol}",
                f"🚨 *【高危：平仓失败】*\n币种：{symbol} ({side.upper()})\n触发原因：{reason}\n触发价：${current_price}\n交易所平仓指令执行失败！该仓位仍真实持有在交易所中，本地引擎将持续重试平仓。\n*请立即前往交易所手动检查并处理该仓位！*"
            )
        logger.error(f"[LiveSniper] 平仓失败，{symbol} 保持跟踪并将在下一 tick 重试 ({reason})")
        return False

    def _send_notification(self, title, content):
        try:
            from notifier import Notifier
            from app import load_yaml_config
            yaml_cfg = load_yaml_config()
            notify_cfg = yaml_cfg.get("notifications", {})
            if not notify_cfg.get("enabled", False):
                return
            if not notify_cfg.get("notify_on_trade", True):
                return
            notifier = Notifier(yaml_cfg)
            notifier.send_notification(title, content)
        except Exception as e:
            logger.warning(f"[SniperEngine] Failed to dispatch push notification: {e}")

    def get_config(self):
        cfg = self.state.get("config", {})
        if cfg.get("mode") == "live":
            cfg["account_balance"] = cfg.get("live_account_balance", 0.0)
        else:
            cfg["account_balance"] = cfg.get("paper_account_balance", 10000.0)
        return cfg

    def update_config(self, new_cfg):
        with self._lock:
            # Reset exchange client cache, balance cache, and positions cache when config changes
            self._exchange_instance = None
            self._live_positions_cache = None
            self._live_balance_cache = None
            
            cfg = self.state.get("config", {})
            for k, v in new_cfg.items():
                if k in cfg:
                    cfg[k] = v
            self.state["config"] = cfg
            self._save_state()
            return self.get_config()

    def get_dashboard_data(self):
        """Thread-safe entry point."""
        with self._lock:
            return self._get_dashboard_data_impl()

    def _get_dashboard_data_impl(self):
        cfg = self.state.get("config", {})
        mode = cfg.get("mode", "paper")
        trades = self.state.get("trades", [])

        if mode == "live":
            filtered_trades = [t for t in trades if t.get("is_live") is True]
            account_bal = round(cfg.get("live_account_balance", 0.0), 2)
            if cfg.get("live_api_key") and cfg.get("live_secret"):
                # Use cached balance (60s TTL) to avoid hammering the exchange
                _now = time.time()
                if self._live_balance_cache is not None and (_now - self._live_balance_cache_time) < 60.0:
                    account_bal = self._live_balance_cache
                else:
                    try:
                        exchange, ex_id = self._init_live_ccxt()
                        bal = exchange.fetch_balance()
                        live_usdt = float(bal.get("total", {}).get("USDT", 0.0))
                        account_bal = round(live_usdt, 2)
                        self._live_balance_cache = account_bal
                        self._live_balance_cache_time = _now
                        cfg["live_account_balance"] = account_bal
                        self._save_state()
                    except Exception as e:
                        logger.warning(f"[SniperEngine] Live balance auto-sync warning: {e}")
        else:
            filtered_trades = [t for t in trades if t.get("is_live") is not True]
            account_bal = round(cfg.get("paper_account_balance", 10000.0), 2)

        closed_trades = [t for t in filtered_trades if t["status"] in ["closed_tp", "closed_sl"]]
        winning_trades = [t for t in closed_trades if t.get("pnl_usd", 0) > 0]
        losing_trades = [t for t in closed_trades if t.get("pnl_usd", 0) <= 0]
        strictly_losing_trades = [t for t in closed_trades if t.get("pnl_usd", 0) < 0]
        breakeven_trades = [t for t in closed_trades if t.get("pnl_usd", 0) == 0]

        win_count = len(winning_trades)
        total_closed = len(closed_trades)
        win_rate = round((win_count / total_closed * 100.0), 1) if total_closed > 0 else 0.0

        total_pnl = sum(t.get("pnl_usd", 0) for t in closed_trades)
        win_dollars = sum(t.get("pnl_usd", 0) for t in winning_trades)
        loss_dollars = abs(sum(t.get("pnl_usd", 0) for t in strictly_losing_trades))

        profit_factor = round(win_dollars / loss_dollars, 2) if loss_dollars > 0 else (round(win_dollars, 2) if win_dollars > 0 else 1.0)

        active_trades = [t for t in filtered_trades if t["status"] in ["pending", "filled", "tp1_hit"]]

        # --- Equity curve, max drawdown & cost transparency --------------
        # Implied starting equity = current balance - realized net PnL.
        # Mode-agnostic (works for paper resets and synced live balances).
        closed_sorted = sorted(closed_trades, key=lambda x: x.get("closed_at") or "")
        equity = account_bal - total_pnl
        peak = equity
        max_dd_usd = 0.0
        max_dd_pct = 0.0
        equity_curve = []
        for ct in closed_sorted:
            equity += ct.get("pnl_usd", 0.0)
            peak = max(peak, equity)
            dd = peak - equity
            max_dd_usd = max(max_dd_usd, dd)
            if peak > 0:
                max_dd_pct = max(max_dd_pct, dd / peak * 100.0)
            equity_curve.append({"t": ct.get("closed_at"), "equity": round(equity, 2)})

        total_fees = round(sum(t.get("fees_usd", 0.0) for t in filtered_trades), 2)

        # Circuit breaker status for the dashboard (per mode)
        daily = self.state.get("daily") or {}
        today = datetime.now().strftime("%Y-%m-%d")
        cb_halted = bool(daily.get(f"halted_{mode}")) and daily.get("date") == today
        day_start_bal = float(daily.get(f"start_balance_{mode}") or account_bal) if daily.get("date") == today else account_bal
        day_realized_pnl = round(account_bal - day_start_bal, 2)

        dashboard_cfg = {**cfg, "account_balance": account_bal}

        return {
            "mode": mode,
            "account_balance": account_bal,
            "initial_balance": round(cfg.get("initial_balance", 10000.0), 2),
            "net_profit_usd": round(total_pnl, 2),
            "win_rate": win_rate,
            "total_trades_count": total_closed,
            "winning_trades_count": win_count,
            "losing_trades_count": len(losing_trades),
            "strictly_losing_trades_count": len(strictly_losing_trades),
            "breakeven_trades_count": len(breakeven_trades),
            "profit_factor": profit_factor,
            "active_positions_count": len(active_trades),
            "max_drawdown_usd": round(max_dd_usd, 2),
            "max_drawdown_percent": round(max_dd_pct, 2),
            "total_fees_usd": total_fees,
            "equity_curve": equity_curve[-50:],
            "circuit_breaker": {
                "enabled": bool(cfg.get("circuit_breaker_enabled", True)),
                "halted": cb_halted,
                "daily_max_loss_percent": cfg.get("daily_max_loss_percent", 6.0),
                "day_realized_pnl": day_realized_pnl,
            },
            "config": dashboard_cfg
        }

    def _fetch_live_positions(self):
        """Fetch active positions from the exchange API and normalize them with caching.
        
        Uses fapiPrivateV2GetPositionRisk for STATIC configured leverage (not effective),
        and calculates allocated margin = entry * size / leverage (stable, matches exchange UI).
        """
        now = time.time()
        # Return cache if it is fresh (60s TTL)
        if self._live_positions_cache is not None and (now - self._live_positions_cache_time) < 60.0:
            return self._live_positions_cache
            
        try:
            exchange, ex_id = self._init_live_ccxt()
            
            # ── Step 1: Get STATIC leverage from V2 PositionRisk endpoint ──
            # fetch_positions() (V3) does NOT return leverage in info dict.
            # V2 returns the configured leverage (e.g. "47") which never changes.
            v2_leverage_map = {}  # symbol -> configured leverage
            try:
                raw_v2 = exchange.fapiPrivateV2GetPositionRisk()
                for rv in raw_v2:
                    amt = float(rv.get("positionAmt", 0) or 0)
                    if amt != 0:
                        sym = rv.get("symbol", "")  # e.g. "ETHUSDT"
                        lev = int(float(rv.get("leverage", 0) or 0))
                        if sym and lev > 0:
                            v2_leverage_map[sym] = lev
                logger.info(f"[PositionSync] V2 leverage map: {v2_leverage_map}")
            except Exception as v2e:
                logger.warning(f"[PositionSync] V2 PositionRisk failed (fallback to V3): {v2e}")
            
            # ── Step 2: Get full position data from fetch_positions ──
            if hasattr(exchange, 'fetch_positions'):
                raw_positions = exchange.fetch_positions()
                open_pos = []
                for p in raw_positions:
                    contracts = float(p.get("contracts", 0.0) or 0.0)
                    notional = abs(float(p.get("notional", 0.0) or 0.0))
                    entry_price = float(p.get("entryPrice", 0.0) or 0.0)
                    
                    if contracts > 0 or notional > 0 or entry_price > 0:
                        symbol = p.get("symbol", "")
                        if not symbol:
                            continue
                        clean_sym = symbol.split(":")[0] if ":" in symbol else symbol
                        # Raw Binance symbol for V2 map lookup (e.g. "ETHUSDT")
                        raw_binance_sym = symbol.replace("/", "").replace(":USDT", "")
                        
                        side_str = str(p.get("side", "long")).lower()
                        normalized_side = "long" if ("buy" in side_str or "long" in side_str) else "short"
                        
                        mark_price = float(p.get("markPrice", 0.0) or 0.0)
                        unrealized_pnl = float(p.get("unrealizedPnl", 0.0) or 0.0)
                        info = p.get("info", {}) if isinstance(p.get("info"), dict) else {}
                        
                        # ── Leverage: prefer V2 configured value (STATIC) ──
                        leverage = v2_leverage_map.get(raw_binance_sym, 0)
                        if leverage == 0:
                            leverage = int(float(info.get("leverage", 0) or 0))
                        if leverage == 0:
                            leverage = int(float(p.get("leverage", 0) or 0))
                        
                        # ── Margin: use isolatedWallet (STABLE, matches exchange UI) ──
                        # isolatedWallet = actual allocated margin (doesn't change with PnL)
                        # isolatedMargin = isolatedWallet + unrealizedPnl (dynamic, NOT what UI shows)
                        margin = float(info.get("isolatedWallet", 0.0) or 0.0)
                        if margin == 0.0:
                            # Fallback: calculate from entry * size / leverage
                            position_amt = abs(float(info.get("positionAmt", 0.0) or 0.0))
                            if position_amt == 0.0:
                                position_amt = contracts
                            if leverage > 0 and entry_price > 0 and position_amt > 0:
                                margin = (entry_price * position_amt) / leverage
                        if margin == 0.0:
                            margin = float(info.get("positionInitialMargin", 0.0) or 0.0)
                        if margin == 0.0 and leverage > 0 and notional > 0:
                            margin = notional / leverage
                        
                        # Fallback: derive leverage if still 0
                        if leverage == 0 and margin > 0 and notional > 0:
                            leverage = max(1, round(notional / margin))
                        
                        logger.info(
                            f"[PositionSync] {clean_sym} {normalized_side}: "
                            f"lev={leverage}x | margin=${margin:.4f} | "
                            f"entry=${entry_price} | mark=${mark_price} | "
                            f"notional=${notional:.2f} | pnl=${unrealized_pnl:.4f}"
                        )
                        
                        pct = float(p.get("percentage", 0.0) or 0.0)
                        if pct == 0.0 and entry_price > 0 and leverage > 0:
                            if normalized_side == "long":
                                pct = (mark_price - entry_price) / entry_price * leverage * 100.0
                            else:
                                pct = (entry_price - mark_price) / entry_price * leverage * 100.0
                        
                        open_pos.append({
                            "symbol": clean_sym,
                            "raw_symbol": symbol,
                            "side": normalized_side,
                            "size": contracts if contracts > 0 else (notional / entry_price if entry_price > 0 else 0.0),
                            "notional": notional,
                            "entry_price": entry_price,
                            "mark_price": mark_price,
                            "leverage": leverage,
                            "margin": margin,
                            "unrealized_pnl": unrealized_pnl,
                            "unrealized_pnl_percent": pct
                        })
                self._live_positions_cache = open_pos
                self._live_positions_cache_time = now
                self._last_positions_error = None
                return open_pos
        except Exception as e:
            self._last_positions_error = str(e)
            logger.warning(f"[SniperEngine] Failed to fetch live positions: {e}")
            if self._live_positions_cache is not None:
                logger.info("[SniperEngine] Returning stale cached positions due to fetch exception.")
                return self._live_positions_cache
        return None

    def get_trades(self, mode_filter=None):
        """Thread-safe entry point."""
        with self._lock:
            return self._get_trades_impl(mode_filter)

    def _get_trades_impl(self, mode_filter=None):
        cfg = self.state.get("config", {})
        target_mode = mode_filter or cfg.get("mode", "paper")
        trades = self.state.get("trades", [])

        if target_mode == "live":
            filtered = [t for t in trades if t.get("is_live") is True]
            
            # Synchronize active positions with the real exchange
            if cfg.get("live_api_key") and cfg.get("live_secret"):
                real_positions = self._fetch_live_positions()
                
                # If API call failed (e.g. rate limit), do not close active trades!
                if real_positions is not None:
                    # Create a map for fast lookup by (symbol, side)
                    real_pos_map = {(pos["symbol"], pos["side"]): pos for pos in real_positions}
                    
                    updated = False
                    synced_trades = []
                    
                    for t in filtered:
                        if t["status"] in ["filled", "tp1_hit", "closed_tp", "closed_sl"]:
                            symbol = t["symbol"]
                            side = t["signal_type"].lower()
                            key = (symbol, side)
                            if key in real_pos_map:
                                # Self-healing: if trade was mistakenly closed in DB but still active on exchange, restore it!
                                if t["status"] in ["closed_tp", "closed_sl"]:
                                    logger.info(f"[SniperEngine] Self-healing: Position {symbol} ({side}) is still active on exchange. Restoring status to filled.")
                                    t["status"] = "filled"
                                    t["closed_at"] = None
                                    t["close_reason"] = ""
                                    updated = True
                                
                                # Update system active trade stats with real exchange stats
                                real_pos = real_pos_map[key]
                                t["current_price"] = real_pos["mark_price"]
                                t["pnl_usd"] = real_pos["unrealized_pnl"]
                                t["pnl_percent"] = real_pos["unrealized_pnl_percent"]
                                t["unrealized_pnl_usd"] = real_pos["unrealized_pnl"]
                                t["unrealized_pnl_percent"] = real_pos["unrealized_pnl_percent"]
                                t["exchange_pnl_percent"] = real_pos["unrealized_pnl_percent"]

                                # Unconditionally sync core params from exchange
                                ex_lev = int(real_pos.get("leverage", 0) or 0)
                                if ex_lev > 0:
                                    t["leverage"] = ex_lev
                                ex_margin = float(real_pos.get("margin", 0.0) or 0.0)
                                if ex_margin > 0:
                                    t["margin_usd"] = round(ex_margin, 4)
                                ex_notional = float(real_pos.get("notional", 0.0) or 0.0)
                                if ex_notional > 0:
                                    t["position_size_usd"] = round(ex_notional, 2)
                                ex_entry = float(real_pos.get("entry_price", 0.0) or 0.0)
                                if ex_entry > 0:
                                    old_entry = t.get("actual_entry")
                                    if old_entry is None or abs(float(old_entry) - ex_entry) / ex_entry > 0.002:
                                        t["peak_pnl_pct"] = 0.0
                                        t["trailing_sl_level"] = 0.0
                                        t["locked_pnl_percent"] = 0.0
                                    t["actual_entry"] = ex_entry
                                updated = True
                                
                                # Remove from real_pos_map so it's not marked as external
                                real_pos_map.pop(key)
                            else:
                                # If active in system but no longer on exchange -> Close it
                                if t["status"] in ["filled", "tp1_hit"]:
                                    logger.info(f"[SniperEngine] Syncing external closure for {symbol} ({side}).")
                                    t["status"] = "closed_tp" if t.get("pnl_usd", 0.0) >= 0 else "closed_sl"
                                    t["closed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                    t["close_reason"] = "🗑️ 交易所侧仓位已平仓，系统自动同步平仓状态"
                                    t["protective_sl_order_id"] = None
                                    updated = True
                                    # Clean up orphaned conditional orders on the exchange
                                    if t.get("is_live"):
                                        self._cancel_all_conditional_orders_for_symbol(symbol)
                        synced_trades.append(t)
                    
                    # Add remaining positions from map as mock external trades
                    # Skip any symbols that were recently manually closed (tombstone)
                    _now = time.time()
                    self._closed_external_symbols = {s: exp for s, exp in self._closed_external_symbols.items() if exp > _now}
                    
                    external_trades = []
                    for (symbol, side), pos in real_pos_map.items():
                        # Skip if this symbol was manually closed and tombstone hasn't expired
                        if symbol in self._closed_external_symbols:
                            logger.info(f"[SniperEngine] Skipping tombstoned symbol {symbol} from external positions.")
                            continue
                        mock_t = {
                            "id": f"external-{pos['symbol']}-{pos['side']}",
                            "symbol": pos["symbol"],
                            "signal_type": pos["side"],
                            "status": "filled",
                            "confidence_score": "-",
                            "entry_min": "-",
                            "entry_max": "-",
                            "planned_entry": "-",
                            "actual_entry": pos["entry_price"],
                            "stop_loss": "-",
                            "take_profit_targets": [],
                            "leverage": pos["leverage"],
                            "position_size_usd": round(pos["notional"], 2),
                            "margin_usd": round(pos["margin"], 2),
                            "entered_at": "-",
                            "closed_at": None,
                            "pnl_usd": round(pos["unrealized_pnl"], 2),
                            "pnl_percent": round(pos["unrealized_pnl_percent"], 2),
                            "unrealized_pnl_usd": round(pos["unrealized_pnl"], 2),
                            "unrealized_pnl_percent": round(pos["unrealized_pnl_percent"], 2),
                            "is_live": True,
                            "is_external": True,
                            "current_price": pos["mark_price"]
                        }
                        external_trades.append(mock_t)
                    
                    if updated:
                        self._save_state()
                    
                    all_returned_trades = synced_trades + external_trades
                    return sorted(all_returned_trades, key=lambda x: x.get("entered_at", "") or "", reverse=True)
        else:
            filtered = [t for t in trades if t.get("is_live") is not True]
            
        return sorted(filtered, key=lambda x: x.get("entered_at", "") or "", reverse=True)

    def calculate_trade_params(self, balance, risk_pct, entry_price, stop_loss, confidence, max_lev=70):
        cfg = self.state.get("config", {})
        lev_mode = cfg.get("leverage_mode", "smart")
        
        if lev_mode == "fixed":
            suggested_lev = int(cfg.get("fixed_leverage", 50))
        else:
            min_lev = int(cfg.get("min_leverage", 35))
            target_max_lev = int(cfg.get("max_leverage", 70))
            if min_lev > target_max_lev:
                min_lev, target_max_lev = target_max_lev, min_lev
                
            if confidence >= 9:
                suggested_lev = target_max_lev
            elif confidence >= 8:
                suggested_lev = int(min_lev + (target_max_lev - min_lev) * 0.65)
            elif confidence >= 7:
                suggested_lev = int(min_lev + (target_max_lev - min_lev) * 0.35)
            else:
                suggested_lev = min_lev

        # Check margin_mode: "smart", "account_percent", "fixed_amount"
        margin_mode = cfg.get("margin_mode", "smart")

        if margin_mode == "account_percent":
            margin_pct = float(cfg.get("margin_percent", 5.0) or 5.0)
            margin_usd = balance * (margin_pct / 100.0)
            pos_value_usd = margin_usd * suggested_lev
        elif margin_mode == "fixed_amount":
            fixed_amt = float(cfg.get("fixed_margin_amount", 20.0) or 20.0)
            margin_usd = fixed_amt
            pos_value_usd = margin_usd * suggested_lev
        else:
            # "smart" mode: risk-scaled sizing based on stop loss distance
            conf_risk_mult = 0.8 + (min(confidence, 10) - 7) * 0.133  # 7→0.8, 8→0.93, 9→1.07, 10→1.2
            conf_risk_mult = max(0.6, min(1.3, conf_risk_mult))
            risk_amount = balance * (risk_pct / 100.0) * conf_risk_mult

            sl_distance_pct = abs(entry_price - stop_loss) / entry_price if entry_price > 0 else 0.02
            if sl_distance_pct <= 0.001:
                sl_distance_pct = 0.01

            pos_value_usd = risk_amount / sl_distance_pct
            margin_usd = pos_value_usd / suggested_lev

            if margin_usd > balance * 0.25:
                margin_usd = balance * 0.25
                pos_value_usd = margin_usd * suggested_lev

        # 🎯 Exchange Minimum Notional Auto-Protector
        # Ensure position notional value is at least $21.00 to pass Binance 20U / OKX 10U minimum notional filter.
        # For high-priced assets, we scale up dynamically to meet minimum lot size requirements (e.g. 0.001 BTC / 0.01 ETH).
        min_pos_value = 21.0
        if entry_price > 30000.0:
            min_pos_value = max(min_pos_value, 0.0011 * entry_price)
        elif entry_price > 1000.0:
            min_pos_value = max(min_pos_value, 0.011 * entry_price)

        if pos_value_usd < min_pos_value:
            pos_value_usd = min_pos_value
            margin_usd = round(pos_value_usd / suggested_lev, 2)

        return round(pos_value_usd, 2), round(margin_usd, 2), suggested_lev

    # --- Adaptive Risk Engine -------------------------------------------
    def _get_recent_win_rate(self, lookback=10):
        """Calculate win rate from the last N closed trades."""
        trades = self.state.get("trades", [])
        closed = [t for t in trades if t["status"] in ("closed_tp", "closed_sl")]
        closed.sort(key=lambda x: x.get("closed_at", ""), reverse=True)
        recent = closed[:lookback]
        if len(recent) < 3:
            return None  # Not enough data to adapt
        wins = sum(1 for t in recent if t["status"] == "closed_tp")
        return wins / len(recent)

    def _adaptive_risk_adjust(self, base_risk_pct):
        """
        Dynamically adjust risk per trade based on recent performance.
        - Win rate >= 60%: allow full risk (strategy is working)
        - Win rate 40-60%: reduce to 80% of base (mild caution)
        - Win rate < 40%: reduce to 50% of base (strategy may be failing)
        - Win rate < 25%: reduce to 30% of base (severe drawdown protection)
        Also applies cooldown multiplier after consecutive SLs.
        """
        win_rate = self._get_recent_win_rate(lookback=10)
        if win_rate is None:
            adjusted = base_risk_pct
        elif win_rate >= 0.6:
            adjusted = base_risk_pct  # Full risk, strategy working
        elif win_rate >= 0.4:
            adjusted = base_risk_pct * 0.8  # Mild caution
        elif win_rate >= 0.25:
            adjusted = base_risk_pct * 0.5  # Significant reduction
        else:
            adjusted = base_risk_pct * 0.3  # Severe protection

        # Apply cooldown multiplier (consecutive SL penalty)
        cooldown_mult = self._cooldown_multiplier()
        adjusted *= cooldown_mult

        if adjusted != base_risk_pct:
            logger.info(
                f"[AdaptiveRisk] Win rate: {win_rate if win_rate else 'N/A'}, "
                f"cooldown_mult: {cooldown_mult}, "
                f"risk: {base_risk_pct}% → {round(adjusted, 2)}%"
            )
        return round(adjusted, 3)

    def _cooldown_multiplier(self):
        """
        After consecutive stop-losses, temporarily reduce position size.
        - 2 consecutive SL: 0.7x
        - 3 consecutive SL: 0.5x
        - 4+ consecutive SL: 0.3x
        Resets on any TP hit.
        """
        trades = self.state.get("trades", [])
        closed = [t for t in trades if t["status"] in ("closed_tp", "closed_sl")]
        closed.sort(key=lambda x: x.get("closed_at", ""), reverse=True)

        consecutive_sl = 0
        for t in closed:
            if t["status"] == "closed_sl":
                consecutive_sl += 1
            else:
                break  # Reset on any win

        if consecutive_sl >= 4:
            return 0.3
        elif consecutive_sl == 3:
            return 0.5
        elif consecutive_sl == 2:
            return 0.7
        return 1.0

    def close_position_manually(self, trade_id):
        """Thread-safe entry point."""
        with self._lock:
            return self._close_position_manually_impl(trade_id)

    def _close_position_manually_impl(self, trade_id):
        self._live_positions_cache = None
        if str(trade_id).startswith("external-"):
            # This is an external/manual position from the exchange
            # trade_id format: "external-{symbol}-{side}"
            parts = str(trade_id).replace("external-", "").split("-")
            if len(parts) >= 2:
                side = parts[-1].lower()
                symbol = "-".join(parts[:-1])
            else:
                symbol = parts[0]
                side = None

            try:
                # Initialize CCXT exchange client
                exchange, ex_id = self._init_live_ccxt()
                
                # Fetch positions to find exact size and side
                real_positions = self._fetch_live_positions()
                if side:
                    match_pos = next((pos for pos in real_positions if pos["symbol"] == symbol and pos["side"] == side), None)
                else:
                    match_pos = next((pos for pos in real_positions if pos["symbol"] == symbol), None)
                if not match_pos:
                    return {"status": "error", "message": f"未在交易所找到 {symbol} 的真实仓位，可能已被平仓或过期。"}
                
                side = match_pos["side"]
                size = match_pos["size"]
                
                # Execute CCXT market close order
                close_side = "sell" if side == "long" else "buy"
                ccxt_symbol = match_pos["raw_symbol"]
                
                # Send order to exchange
                order = exchange.create_order(
                    symbol=ccxt_symbol,
                    type="market",
                    side=close_side,
                    amount=size,
                    params={"reduceOnly": True}
                )
                logger.info(f"[SniperEngine] Handled manual close of external position: {symbol} size {size} side {side} order_id {order.get('id')}")
                
                # Register tombstone: exclude this symbol from positions list for 90s
                # so it doesn't re-appear while exchange is still processing the close
                self._closed_external_symbols[symbol] = time.time() + 90.0
                self._live_positions_cache = None
                
                # Clean up orphaned conditional orders (stop/TP/trailing) on the exchange
                self._cancel_all_conditional_orders_for_symbol(symbol)
                
                return {"status": "success", "message": f"已成功平仓外部/手动仓位 {symbol}，平仓量为 {size}！"}
            except Exception as close_e:
                logger.error(f"[SniperEngine] Handled manual close of external position failed: {close_e}")
                return {"status": "error", "message": f"平仓外部/手动仓位 {symbol} 失败：{close_e}。请直接前往交易所手动处理。"}

        trades = self.state.get("trades", [])
        trade = next((t for t in trades if t["id"] == trade_id), None)
        if not trade:
            return {"status": "error", "message": f"未找到 ID 为 {trade_id} 的持仓单！"}

        if trade["status"] not in ["filled", "tp1_hit", "pending"]:
            return {"status": "error", "message": f"订单状态为 {trade['status']}，无法执行手动平仓/撤单。"}

        symbol = trade["symbol"]
        sig_type = trade["signal_type"]
        current_price = trade.get("current_price") or trade.get("planned_entry")
        actual_entry = trade.get("actual_entry") or trade.get("planned_entry")
        pos_val = trade.get("position_size_usd", 0.0)
        margin = trade.get("margin_usd", 0.0)
        lev = trade.get("leverage", 10)

        # If trade is pending, cancel it
        if trade["status"] == "pending":
            if trade.get("is_live") and trade.get("live_order_id"):
                try:
                    exchange, ex_id = self._init_live_ccxt()
                    ccxt_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
                    exchange.cancel_order(trade["live_order_id"], ccxt_symbol)
                    # Clean up SL/TP conditional orders attached to the cancelled pending order
                    self._cancel_all_conditional_orders_for_symbol(symbol)
                except Exception as cancel_e:
                    logger.warning(f"[LiveSniper] Cancel live order error: {cancel_e}")
                    return {"status": "error", "message": f"交易所撤单失败：{cancel_e}。该挂单可能仍在交易所生效，请前往交易所核实。"}
            trade["status"] = "cancelled"
            trade["close_reason"] = "✋ 用户手动在界面撤销挂单"
            self._save_state()
            return {"status": "success", "message": f"已成功撤销 {symbol} 的埋伏挂单！"}

        # If trade is filled or tp1_hit, execute market close
        amount = round(pos_val / actual_entry, 4)
        rem_factor = 0.5 if trade.get("tp1_partial_closed") else 1.0

        if not self._try_live_close(trade, symbol, sig_type, round(amount * rem_factor, 4), reason="用户手动在界面点击市价平仓", alert_tag="manual", current_price=current_price):
            return {"status": "error", "message": f"交易所平仓指令执行失败！{symbol} 仓位仍真实持有在交易所中，请立即前往交易所手动处理或稍后重试。"}

        # Manual market close: apply adverse slippage + taker fee on exit
        taker_fee, _, slippage = self._fee_rates()
        if sig_type == "long":
            exec_price = current_price * (1 - slippage)
            float_pct = (exec_price - actual_entry) / actual_entry * lev
        else:
            exec_price = current_price * (1 + slippage)
            float_pct = (actual_entry - exec_price) / actual_entry * lev

        exit_fee = self._record_fee(trade, pos_val * rem_factor, taker_fee)
        final_pnl_usd = round(margin * rem_factor * float_pct - exit_fee, 2)
        total_pnl = round(trade.get("pnl_usd", 0.0) + final_pnl_usd, 2)
        
        trade["status"] = "closed_tp" if total_pnl >= 0 else "closed_sl"
        trade["closed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        trade["pnl_usd"] = total_pnl
        trade["pnl_percent"] = round((total_pnl / margin) * 100.0, 2)
        trade["close_reason"] = "✋ 用户手动在界面平仓离场"

        cfg = self.state.get("config", {})
        if not trade.get("is_live"):
            cfg["paper_account_balance"] += final_pnl_usd

        self._save_state()

        self._send_notification(
            f"✋ 用户手动平仓通知：{symbol}",
            f"✋ *【手动平仓通知】*\n币种：{symbol} ({sig_type.upper()})\n平仓现价：${current_price}\n实现总盈亏：${total_pnl} USD ({trade['pnl_percent']}%)"
        )
        return {"status": "success", "message": f"成功手动平仓 {symbol}！最后出局价格：${current_price}，盈亏：${total_pnl} USD"}

    def reset_paper_data(self, initial_balance=10000.0):
        with self._lock:
            return self._reset_paper_data_impl(initial_balance)

    def reset_live_data(self, initial_balance=10000.0):
        with self._lock:
            return self._reset_live_data_impl(initial_balance)

    def _reset_live_data_impl(self, initial_balance=10000.0):
        try:
            initial_balance = float(initial_balance) if float(initial_balance) > 0 else 10000.0
        except Exception:
            initial_balance = 10000.0

        cfg = self.state.get("config", {})
        cfg["live_account_balance"] = initial_balance

        # Reset daily baseline for live account
        today = datetime.now().strftime("%Y-%m-%d")
        daily = self.state.get("daily") or {}
        daily["date"] = today
        daily["start_balance_live"] = initial_balance
        daily["halted_live"] = False
        daily["notified_live"] = False
        self.state["daily"] = daily

        # Keep paper trades, but remove live trades
        new_trades = [t for t in self.state.get("trades", []) if t.get("is_live") is not True]
        self.state["trades"] = new_trades
        self.state["config"] = cfg
        self._save_state()
        return {"status": "success", "message": f"实盘统计数据已清空！ live 账户基准重置为 ${initial_balance} USD。"}

    def _reset_paper_data_impl(self, initial_balance=10000.0):
        try:
            initial_balance = float(initial_balance) if float(initial_balance) > 0 else 10000.0
        except Exception:
            initial_balance = 10000.0

        cfg = self.state.get("config", {})
        cfg["paper_account_balance"] = initial_balance
        cfg["initial_balance"] = initial_balance
        
        # Reset daily baseline for paper account so Today's PnL is 0.00 USD
        today = datetime.now().strftime("%Y-%m-%d")
        daily = self.state.get("daily") or {}
        daily["date"] = today
        daily["start_balance_paper"] = initial_balance
        daily["halted_paper"] = False
        daily["notified_paper"] = False
        self.state["daily"] = daily

        # Keep live trades, but remove paper trades
        new_trades = [t for t in self.state.get("trades", []) if t.get("is_live") is True]
        self.state["trades"] = new_trades
        self.state["config"] = cfg
        self._save_state()
        return {"status": "success", "message": f"模拟盘数据已成功重置！初始可用资金设定为 ${initial_balance} USD。"}

    def _init_live_ccxt(self):
        cfg = self.state.get("config", {})
        ex_id = cfg.get("live_exchange", "binance").lower()
        api_key = cfg.get("live_api_key", "").strip()
        secret = cfg.get("live_secret", "").strip()
        passphrase = cfg.get("live_passphrase", "").strip()

        if not api_key or not secret:
            raise ValueError(f"未在系统中配置 {ex_id.upper()} 的实盘 API Key 或 Secret！请先前往设置补全。")

        # Compare params to see if key/config changed. If not, reuse the cached instance.
        params_key = (ex_id, api_key, secret, passphrase)
        if getattr(self, '_exchange_instance', None) is not None and getattr(self, '_exchange_instance_params', None) == params_key:
            return self._exchange_instance, ex_id

        import ccxt
        ex_class = getattr(ccxt, ex_id)
        params = {
            "apiKey": api_key,
            "secret": secret,
            "enableRateLimit": True,
            "options": {"defaultType": "swap"}
        }
        if passphrase:
            params["password"] = passphrase

        try:
            from data_fetcher import get_data_fetcher
            df_fetcher = get_data_fetcher(ex_id)
            if getattr(df_fetcher, 'proxies', None):
                params["proxies"] = df_fetcher.proxies
        except Exception:
            pass

        exchange = ex_class(params)
        self._exchange_instance = exchange
        self._exchange_instance_params = params_key
        return exchange, ex_id

    def fetch_live_usdt_balance(self):
        """
        Auto-sync real USDT futures available balance from exchange API
        """
        try:
            exchange, ex_id = self._init_live_ccxt()
            bal = exchange.fetch_balance()
            usdt_info = bal.get("USDT", {})
            free_usdt = float(usdt_info.get("free") or usdt_info.get("total") or 0.0)
            if free_usdt > 0:
                self.state["config"]["live_account_balance"] = round(free_usdt, 2)
                self._save_state()
                return round(free_usdt, 2)
        except Exception as e:
            logger.warning(f"[LiveSniper] Balance sync warning: {e}")
        return self.state.get("config", {}).get("live_account_balance", 10.0)

    def _execute_live_market_close(self, symbol, side, amount, reason=""):
        """
        Dual Insurance: Fallback active market close order to force-close live positions!
        Dual-mode support (One-Way and Hedge Mode).
        """
        try:
            exchange, ex_id = self._init_live_ccxt()
            ccxt_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
            close_side = "sell" if side.lower() in ["long", "buy"] else "buy"
            pos_side = "LONG" if side.lower() in ["long", "buy"] else "SHORT"
            
            try:
                amount = float(exchange.amount_to_precision(ccxt_symbol, amount))
            except Exception:
                pass

            # Safety: if precision truncation killed the amount, fetch real position size
            market = exchange.market(ccxt_symbol)
            min_amount = (market.get("limits") or {}).get("amount", {}).get("min", 0) or 0
            if amount < min_amount:
                try:
                    positions = exchange.fetch_positions([ccxt_symbol])
                    for p in positions:
                        if p.get("symbol") == ccxt_symbol and float(p.get("contracts", 0) or 0) > 0:
                            amount = float(p["contracts"])
                            logger.info(f"[LiveSniper] 使用交易所实际仓位量替代: {amount}")
                            break
                except Exception:
                    pass
                if amount < min_amount:
                    logger.error(f"[LiveSniper] ❌ {symbol} 平仓量 {amount} < 最小量 {min_amount}，无法下单")
                    return None

            logger.info(f"[LiveSniper] 🛡️ [双保险防守触发] 向 {ex_id.upper()} 下发紧急市价平仓单: {ccxt_symbol} {close_side.upper()} amount={amount} ({reason})")
            
            # Attempt 1: Try with Hedge Mode positionSide parameter
            try:
                res = exchange.create_order(
                    symbol=ccxt_symbol,
                    type="market",
                    side=close_side,
                    amount=amount,
                    params={"reduceOnly": True, "positionSide": pos_side}
                )
                return str(res.get("id"))
            except Exception as hedge_e:
                logger.warning(f"[LiveSniper] Hedge mode close attempt failed ({hedge_e}), retrying with One-Way mode params...")
                # Attempt 2: Fallback to standard One-Way mode reduceOnly parameter
                res = exchange.create_order(
                    symbol=ccxt_symbol,
                    type="market",
                    side=close_side,
                    amount=amount,
                    params={"reduceOnly": True}
                )
                return str(res.get("id"))
        except Exception as e:
            logger.error(f"[LiveSniper] ❌ 双保险紧急市价平仓失败 ({symbol}): {e}")
            return None

    def process_new_signal(self, symbol, current_price, json_signal, markdown_report):
        """Thread-safe entry point."""
        with self._lock:
            return self._process_new_signal_impl(symbol, current_price, json_signal, markdown_report)

    def _process_new_signal_impl(self, symbol, current_price, json_signal, markdown_report):
        cfg = self.state.get("config", {})
        mode = cfg.get("mode", "paper")
        if mode == "off":
            return None

        # 🚨 Daily circuit breaker: refuse all new trades while halted
        if self._check_circuit_breaker():
            logger.info(f"[SniperEngine] Circuit breaker active — new {symbol} signal ignored.")
            return None

        # ⏰ Time-of-day liquidity filter: avoid low-liquidity hours
        # UTC 20:00-00:00 = Asian dead zone (thin order books, wider spreads, more slippage)
        from datetime import datetime as _dt, timezone as _tz
        utc_hour = _dt.now(_tz.utc).hour
        low_liquidity_hours = cfg.get("low_liquidity_hours_utc", [20, 21, 22, 23])
        if utc_hour in low_liquidity_hours:
            logger.info(f"[SniperEngine] Low-liquidity hour (UTC {utc_hour}:00) — {symbol} signal deferred.")
            return None

        # 📰 Market context bias check: respect sentiment-driven stand_aside
        market_ctx = json_signal.get("market_context") or {}
        trading_bias = market_ctx.get("trading_bias", "normal")
        if trading_bias == "stand_aside":
            logger.info(f"[SniperEngine] Market context bias = stand_aside — {symbol} signal blocked.")
            return None

        # 🔗 Correlation check: avoid same-direction positions on highly correlated pairs
        CORRELATED_GROUPS = [
            {"BTC/USDT", "ETH/USDT"},           # BTC-ETH high correlation
            {"DOGE/USDT", "HYPE/USDT"},         # Alt-meme correlation
        ]
        sig_type_check = str(json_signal.get("signal_type", "wait")).lower()
        trades = self.state.get("trades", [])
        active_filled = [t for t in trades if t["status"] in ["filled", "tp1_hit"]]
        for group in CORRELATED_GROUPS:
            if symbol in group:
                for t in active_filled:
                    if t["symbol"] in group and t["symbol"] != symbol and t["signal_type"] == sig_type_check:
                        logger.info(
                            f"[SniperEngine] Correlation block: {symbol} {sig_type_check} blocked — "
                            f"correlated {t['symbol']} already has same-direction position."
                        )
                        return None

        sig_type = str(json_signal.get("signal_type", "wait")).lower()
        conf = json_signal.get("confidence_score", 0)
        min_conf = cfg.get("min_confidence", 7)

        if sig_type not in ["long", "short"] or conf < min_conf:
            return None

        trades = self.state.get("trades", [])
        active_trades = [t for t in trades if t["status"] in ["pending", "filled", "tp1_hit"]]
        max_active = cfg.get("max_active_trades", 3)

        # Check existing active trade for this symbol FIRST to update/replace unfilled pending order or handle reversal
        existing_active = [t for t in active_trades if t["symbol"] == symbol]
        if existing_active:
            for old_t in existing_active:
                if old_t["status"] == "pending":
                    if old_t.get("is_live") and old_t.get("live_order_id"):
                        try:
                            exchange, ex_id = self._init_live_ccxt()
                            ccxt_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
                            exchange.cancel_order(old_t["live_order_id"], ccxt_symbol)
                            logger.info(f"[LiveSniper] 🔄 成功撤销旧的未成交 {ex_id.upper()} 挂单 #{old_t['live_order_id']} ({symbol})")
                            # Clean up SL/TP conditional orders attached to the cancelled pending order
                            self._cancel_all_conditional_orders_for_symbol(symbol)
                        except Exception as cancel_e:
                            logger.warning(f"[LiveSniper] ⚠️ 撤销旧挂单失败: {cancel_e} — 保留原挂单继续跟踪，跳过本次替换")
                            return None

                    old_t["status"] = "cancelled"
                    old_t["close_reason"] = f"🔄 大模型更新 {sig_type.upper()} 点位策略，原未成交挂单已自动撤单重置"
                    logger.info(f"[SniperEngine] Cancelled old pending trade for {symbol} to replace with new {sig_type.upper()} signal.")
                else:
                    # Position is active (filled or tp1_hit)
                    old_sig = old_t["signal_type"]
                    if old_sig == sig_type:
                        logger.info(f"[SniperEngine] Symbol {symbol} already has a FILLED position in SAME direction ({sig_type}). Keeping active position, skipping duplicate signal.")
                        return None
                    else:
                        # 🔄 REVERSAL DETECTED! (e.g. Existing SHORT vs New High-Confidence LONG)
                        close_px = float(current_price) if (current_price and float(current_price) > 0) else float(old_t.get("current_price", 0.0))
                        if close_px <= 0:
                            close_px = float(old_t.get("actual_entry", 0.0))

                        # Live market close via CCXT if live trade
                        if old_t.get("is_live"):
                            rem_amount = round(old_t["position_size_usd"] * (0.5 if old_t.get("tp1_partial_closed") else 1.0) / old_t.get("actual_entry", 1.0), 4)
                            if not self._try_live_close(old_t, symbol, old_sig, rem_amount, reason=f"🔄 触发高置信度 ({conf}/10分) 反向 {sig_type.upper()} 信号，市价平仓翻向", alert_tag="reversal", current_price=close_px):
                                logger.warning(f"[SniperEngine] [{symbol}] Reversal close FAILED on exchange — aborting reversal, keeping old {old_sig.upper()} position")
                                return None

                        # Calculate final net PnL for old_t
                        lev = old_t.get("leverage", 1)
                        margin = old_t.get("margin_usd", 0.0)
                        actual_entry = old_t.get("actual_entry") or old_t.get("planned_entry", close_px)
                        rem_ratio = 0.5 if old_t.get("tp1_partial_closed") else 1.0

                        if old_sig == "long":
                            raw_pct = (close_px - actual_entry) / actual_entry * lev
                        else:
                            raw_pct = (actual_entry - close_px) / actual_entry * lev

                        taker_fee, _, _ = self._fee_rates()
                        exit_fee = self._record_fee(old_t, old_t.get("position_size_usd", 0.0) * rem_ratio, taker_fee)
                        leg_net = round(margin * rem_ratio * raw_pct - exit_fee, 2)

                        final_pnl = round(old_t.get("pnl_usd", 0.0) + leg_net, 2)
                        old_t["pnl_usd"] = final_pnl
                        old_t["pnl_percent"] = round((final_pnl / margin) * 100.0, 2) if margin > 0 else 0.0
                        old_t["status"] = "closed_tp" if final_pnl >= 0 else "closed_sl"
                        old_t["closed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        old_t["close_reason"] = f"🔄 触发高置信度 ({conf}/10分) 反向 {sig_type.upper()} 信号，自动平仓旧 {old_sig.upper()} 仓位锁定利润离场"

                        if not old_t.get("is_live"):
                            cfg["paper_account_balance"] = round(cfg.get("paper_account_balance", 10000.0) + leg_net, 2)

                        logger.info(f"[SniperEngine] 🔄 Reversal triggered for {symbol}: closed old {old_sig.upper()} position at ${close_px}, PnL=${final_pnl}")
                        self._send_notification(
                            f"🔄 狙击智能平仓翻向通知：{symbol}",
                            f"🔄 *【反向信号平仓翻向通知】*\n币种：{symbol}\n旧持仓：{old_sig.upper()} -> 现信号：{sig_type.upper()} ({conf}/10分)\n平仓触发价：${close_px}\n实现盈亏：${final_pnl} USD ({old_t['pnl_percent']}%)\n原因：{old_t['close_reason']}"
                        )

        # Re-evaluate active positions count AFTER handling existing trades for this symbol
        entry_zone = json_signal.get("entry_zone") or {}
        raw_min = entry_zone.get("min")
        raw_max = entry_zone.get("max")
        if raw_min is None or raw_max is None:
            return None

        entry_min = float(min(raw_min, raw_max))
        entry_max = float(max(raw_min, raw_max))
        entry_mid = dynamic_round_price((entry_min + entry_max) / 2.0)
        # 🪜 Ladder order: split position into 3 tranches across entry zone
        # Tranche 1 (40%): at entry_min — aggressive edge catch
        # Tranche 2 (35%): at midpoint — core position
        # Tranche 3 (25%): at entry_max — deep pullback bonus
        ladder = [
            {"price": dynamic_round_price(entry_min), "ratio": 0.40, "filled": False},
            {"price": entry_mid, "ratio": 0.35, "filled": False},
            {"price": dynamic_round_price(entry_max), "ratio": 0.25, "filled": False},
        ]
        # Weighted average entry for display/sizing (assumes all 3 fill)
        planned_entry = dynamic_round_price(
            ladder[0]["price"] * 0.40 + ladder[1]["price"] * 0.35 + ladder[2]["price"] * 0.25
        )
        sl = float(json_signal.get("stop_loss", 0.0))

        raw_tps = json_signal.get("take_profit_targets") or []
        tp_list = [float(x) for x in raw_tps if x is not None]

        if not tp_list or sl <= 0:
            return None

        # 🛡️ Hard signal-geometry validation.
        if sig_type == "long":
            geometry_ok = (sl < entry_min) and (tp_list[0] > entry_max)
            reject_reason = f"多头要求 SL({sl}) < 入场区下限({entry_min}) 且 TP1({tp_list[0]}) > 入场区上限({entry_max})"
        else:
            geometry_ok = (sl > entry_max) and (tp_list[0] < entry_min)
            reject_reason = f"空头要求 SL({sl}) > 入场区上限({entry_max}) 且 TP1({tp_list[0]}) < 入场区下限({entry_min})"
        if not geometry_ok:
            logger.warning(f"[SniperEngine] Rejected {sig_type.upper()} signal for {symbol}: invalid geometry. {reject_reason}")
            return None

        # Check current price vs entry zone -> determine whether to place pending limit order or fill INSTANTLY
        curr_px = float(current_price) if (current_price and float(current_price) > 0) else 0.0
        
        # Check if current price is already invalidated (past stop loss)
        if curr_px > 0:
            if (sig_type == "long" and curr_px <= sl) or (sig_type == "short" and curr_px >= sl):
                logger.warning(f"[SniperEngine] Rejected {sig_type.upper()} signal for {symbol}: current price ${curr_px} already breached SL ${sl}.")
                return None

        # Check if current price is ALREADY inside entry_zone or better -> Instant Market Fill!
        instant_fill = False
        if curr_px > 0:
            if sig_type == "long" and curr_px <= entry_max and curr_px > sl:
                instant_fill = True
            elif sig_type == "short" and curr_px >= entry_min and curr_px < sl:
                instant_fill = True

        balance = cfg.get("live_account_balance" if mode == "live" else "paper_account_balance", 10000.0)
        risk_pct = cfg.get("risk_per_trade_percent", 2.0)
        max_lev = cfg.get("max_leverage", 15)

        # 📊 Adaptive risk: adjust risk_pct based on recent win rate
        risk_pct = self._adaptive_risk_adjust(risk_pct)

        exec_entry = curr_px if instant_fill else planned_entry
        pos_val, margin, lev = self.calculate_trade_params(
            balance, risk_pct, exec_entry, sl, conf, max_lev
        )

        trade_id = f"trade-{int(time.time() * 1000)}"
        new_trade = {
            "id": trade_id,
            "symbol": symbol,
            "signal_type": sig_type,
            "status": "filled" if instant_fill else "pending",
            "confidence_score": conf,
            "entry_min": entry_min,
            "entry_max": entry_max,
            "planned_entry": planned_entry,
            "ladder": ladder,
            "ladder_filled_count": 3 if instant_fill else 0,
            "actual_entry": exec_entry if instant_fill else None,
            "stop_loss": sl,
            "initial_stop_loss": sl,
            "take_profit_targets": tp_list,
            "leverage": lev,
            "position_size_usd": pos_val,
            "margin_usd": margin,
            "entered_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "closed_at": None,
            "pnl_usd": 0.0,
            "pnl_percent": 0.0,
            "close_reason": f"⚡ 市价即时吃单成单（当前价 ${curr_px} 处于吃单区间 [{entry_min}, {entry_max}] 内）" if instant_fill else "",
            "tp1_partial_closed": False,
            "is_live": (mode == "live"),
            "live_order_id": None,
            "live_exchange": cfg.get("live_exchange", "binance") if mode == "live" else None,
            "protective_sl_order_id": None,
            "fees_usd": 0.0
        }

        if instant_fill and not new_trade.get("is_live"):
            taker_fee, _, _ = self._fee_rates()
            entry_fee = self._record_fee(new_trade, pos_val, taker_fee)
            cfg["paper_account_balance"] = round(cfg.get("paper_account_balance", 10000.0) - entry_fee, 4)
            logger.info(f"[SniperEngine] ⚡ Instant Market Fill for {symbol} at ${curr_px} (Entry Zone [{entry_min}, {entry_max}]).")

            self._send_notification(
                f"⚡ 狙击模拟即时吃单成单：{symbol}",
                f"⚡ *【即时吃单成单通知】*\n币种：{symbol} ({sig_type.upper()})\n当前市价：${curr_px}（在吃单区间 [{entry_min}, {entry_max}] 内，免去等待）\n建仓价：${curr_px}\n杠杆：{lev}x | 保证金：${margin}\n防守位：${sl} | 目标位：${tp_list[0]}"
            )

        # REAL LIVE TRADING CCXT EXECUTION
        if mode == "live":
            try:
                exchange, ex_id = self._init_live_ccxt()
                ccxt_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
                
                # 🛡️ Force ISOLATED margin mode on exchange before placing live order (protect full account balance)
                try:
                    exchange.set_margin_mode("ISOLATED", ccxt_symbol)
                    logger.info(f"[LiveSniper] 🛡️ 强制切换为逐仓模式 (ISOLATED): {ccxt_symbol}")
                except Exception as margin_e:
                    logger.info(f"[LiveSniper] set_margin_mode ISOLATED info ({ccxt_symbol}): {margin_e}")

                try:
                    exchange.set_leverage(lev, ccxt_symbol)
                except Exception as lev_e:
                    logger.warning(f"[LiveSniper] set_leverage failed: {lev_e}")

                side = "buy" if sig_type == "long" else "sell"
                
                try:
                    exchange.load_markets()
                    market = exchange.market(ccxt_symbol)
                    min_amount = market.get('limits', {}).get('amount', {}).get('min', 0.001)
                except Exception:
                    min_amount = 0.001

                raw_amount = pos_val / planned_entry
                if raw_amount < min_amount:
                    raw_amount = min_amount
                    pos_val = raw_amount * planned_entry
                    margin = pos_val / lev
                    new_trade["position_size_usd"] = round(pos_val, 2)
                    new_trade["margin_usd"] = round(margin, 2)

                try:
                    amount = float(exchange.amount_to_precision(ccxt_symbol, raw_amount))
                except Exception:
                    amount = round(raw_amount, 4)

                try:
                    limit_price = float(exchange.price_to_precision(ccxt_symbol, planned_entry))
                except Exception:
                    limit_price = planned_entry

                pos_side = "LONG" if sig_type == "long" else "SHORT"

                # 🧹 Defensive cleanup: remove any orphaned conditional orders
                # (SL/TP) left over from previously cancelled pending orders for
                # this symbol, so they don't interfere with the new position.
                self._cancel_all_conditional_orders_for_symbol(symbol)

                order_params = {}
                if ex_id == "binance":
                    # Only attach SL for crash protection; do NOT attach TP here.
                    # Attached TP would close 100% at TP1 on exchange side, overriding
                    # the monitoring loop's 50% partial close + TP2 strategy.
                    order_params = {
                        "positionSide": pos_side,
                        "stopLoss": {"triggerPrice": sl}
                    }
                elif ex_id == "okx":
                    order_params = {
                        "positionSide": pos_side.lower(),
                        "slTriggerPrice": str(sl),
                        "slOrderType": "market"
                    }
                elif ex_id == "bybit":
                    order_params = {
                        "positionSide": pos_side,
                        "stopLoss": str(sl)
                    }

                order_type = "market" if instant_fill else "limit"
                order_price = None if instant_fill else limit_price
                try:
                    live_res = exchange.create_order(
                        symbol=ccxt_symbol,
                        type=order_type,
                        side=side,
                        amount=amount,
                        price=order_price,
                        params=order_params
                    )
                except Exception as ord_e1:
                    logger.warning(f"[LiveSniper] Primary {order_type} order attempt with Hedge/Algo params failed ({ord_e1}), retrying clean {order_type} order...")
                    live_res = exchange.create_order(
                        symbol=ccxt_symbol,
                        type=order_type,
                        side=side,
                        amount=amount,
                        price=order_price,
                        params={}
                    )

                new_trade["live_order_id"] = str(live_res.get("id"))
                logger.info(f"[LiveSniper] Successfully submitted real {ex_id.upper()} contract order #{new_trade['live_order_id']} for {symbol} ({sig_type.upper()}) @ ${planned_entry}.")
            except Exception as live_e:
                logger.error(f"[LiveSniper] Real exchange order placement FAILED: {live_e}")
                new_trade["status"] = "cancelled"
                new_trade["close_reason"] = f"交易所拒单报错: {str(live_e)}"
                self.state["trades"].append(new_trade)
                self._save_state()
                raise RuntimeError(f"实盘开单失败 ({ex_id.upper()} 拒单)：{str(live_e)}")

        # Paper mode: immediate fill only when the limit price is already
        # marketable (price at/beyond planned_entry but stop still intact).
        # Fill at planned_entry — a resting limit order never fills mid-zone.
        # Skip if instant_fill already handled the fee above.
        if mode == "paper" and not instant_fill:
            _, maker_fee, _ = self._fee_rates()
            immediate_fill = (
                (sig_type == "long" and sl < current_price <= planned_entry) or
                (sig_type == "short" and sl > current_price >= planned_entry)
            )
            if immediate_fill:
                new_trade["status"] = "filled"
                new_trade["actual_entry"] = planned_entry
                entry_fee = self._record_fee(new_trade, pos_val, maker_fee)
                cfg["paper_account_balance"] = round(cfg.get("paper_account_balance", 0.0) - entry_fee, 4)

        self.state["trades"].append(new_trade)
        self._save_state()
        
        # Dispatch Sniper New Order Notification
        mode_label = "【实盘 API】" if mode == "live" else "【模拟盘】"
        sig_emoji = "📈 做多" if sig_type == "long" else "📉 做空"
        push_content = (
            f"🎯 *狙击挂单已就位 ({mode_label})*\n"
            f"币种方向：{symbol} {sig_emoji}\n"
            f"埋伏位点：${planned_entry} (区间 ${entry_min} - ${entry_max})\n"
            f"智能风控：杠杆 {lev}x | 保证金 ${margin} | 仓位价值 ${pos_val}\n"
            f"防守线 (SL)：${sl} | 第一目标 (TP1)：${tp_list[0]}\n"
        )
        self._send_notification(f"🎯 狙击挂单成单：{symbol}", push_content)

        logger.info(f"[SniperEngine] [{mode.upper()}] Placed new {sig_type.upper()} sniper trade for {symbol}: entry ${planned_entry}, margin ${margin}, lev {lev}x.")
        return new_trade

    def check_market_prices(self, symbol_prices_dict):
        """Thread-safe entry point."""
        with self._lock:
            self._check_market_prices_impl(symbol_prices_dict)

    def _check_market_prices_impl(self, symbol_prices_dict):
        cfg = self.state.get("config", {})
        mode = cfg.get("mode", "paper")
        if mode == "off":
            return

        # 🚨 Circuit breaker check on every tick: cancels pending orders and
        # blocks further fills once today's loss limit is breached
        halted = self._check_circuit_breaker()

        trades = self.state.get("trades", [])
        updated = False

        # 🔄 Live mode: sync position params from exchange every tick (60s cached)
        # Exchange is the single source of truth for live positions
        live_pos_map = {}
        if mode == "live" and cfg.get("live_api_key") and cfg.get("live_secret"):
            real_positions = self._fetch_live_positions()
            if real_positions:
                live_pos_map = {(p["symbol"], p["side"]): p for p in real_positions}
                for t in trades:
                    if t["status"] not in ["filled", "tp1_hit"]:
                        continue
                    if not t.get("is_live"):
                        continue
                    key = (t["symbol"], t["signal_type"].lower())
                    rp = live_pos_map.get(key)
                    if rp is None:
                        continue
                    # Unconditionally overwrite from exchange — no conditions
                    ex_lev = int(rp.get("leverage", 0) or 0)
                    if ex_lev > 0:
                        t["leverage"] = ex_lev
                    ex_margin = float(rp.get("margin", 0.0) or 0.0)
                    if ex_margin > 0:
                        t["margin_usd"] = round(ex_margin, 4)
                    ex_notional = float(rp.get("notional", 0.0) or 0.0)
                    if ex_notional > 0:
                        t["position_size_usd"] = round(ex_notional, 2)
                    ex_entry = float(rp.get("entry_price", 0.0) or 0.0)
                    if ex_entry > 0:
                        old_entry = t.get("actual_entry")
                        if old_entry is None or abs(float(old_entry) - ex_entry) / ex_entry > 0.002:
                            t["actual_entry"] = ex_entry
                            t["peak_pnl_pct"] = 0.0
                            t["trailing_sl_level"] = 0.0
                            t["locked_pnl_percent"] = 0.0
                        else:
                            t["actual_entry"] = ex_entry
                    # PnL from exchange (authoritative)
                    t["exchange_pnl_percent"] = rp.get("unrealized_pnl_percent", 0.0)
                    t["unrealized_pnl_percent"] = rp.get("unrealized_pnl_percent", 0.0)
                    ex_pnl = float(rp.get("unrealized_pnl", 0.0) or 0.0)
                    t["unrealized_pnl_usd"] = ex_pnl
                    t["pnl_usd"] = ex_pnl
                    ex_mark = float(rp.get("mark_price", 0.0) or 0.0)
                    if ex_mark > 0:
                        t["current_price"] = ex_mark
                    updated = True

        for t in trades:
            if t["status"] in ["cancelled", "closed_tp", "closed_sl"]:
                continue

            symbol = t["symbol"]
            price_val = symbol_prices_dict.get(symbol)
            if price_val is None:
                continue

            if isinstance(price_val, dict):
                high_price = float(price_val.get("high", price_val.get("close", 0.0)))
                low_price = float(price_val.get("low", price_val.get("close", 0.0)))
                current_price = float(price_val.get("close", 0.0))
            else:
                high_price = low_price = current_price = float(price_val)

            sig_type = t["signal_type"]
            entry_min = t["entry_min"]
            entry_max = t["entry_max"]
            planned_entry = t["planned_entry"]
            sl = t["stop_loss"]
            tps = t["take_profit_targets"]
            pos_val = t["position_size_usd"]
            margin = t["margin_usd"]
            lev = t["leverage"]

            # Real Live Order Sync via CCXT
            if t.get("is_live") and t.get("live_order_id") and t["status"] == "pending":
                try:
                    exchange, ex_id = self._init_live_ccxt()
                    ccxt_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
                    live_ord = exchange.fetch_order(t["live_order_id"], ccxt_symbol)
                    ord_status = str(live_ord.get("status")).lower()

                    if ord_status == "closed" and t["status"] == "pending":
                        t["status"] = "filled"
                        t["actual_entry"] = float(live_ord.get("average") or live_ord.get("price") or planned_entry)
                        updated = True

                        # 🛡️ Exchange-side protective stop: keeps the position
                        # protected even if this app crashes / sleeps / drops network
                        if not t.get("protective_sl_order_id"):
                            try:
                                filled_amount = float(live_ord.get("amount") or round(pos_val / t["actual_entry"], 4))
                            except Exception:
                                filled_amount = round(pos_val / t["actual_entry"], 4)
                            prot_id = self._place_live_protective_sl(exchange, ex_id, symbol, sig_type, filled_amount, sl)
                            if prot_id:
                                t["protective_sl_order_id"] = prot_id

                        # Dispatch Filled Notification
                        self._send_notification(
                            f"⚡ 狙击实盘建仓成功：{symbol}",
                            f"⚡ *【实盘建仓履约通知】*\n币种：{symbol} ({sig_type.upper()})\n建仓价：${t['actual_entry']}\n杠杆：{lev}x | 保证金：${margin}\n防守位：${sl} | 目标位：${tps[0]}\n交易所侧保护止损单：{'已挂设 ✅' if t.get('protective_sl_order_id') else '未挂设 ⚠️'}"
                        )
                    elif ord_status == "canceled":
                        t["status"] = "cancelled"
                        t["close_reason"] = "实盘交易所订单已被撤销"
                        updated = True
                        # Clean up SL/TP conditional orders that may still be attached
                        if t.get("is_live"):
                            self._cancel_all_conditional_orders_for_symbol(symbol)
                except Exception as sync_e:
                    logger.warning(f"[LiveSniper] Order sync warning for {symbol}: {sync_e}")

            # Paper / Local Engine Order Tracking
            if t["status"] == "pending":
                t["current_price"] = current_price
                updated = True

                # ⏳ Pending order expiry: a stale setup is not a valid setup
                ttl_hours = float(cfg.get("pending_ttl_hours", 24.0))
                if ttl_hours > 0:
                    try:
                        entered_dt = datetime.strptime(t.get("entered_at", ""), "%Y-%m-%d %H:%M:%S")
                        age_hours = (datetime.now() - entered_dt).total_seconds() / 3600.0
                    except Exception:
                        age_hours = 0.0
                    if age_hours > ttl_hours:
                        if t.get("is_live") and t.get("live_order_id"):
                            try:
                                exchange, ex_id = self._init_live_ccxt()
                                ccxt_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
                                exchange.cancel_order(t["live_order_id"], ccxt_symbol)
                                # Clean up SL/TP conditional orders attached to the expired pending order
                                self._cancel_all_conditional_orders_for_symbol(symbol)
                            except Exception as e:
                                logger.warning(f"[LiveSniper] TTL cancel failed for {symbol}: {e} — 保留挂单，下一 tick 重试")
                                continue
                        t["status"] = "cancelled"
                        t["close_reason"] = f"⏳ 挂单超过 {int(ttl_hours)} 小时未成交，点位失效自动撤单"
                        updated = True
                        logger.info(f"[SniperEngine] Pending order for {symbol} expired after {round(age_hours, 1)}h.")
                        continue

                # Realistic limit-order semantics:
                # - a resting limit fills ONLY when price reaches planned_entry
                #   (evaluating against candle low for long / candle high for short)
                # - if price gaps straight through the stop-loss first, the
                #   setup is structurally broken -> cancel the order
                if sig_type == "long":
                    invalidated = low_price <= sl
                    crossed_entry = low_price <= planned_entry
                else:
                    invalidated = high_price >= sl
                    crossed_entry = high_price >= planned_entry

                if invalidated:
                    # Cancel the live order and its attached conditional orders on the exchange
                    if t.get("is_live") and t.get("live_order_id"):
                        try:
                            exchange, ex_id = self._init_live_ccxt()
                            ccxt_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
                            exchange.cancel_order(t["live_order_id"], ccxt_symbol)
                            self._cancel_all_conditional_orders_for_symbol(symbol)
                        except Exception as inv_e:
                            logger.warning(f"[LiveSniper] Invalidated cancel failed for {symbol}: {inv_e}")
                    t["status"] = "cancelled"
                    t["close_reason"] = f"⚠️ 价格 (${low_price if sig_type == 'long' else high_price}) 未回踩埋伏位，先行穿透防守线 (${sl})，结构破坏挂单自动作废"
                    updated = True
                    logger.info(f"[SniperEngine] Pending order for {symbol} invalidated: price breached SL {sl} before entry.")
                    continue

                if crossed_entry and not halted:
                    filled_count = len([x for x in self.state.get("trades", []) if x["status"] in ["filled", "tp1_hit"]])
                    max_active = cfg.get("max_active_trades", 3)
                    if filled_count < max_active:
                        t["status"] = "filled"
                        t["actual_entry"] = planned_entry
                        if not t.get("is_live"):
                            _, maker_fee, _ = self._fee_rates()
                            entry_fee = self._record_fee(t, pos_val, maker_fee)
                            cfg["paper_account_balance"] = round(cfg.get("paper_account_balance", 0.0) - entry_fee, 4)
                        logger.info(f"[SniperEngine] Limit order filled for {symbol} at planned entry ${planned_entry}.")

                        # Dispatch Paper Filled Notification
                        self._send_notification(
                            f"⚡ 狙击模拟建仓成功：{symbol}",
                            f"⚡ *【模拟盘建仓成单通知】*\n币种：{symbol} ({sig_type.upper()})\n成交价：${planned_entry}\n杠杆：{lev}x | 保证金：${margin}\n防守位：${sl} | 目标位：${tps[0]}"
                        )
                    else:
                        logger.info(f"[SniperEngine] Price triggered for {symbol}, but active filled trades limit ({max_active}) reached. Order queued in pending table.")
                continue

            actual_entry = t.get("actual_entry") or planned_entry
            amount = round(pos_val / actual_entry, 4)

            # Real-Time Floating Unrealized PnL Calculation
            # For live positions with exchange sync, exchange data is authoritative
            has_exchange_sync = t.get("is_live") and t.get("exchange_pnl_percent") is not None
            if not has_exchange_sync:
                t["current_price"] = current_price
            if sig_type == "long":
                float_pct = (current_price - actual_entry) / actual_entry * lev
            else:
                float_pct = (actual_entry - current_price) / actual_entry * lev

            rem_ratio = 0.5 if t.get("tp1_partial_closed") else 1.0
            if not has_exchange_sync:
                t["unrealized_pnl_percent"] = round(float_pct * 100.0, 2)
                realized_pnl = t.get("pnl_usd", 0.0) if t.get("tp1_partial_closed") else 0.0
                t["unrealized_pnl_usd"] = round((margin * rem_ratio * float_pct) + realized_pnl, 2)
            updated = True

            # 🔒 Dynamic Trailing Stop Loss: milestones at every 10% PnL
            # For live positions, prefer exchange-reported PnL (accounts for fees/funding)
            # to avoid triggering on inflated theoretical gains
            trailing_updated = False
            if t["status"] in ["filled", "tp1_hit"] and float_pct > 0:
                if t.get("is_live") and t.get("exchange_pnl_percent") is not None:
                    # Use exchange-synced PnL% (survives monitoring loop overwrite)
                    effective_pnl_pct = float(t["exchange_pnl_percent"])
                else:
                    effective_pnl_pct = float_pct * 100.0
                if effective_pnl_pct > 0:
                    trailing_updated = self._update_trailing_stop_loss(
                        t=t,
                        current_pnl_pct=effective_pnl_pct,
                        actual_entry=actual_entry,
                        sig_type=sig_type,
                        lev=lev,
                        amount=round(amount * rem_ratio, 4)
                    )
                if trailing_updated:
                    sl = t["stop_loss"]  # refresh local sl variable for the SL check below

            # 💸 Funding fee model (paper)
            if not t.get("is_live") and t["status"] in ["filled", "tp1_hit"]:
                funding_rate = float(cfg.get("funding_rate_per_8h", 0.0001))
                if funding_rate > 0:
                    current_epoch = int(time.time() // FUNDING_EPOCH_SECONDS)
                    last_epoch = t.get("funding_epoch")
                    if last_epoch is None:
                        t["funding_epoch"] = current_epoch
                        updated = True
                    elif current_epoch > last_epoch:
                        epochs = current_epoch - last_epoch
                        funding_fee = round(pos_val * rem_ratio * funding_rate * epochs, 4)
                        if funding_fee > 0:
                            t["funding_epoch"] = current_epoch
                            t["fees_usd"] = round(t.get("fees_usd", 0.0) + funding_fee, 4)
                            t["funding_fees_usd"] = round(t.get("funding_fees_usd", 0.0) + funding_fee, 4)
                            t["pnl_usd"] = round(t.get("pnl_usd", 0.0) - funding_fee, 4)
                            cfg["paper_account_balance"] = round(cfg.get("paper_account_balance", 0.0) - funding_fee, 4)
                            updated = True
                            logger.info(f"[SniperEngine] [{symbol}] Funding fee charged: ${funding_fee}")

            # ⏰ Time-based stop: close stale positions that haven't reached TP1 after 72h
            if t["status"] == "filled" and not t.get("tp1_partial_closed"):
                try:
                    entered_dt = datetime.strptime(t.get("entered_at", ""), "%Y-%m-%d %H:%M:%S")
                    hold_hours = (datetime.now() - entered_dt).total_seconds() / 3600.0
                except Exception:
                    hold_hours = 0.0
                max_hold_hours = float(cfg.get("max_hold_hours", 72.0))
                if max_hold_hours > 0 and hold_hours > max_hold_hours:
                    # Close at current market price — position is stale
                    close_px = current_price
                    if sig_type == "long":
                        stale_pnl_pct = (close_px - actual_entry) / actual_entry * lev
                    else:
                        stale_pnl_pct = (actual_entry - close_px) / actual_entry * lev
                    taker_fee, _, slippage = self._fee_rates()
                    exit_fee = self._record_fee(t, pos_val, taker_fee)
                    stale_net = round(margin * stale_pnl_pct - exit_fee, 2)
                    close_reason = f"⏰ 持仓超过 {int(max_hold_hours)}h 未达 TP1，时间止损市价平仓 (PnL: ${stale_net})"
                    if t.get("is_live"):
                        # Must confirm exchange close succeeded before marking closed
                        if not self._try_live_close(t, symbol, sig_type, round(amount, 4), reason=close_reason, alert_tag="time_stop", current_price=close_px):
                            updated = True
                            continue
                    t["pnl_usd"] = round(t.get("pnl_usd", 0.0) + stale_net, 2)
                    t["pnl_percent"] = round((t["pnl_usd"] / margin) * 100.0, 2) if margin > 0 else 0.0
                    t["status"] = "closed_tp" if stale_net >= 0 else "closed_sl"
                    t["closed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    t["close_reason"] = close_reason
                    if not t.get("is_live"):
                        cfg["paper_account_balance"] = round(cfg.get("paper_account_balance", 0.0) + stale_net, 2)
                    updated = True
                    logger.info(f"[SniperEngine] [{symbol}] Time-based stop: held {round(hold_hours, 1)}h > {max_hold_hours}h, closed at ${close_px}, PnL=${stale_net}")
                    self._send_notification(
                        f"⏰ 时间止损平仓：{symbol}",
                        f"⏰ *【时间止损通知】*\n币种：{symbol} ({sig_type.upper()})\n持仓时长：{round(hold_hours, 1)}h（超过 {int(max_hold_hours)}h 未达 TP1）\n平仓价：${close_px}\n实现盈亏：${t['pnl_usd']} USD ({t['pnl_percent']}%)\n资金已释放，等待下一个高共振机会。"
                    )
                    continue

            # 🛡️ PnL-based risk control (风控用实际盈亏做风控)
            max_trade_loss_pct = float(cfg.get("max_trade_loss_percent", 80.0))
            pnl_breached = (float_pct * 100.0 <= -max_trade_loss_pct)

            if sig_type == "long":
                if low_price <= sl or pnl_breached:
                    rem_ratio = 0.5 if t.get("tp1_partial_closed") else 1.0
                    trigger_reason = f"双保险触发：价格 ${low_price} 触及/穿透止损线 ${sl}" if not pnl_breached else f"实际盈亏风控触发：浮动亏损率达 {round(float_pct * 100.0, 2)}% 触及风控阈值 -{max_trade_loss_pct}%"
                    if not self._try_live_close(t, symbol, "long", round(amount * rem_ratio, 4), reason=trigger_reason, alert_tag="sl", current_price=low_price):
                        updated = True
                        continue
                    t["status"] = "closed_sl"
                    t["closed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    t["close_reason"] = trigger_reason
                    taker_fee, _, slippage = self._fee_rates()
                    exit_price = current_price if pnl_breached else sl
                    exec_price = exit_price * (1 - slippage)
                    loss_pct = (exec_price - actual_entry) / actual_entry * lev
                    exit_fee = self._record_fee(t, pos_val * rem_ratio, taker_fee)
                    leg_net = round(margin * rem_ratio * loss_pct - exit_fee, 2)
                    t["pnl_usd"] = round(t.get("pnl_usd", 0.0) + leg_net, 2)
                    t["pnl_percent"] = round((t["pnl_usd"] / margin) * 100.0, 2)
                    if not t.get("is_live"):
                        cfg["paper_account_balance"] = round(cfg["paper_account_balance"] + leg_net, 2)
                    updated = True
                    logger.info(f"[SniperEngine] [{symbol}] LONG SL Triggered ({trigger_reason}): PnL=${t['pnl_usd']}")

                    self._send_notification(
                        f"🛡️ 狙击风控触发离场：{symbol}",
                        f"🛡️ *【风控平仓通知】*\n币种：{symbol} (LONG)\n平仓触发价：${low_price if not pnl_breached else current_price} | 止损线：${sl}\n实现盈亏：${t['pnl_usd']} USD ({t['pnl_percent']}%)\n原因：{t['close_reason']}"
                    )
                    continue

                if not t.get("tp1_partial_closed", False) and tps and high_price >= tps[0] \
                        and self._try_live_close(t, symbol, "long", round(amount * 0.5, 4), reason=f"双保险 TP1 (${tps[0]}) 止盈平仓 50%", alert_tag="tp1", current_price=high_price):
                    t["tp1_partial_closed"] = True
                    t["status"] = "tp1_hit"
                    t["stop_loss"] = actual_entry
                    # Re-place protective SL on exchange for remaining 50% (cleanup inside _try_live_close removed it)
                    if t.get("is_live"):
                        self._place_protective_sl_on_exchange(symbol, "long", actual_entry, round(amount * 0.5, 4))
                    taker_fee, _, _ = self._fee_rates()
                    part_pnl = (tps[0] - actual_entry) / actual_entry * lev * 0.5 * margin
                    exit_fee = self._record_fee(t, pos_val * 0.5, taker_fee)
                    part_net = round(part_pnl - exit_fee, 2)
                    t["pnl_usd"] = round(t.get("pnl_usd", 0.0) + part_net, 2)
                    if not t.get("is_live"):
                        cfg["paper_account_balance"] = round(cfg["paper_account_balance"] + part_net, 2)
                    t["close_reason"] = f"🎯 达到 TP1 (${tps[0]})，部分平仓 50% 并在保本位锁定防守。"
                    updated = True

                    self._send_notification(
                        f"🎉 狙击 TP1 阶段止盈保本：{symbol}",
                        f"🎉 *【阶段止盈 & 保本推损通知】*\n币种：{symbol} (LONG)\n触发价：${high_price} | 目标位 TP1：${tps[0]}\n已平仓 50% 浮盈落袋：+${part_net} USD（净额，已扣费）\n🛡️ *防守线已自动上移至建仓成本价 (${actual_entry})，已锁定无风险持仓！*"
                    )

                max_tp = max(tps) if tps else 0
                rem_factor = 0.5 if t.get("tp1_partial_closed") else 1.0
                if max_tp > 0 and high_price >= max_tp:
                    if not self._try_live_close(t, symbol, "long", round(amount * rem_factor, 4), reason=f"双保险终极止盈 (${max_tp}) 全平出局", alert_tag="tp", current_price=high_price):
                        updated = True
                        continue
                    t["status"] = "closed_tp"
                    t["closed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    taker_fee, _, _ = self._fee_rates()
                    rem_pnl = (max_tp - actual_entry) / actual_entry * lev * rem_factor * margin
                    exit_fee = self._record_fee(t, pos_val * rem_factor, taker_fee)
                    rem_net = round(rem_pnl - exit_fee, 2)
                    t["pnl_usd"] = round(t.get("pnl_usd", 0.0) + rem_net, 2)
                    t["pnl_percent"] = round((t["pnl_usd"] / margin) * 100.0, 2)
                    if not t.get("is_live"):
                        cfg["paper_account_balance"] = round(cfg["paper_account_balance"] + rem_net, 2)
                    t["close_reason"] = f"🎉 触发终极止盈位 (${max_tp})"
                    updated = True

                    self._send_notification(
                        f"🎊 狙击终极全平止盈：{symbol}",
                        f"🎊 *【终极止盈全平通知】*\n币种：{symbol} (LONG)\n止盈触发价：${high_price} | 终极目标：${max_tp}\n累计平仓最终收益：+${t['pnl_usd']} USD (+{t['pnl_percent']}%)（净额，累计手续费 ${t.get('fees_usd', 0)}）\n离场原因：{t['close_reason']}"
                    )

            elif sig_type == "short":
                if high_price >= sl or pnl_breached:
                    rem_ratio = 0.5 if t.get("tp1_partial_closed") else 1.0
                    trigger_reason = f"双保险触发：价格 ${high_price} 触及/穿透止损线 ${sl}" if not pnl_breached else f"实际盈亏风控触发：浮动亏损率达 {round(float_pct * 100.0, 2)}% 触及风控阈值 -{max_trade_loss_pct}%"
                    if not self._try_live_close(t, symbol, "short", round(amount * rem_ratio, 4), reason=trigger_reason, alert_tag="sl", current_price=high_price):
                        updated = True
                        continue
                    t["status"] = "closed_sl"
                    t["closed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    t["close_reason"] = trigger_reason
                    taker_fee, _, slippage = self._fee_rates()
                    exit_price = current_price if pnl_breached else sl
                    exec_price = exit_price * (1 + slippage)
                    loss_pct = (actual_entry - exec_price) / actual_entry * lev
                    exit_fee = self._record_fee(t, pos_val * rem_ratio, taker_fee)
                    leg_net = round(margin * rem_ratio * loss_pct - exit_fee, 2)
                    t["pnl_usd"] = round(t.get("pnl_usd", 0.0) + leg_net, 2)
                    t["pnl_percent"] = round((t["pnl_usd"] / margin) * 100.0, 2)
                    if not t.get("is_live"):
                        cfg["paper_account_balance"] = round(cfg["paper_account_balance"] + leg_net, 2)
                    updated = True
                    logger.info(f"[SniperEngine] [{symbol}] SHORT SL Triggered ({trigger_reason}): PnL=${t['pnl_usd']}")

                    self._send_notification(
                        f"🛡️ 狙击风控触发离场：{symbol}",
                        f"🛡️ *【风控平仓通知】*\n币种：{symbol} (SHORT)\n平仓触发价：${high_price if not pnl_breached else current_price} | 止损线：${sl}\n实现盈亏：${t['pnl_usd']} USD ({t['pnl_percent']}%)\n原因：{t['close_reason']}"
                    )
                    continue

                if not t.get("tp1_partial_closed", False) and tps and low_price <= tps[0] \
                        and self._try_live_close(t, symbol, "short", round(amount * 0.5, 4), reason=f"双保险 TP1 (${tps[0]}) 止盈平仓 50%", alert_tag="tp1", current_price=low_price):
                    t["tp1_partial_closed"] = True
                    t["status"] = "tp1_hit"
                    t["stop_loss"] = actual_entry
                    # Re-place protective SL on exchange for remaining 50% (cleanup inside _try_live_close removed it)
                    if t.get("is_live"):
                        self._place_protective_sl_on_exchange(symbol, "short", actual_entry, round(amount * 0.5, 4))
                    taker_fee, _, _ = self._fee_rates()
                    part_pnl = (actual_entry - tps[0]) / actual_entry * lev * 0.5 * margin
                    exit_fee = self._record_fee(t, pos_val * 0.5, taker_fee)
                    part_net = round(part_pnl - exit_fee, 2)
                    t["pnl_usd"] = round(t.get("pnl_usd", 0.0) + part_net, 2)
                    if not t.get("is_live"):
                        cfg["paper_account_balance"] = round(cfg["paper_account_balance"] + part_net, 2)
                    t["close_reason"] = f"🎯 达到 TP1 (${tps[0]})，部分平仓 50% 并在保本位锁定防守。"
                    updated = True

                    self._send_notification(
                        f"🎉 狙击 TP1 阶段止盈保本：{symbol}",
                        f"🎉 *【阶段止盈 & 保本推损通知】*\n币种：{symbol} (SHORT)\n触发价：${low_price} | 目标位 TP1：${tps[0]}\n已平仓 50% 浮盈落袋：+${part_net} USD（净额，已扣费）\n🛡️ *防守线已自动上移至建仓成本价 (${actual_entry})，已锁定无风险持仓！*"
                    )

                min_tp = min(tps) if tps else 999999
                rem_factor = 0.5 if t.get("tp1_partial_closed") else 1.0
                if min_tp < 999999 and low_price <= min_tp:
                    if not self._try_live_close(t, symbol, "short", round(amount * rem_factor, 4), reason=f"双保险终极止盈 (${min_tp}) 全平出局", alert_tag="tp", current_price=low_price):
                        updated = True
                        continue
                    t["status"] = "closed_tp"
                    t["closed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    taker_fee, _, _ = self._fee_rates()
                    rem_pnl = (actual_entry - min_tp) / actual_entry * lev * rem_factor * margin
                    exit_fee = self._record_fee(t, pos_val * rem_factor, taker_fee)
                    rem_net = round(rem_pnl - exit_fee, 2)
                    t["pnl_usd"] = round(t.get("pnl_usd", 0.0) + rem_net, 2)
                    t["pnl_percent"] = round((t["pnl_usd"] / margin) * 100.0, 2)
                    if not t.get("is_live"):
                        cfg["paper_account_balance"] = round(cfg["paper_account_balance"] + rem_net, 2)
                    t["close_reason"] = f"🎉 触发终极止盈位 (${min_tp})"
                    updated = True

                    self._send_notification(
                        f"🎊 狙击终极全平止盈：{symbol}",
                        f"🎊 *【终极止盈全平通知】*\n币种：{symbol} (SHORT)\n止盈触发价：${low_price} | 终极目标：${min_tp}\n累计平仓最终收益：+${t['pnl_usd']} USD (+{t['pnl_percent']}%)（净额，累计手续费 ${t.get('fees_usd', 0)}）\n离场原因：{t['close_reason']}"
                    )

        if updated:
            self._save_state()
