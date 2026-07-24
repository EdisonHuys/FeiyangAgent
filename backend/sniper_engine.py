import os
import json
import time
import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

# Perpetual futures funding settles every 8 hours (UTC 00:00 / 08:00 / 16:00)
FUNDING_EPOCH_SECONDS = 8 * 3600

class SniperEngine:
    def __init__(self, root_dir):
        self.root_dir = root_dir
        self.state_file = os.path.join(root_dir, "trades.json")
        # Guards all state mutations: the 10s price loop, hourly LLM loop and
        # API handlers (manual close / config update) run on different threads.
        self._lock = threading.RLock()
        self.state = self._load_state()

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
                "min_leverage": 35,
                "max_leverage": 70,
                "fixed_leverage": 50,
                "live_exchange": "binance",
                "live_api_key": "",
                "live_secret": "",
                "live_passphrase": "",
                "live_trading_mode": "swap",
                # Fee & slippage model (paper-mode accounting realism).
                # At 70x leverage, a 0.05% taker fee on notional equals 3.5% of
                # margin per side — ignoring fees flatters backtest results badly.
                "taker_fee_rate": 0.0005,   # market orders / stop exits
                "maker_fee_rate": 0.0002,   # resting limit entries
                "slippage_rate": 0.0005,    # adverse price slip on market exits
                # Daily drawdown circuit breaker: stop opening anything new and
                # cancel all pending orders once today's realized loss exceeds
                # this % of the day-start balance. Auto-resets next day.
                "circuit_breaker_enabled": True,
                "daily_max_loss_percent": 6.0,
                # Pending orders older than this many hours are auto-cancelled
                # (a stale setup is not a valid setup). 0 disables expiry.
                "pending_ttl_hours": 24.0,
                # Perpetual funding fee charged on notional every 8 hours
                # (UTC 00/08/16) while a position is open. 0 disables.
                "funding_rate_per_8h": 0.0001
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
        ccxt_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
        close_side = "sell" if sig_type == "long" else "buy"
        try:
            try:
                amount = float(exchange.amount_to_precision(ccxt_symbol, amount))
            except Exception:
                pass
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

    def _cancel_protective_sl(self, trade):
        """Cancel the exchange-side protective stop once the position is closed locally."""
        order_id = trade.get("protective_sl_order_id")
        if not trade.get("is_live") or not order_id:
            return
        try:
            exchange, ex_id = self._init_live_ccxt()
            symbol = trade["symbol"]
            ccxt_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
            exchange.cancel_order(order_id, ccxt_symbol)
            logger.info(f"[LiveSniper] 保护性止损单 #{order_id} 已随本地平仓撤销 ({symbol})")
        except Exception as e:
            logger.warning(f"[LiveSniper] 撤销保护性止损单失败 ({trade.get('symbol')}): {e}")
        finally:
            trade["protective_sl_order_id"] = None

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
                try:
                    exchange, ex_id = self._init_live_ccxt()
                    bal = exchange.fetch_balance()
                    live_usdt = float(bal.get("total", {}).get("USDT", 0.0))
                    account_bal = round(live_usdt, 2)
                    cfg["live_account_balance"] = account_bal
                    self._save_state()
                except Exception as e:
                    logger.warning(f"[SniperEngine] Live balance auto-sync warning: {e}")
        else:
            filtered_trades = [t for t in trades if t.get("is_live") is not True]
            account_bal = round(cfg.get("paper_account_balance", 10000.0), 2)

        closed_trades = [t for t in filtered_trades if t["status"] in ["closed_tp", "closed_sl"]]
        winning_trades = [t for t in closed_trades if t.get("pnl_usd", 0) > 0]
        losing_trades = [t for t in closed_trades if t.get("pnl_usd", 0) < 0]

        win_count = len(winning_trades)
        total_closed = len(closed_trades)
        win_rate = round((win_count / total_closed * 100.0), 1) if total_closed > 0 else 0.0

        total_pnl = sum(t.get("pnl_usd", 0) for t in closed_trades)
        win_dollars = sum(t.get("pnl_usd", 0) for t in winning_trades)
        loss_dollars = abs(sum(t.get("pnl_usd", 0) for t in losing_trades))

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
        else:
            filtered = [t for t in trades if t.get("is_live") is not True]

        return sorted(filtered, key=lambda x: x.get("entered_at", ""), reverse=True)

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

        risk_amount = balance * (risk_pct / 100.0)
        sl_distance_pct = abs(entry_price - stop_loss) / entry_price if entry_price > 0 else 0.02
        if sl_distance_pct <= 0.001:
            sl_distance_pct = 0.01

        # 🛡️ Anti-liquidation leverage cap:
        # A position is liquidated when adverse move ≈ 1/leverage (minus
        # maintenance margin). If lev * sl_distance_pct >= ~1, the exchange
        # force-liquidates BEFORE our stop-loss triggers — turning a planned
        # 2% risk into a 100% margin wipeout. Cap leverage so the stop-loss
        # always fires first, keeping a 25% safety buffer for maintenance
        # margin, fees and wick overshoot.
        max_safe_lev = max(1, int(0.75 / sl_distance_pct))
        if suggested_lev > max_safe_lev:
            logger.info(
                f"[SniperEngine] Leverage safety cap: {suggested_lev}x -> {max_safe_lev}x "
                f"(SL distance {round(sl_distance_pct * 100, 2)}%)"
            )
            suggested_lev = max_safe_lev

        pos_value_usd = risk_amount / sl_distance_pct
        margin_usd = pos_value_usd / suggested_lev

        if margin_usd > balance * 0.33:
            margin_usd = balance * 0.33
            pos_value_usd = margin_usd * suggested_lev

        # 🎯 10U Micro-Capital Auto-Protector ($10U - $20U 小资金适配)
        # Ensure position notional value is at least $6.00 to pass Binance 5U / OKX 10U minimum notional filter
        if balance <= 20.0:
            min_pos_value = 6.0
            if pos_value_usd < min_pos_value:
                pos_value_usd = min_pos_value
                margin_usd = round(pos_value_usd / suggested_lev, 2)

        return round(pos_value_usd, 2), round(margin_usd, 2), suggested_lev

    def close_position_manually(self, trade_id):
        """Thread-safe entry point."""
        with self._lock:
            return self._close_position_manually_impl(trade_id)

    def _close_position_manually_impl(self, trade_id):
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
                            self._try_live_close(old_t, symbol, old_sig, rem_amount, reason=f"🔄 触发高置信度 ({conf}/10分) 反向 {sig_type.upper()} 信号，市价平仓翻向", alert_tag="reversal", current_price=close_px)

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
        planned_entry = round((entry_min + entry_max) / 2.0, 2)
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
            _, taker_fee, _ = self._fee_rates()
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
                
                try:
                    exchange.set_leverage(lev, ccxt_symbol)
                except Exception as lev_e:
                    logger.warning(f"[LiveSniper] set_leverage failed: {lev_e}")

                side = "buy" if sig_type == "long" else "sell"
                raw_amount = pos_val / planned_entry
                try:
                    amount = float(exchange.amount_to_precision(ccxt_symbol, raw_amount))
                except Exception:
                    amount = round(raw_amount, 4)

                try:
                    limit_price = float(exchange.price_to_precision(ccxt_symbol, planned_entry))
                except Exception:
                    limit_price = planned_entry

                pos_side = "LONG" if sig_type == "long" else "SHORT"
                order_params = {}
                if ex_id == "binance":
                    order_params = {
                        "positionSide": pos_side,
                        "stopLoss": {"triggerPrice": sl},
                        "takeProfit": {"triggerPrice": tp_list[0]}
                    }
                elif ex_id == "okx":
                    order_params = {
                        "positionSide": pos_side.lower(),
                        "slTriggerPrice": str(sl),
                        "slOrderType": "market",
                        "tpTriggerPrice": str(tp_list[0]),
                        "tpOrderType": "market"
                    }
                elif ex_id == "bybit":
                    order_params = {
                        "positionSide": pos_side,
                        "stopLoss": str(sl),
                        "takeProfit": str(tp_list[0])
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
        if mode == "paper":
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
            if t.get("is_live") and t.get("live_order_id"):
                try:
                    exchange, ex_id = self._init_live_ccxt()
                    ccxt_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
                    live_ord = exchange.fetch_order(t["live_order_id"], ccxt_symbol)
                    ord_status = str(live_ord.get("status")).lower()

                    if ord_status == "closed" and t["status"] == "pending":
                        t["status"] = "filled"
                        t["actual_entry"] = float(live_ord.get("price") or planned_entry)
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
            t["current_price"] = current_price
            if sig_type == "long":
                float_pct = (current_price - actual_entry) / actual_entry * lev
            else:
                float_pct = (actual_entry - current_price) / actual_entry * lev

            rem_ratio = 0.5 if t.get("tp1_partial_closed") else 1.0
            t["unrealized_pnl_percent"] = round(float_pct * 100.0, 2)
            realized_pnl = t.get("pnl_usd", 0.0) if t.get("tp1_partial_closed") else 0.0
            t["unrealized_pnl_usd"] = round((margin * rem_ratio * float_pct) + realized_pnl, 2)
            updated = True

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

            if sig_type == "long":
                if low_price <= sl:
                    rem_ratio = 0.5 if t.get("tp1_partial_closed") else 1.0
                    if not self._try_live_close(t, symbol, "long", round(amount * rem_ratio, 4), reason=f"双保险触发：价格 ${low_price} 触及/穿透止损线 ${sl}", alert_tag="sl", current_price=low_price):
                        updated = True
                        continue
                    t["status"] = "closed_sl"
                    t["closed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    t["close_reason"] = f"🛡️ 双保险生效：触发防守线止损 (${sl})" if not t.get("tp1_partial_closed") else f"🛡️ 触及保本止损线离场 (${sl})"
                    taker_fee, _, slippage = self._fee_rates()
                    exec_price = sl * (1 - slippage)
                    loss_pct = (exec_price - actual_entry) / actual_entry * lev
                    exit_fee = self._record_fee(t, pos_val * rem_ratio, taker_fee)
                    leg_net = round(margin * rem_ratio * loss_pct - exit_fee, 2)
                    t["pnl_usd"] = round(t.get("pnl_usd", 0.0) + leg_net, 2)
                    t["pnl_percent"] = round((t["pnl_usd"] / margin) * 100.0, 2)
                    if not t.get("is_live"):
                        cfg["paper_account_balance"] = round(cfg["paper_account_balance"] + leg_net, 2)
                    updated = True
                    logger.info(f"[SniperEngine] [{symbol}] LONG Dual-Insurance SL Triggered: PnL=${t['pnl_usd']}")

                    self._send_notification(
                        f"🛡️ 狙击风控触发离场：{symbol}",
                        f"🛡️ *【双保险平仓通知】*\n币种：{symbol} (LONG)\n平仓触发价：${low_price} | 止损线：${sl}\n实现盈亏：${t['pnl_usd']} USD ({t['pnl_percent']}%)（已扣手续费 ${t.get('fees_usd', 0)}）\n原因：{t['close_reason']}"
                    )
                    continue

                if not t.get("tp1_partial_closed", False) and tps and high_price >= tps[0] \
                        and self._try_live_close(t, symbol, "long", round(amount * 0.5, 4), reason=f"双保险 TP1 (${tps[0]}) 止盈平仓 50%", alert_tag="tp1", current_price=high_price):
                    t["tp1_partial_closed"] = True
                    t["status"] = "tp1_hit"
                    t["stop_loss"] = actual_entry
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
                if high_price >= sl:
                    rem_ratio = 0.5 if t.get("tp1_partial_closed") else 1.0
                    if not self._try_live_close(t, symbol, "short", round(amount * rem_ratio, 4), reason=f"双保险触发：价格 ${high_price} 触及/穿透止损线 ${sl}", alert_tag="sl", current_price=high_price):
                        updated = True
                        continue
                    t["status"] = "closed_sl"
                    t["closed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    t["close_reason"] = f"🛡️ 双保险生效：触发防守线止损 (${sl})" if not t.get("tp1_partial_closed") else f"🛡️ 触及保本止损线离场 (${sl})"
                    taker_fee, _, slippage = self._fee_rates()
                    exec_price = sl * (1 + slippage)
                    loss_pct = (actual_entry - exec_price) / actual_entry * lev
                    exit_fee = self._record_fee(t, pos_val * rem_ratio, taker_fee)
                    leg_net = round(margin * rem_ratio * loss_pct - exit_fee, 2)
                    t["pnl_usd"] = round(t.get("pnl_usd", 0.0) + leg_net, 2)
                    t["pnl_percent"] = round((t["pnl_usd"] / margin) * 100.0, 2)
                    if not t.get("is_live"):
                        cfg["paper_account_balance"] = round(cfg["paper_account_balance"] + leg_net, 2)
                    updated = True
                    logger.info(f"[SniperEngine] [{symbol}] SHORT Dual-Insurance SL Triggered: PnL=${t['pnl_usd']}")

                    self._send_notification(
                        f"🛡️ 狙击风控触发离场：{symbol}",
                        f"🛡️ *【双保险平仓通知】*\n币种：{symbol} (SHORT)\n平仓触发价：${high_price} | 止损线：${sl}\n实现盈亏：${t['pnl_usd']} USD ({t['pnl_percent']}%)（已扣手续费 ${t.get('fees_usd', 0)}）\n原因：{t['close_reason']}"
                    )
                    continue

                if not t.get("tp1_partial_closed", False) and tps and low_price <= tps[0] \
                        and self._try_live_close(t, symbol, "short", round(amount * 0.5, 4), reason=f"双保险 TP1 (${tps[0]}) 止盈平仓 50%", alert_tag="tp1", current_price=low_price):
                    t["tp1_partial_closed"] = True
                    t["status"] = "tp1_hit"
                    t["stop_loss"] = actual_entry
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
