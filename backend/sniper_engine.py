import os
import json
import time
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class SniperEngine:
    def __init__(self, root_dir):
        self.root_dir = root_dir
        self.state_file = os.path.join(root_dir, "trades.json")
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
                "live_trading_mode": "swap"
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
                logger.error(f"Failed to load trades.json: {e}")
        return default_state

    def _save_state(self):
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save trades.json: {e}")

    def _send_notification(self, title, content):
        try:
            from notifier import Notifier
            from app import load_yaml_config
            yaml_cfg = load_yaml_config()
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
        cfg = self.state.get("config", {})
        for k, v in new_cfg.items():
            if k in cfg:
                cfg[k] = v
        self.state["config"] = cfg
        self._save_state()
        return self.get_config()

    def get_dashboard_data(self):
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
            "config": dashboard_cfg
        }

    def get_trades(self, mode_filter=None):
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
            trade["status"] = "cancelled"
            trade["close_reason"] = "✋ 用户手动在界面撤销挂单"
            self._save_state()
            return {"status": "success", "message": f"已成功撤销 {symbol} 的埋伏挂单！"}

        # If trade is filled or tp1_hit, execute market close
        amount = round(pos_val / actual_entry, 4)
        rem_factor = 0.5 if trade.get("tp1_partial_closed") else 1.0

        if trade.get("is_live"):
            self._execute_live_market_close(symbol, sig_type, round(amount * rem_factor, 4), reason="用户手动在界面点击市价平仓")

        if sig_type == "long":
            float_pct = (current_price - actual_entry) / actual_entry * lev
        else:
            float_pct = (actual_entry - current_price) / actual_entry * lev

        final_pnl_usd = round(margin * rem_factor * float_pct, 2)
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
        try:
            initial_balance = float(initial_balance) if float(initial_balance) > 0 else 10000.0
        except Exception:
            initial_balance = 10000.0

        cfg = self.state.get("config", {})
        cfg["paper_account_balance"] = initial_balance
        cfg["initial_balance"] = initial_balance
        
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
            from data_fetcher import DataFetcher
            df_fetcher = DataFetcher(exchange_id=ex_id)
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
                self.save_state()
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
        cfg = self.state.get("config", {})
        mode = cfg.get("mode", "paper")
        if mode == "off":
            return None

        sig_type = str(json_signal.get("signal_type", "wait")).lower()
        conf = json_signal.get("confidence_score", 0)
        min_conf = cfg.get("min_confidence", 7)

        if sig_type not in ["long", "short"] or conf < min_conf:
            return None

        trades = self.state.get("trades", [])
        active_trades = [t for t in trades if t["status"] in ["pending", "filled", "tp1_hit"]]
        max_active = cfg.get("max_active_trades", 3)

        # Check existing active trade for this symbol FIRST to update/replace unfilled pending order
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
                            logger.warning(f"[LiveSniper] ⚠️ 撤销旧挂单失败: {cancel_e}")
                    
                    old_t["status"] = "cancelled"
                    old_t["close_reason"] = f"🔄 大模型更新 {sig_type.upper()} 点位策略，原未成交挂单已自动撤单重置"
                    logger.info(f"[SniperEngine] Cancelled old pending trade for {symbol} to replace with new {sig_type.upper()} signal.")
                else:
                    logger.info(f"[SniperEngine] Symbol {symbol} already has a FILLED position ({old_t['status']}). Skipping new signal.")
                    return None

        # Re-evaluate active positions count AFTER cancelling existing pending order for this symbol
        # Allow creating pending trade in trades list so all diagnostic signals are recorded in pending table
        # Max active trade limit is enforced during order fill execution

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

        balance = cfg.get("live_account_balance" if mode == "live" else "paper_account_balance", 10000.0)
        risk_pct = cfg.get("risk_per_trade_percent", 2.0)
        max_lev = cfg.get("max_leverage", 15)

        pos_val, margin, lev = self.calculate_trade_params(
            balance, risk_pct, planned_entry, sl, conf, max_lev
        )

        trade_id = f"trade-{int(time.time() * 1000)}"
        new_trade = {
            "id": trade_id,
            "symbol": symbol,
            "signal_type": sig_type,
            "status": "pending",
            "confidence_score": conf,
            "entry_min": entry_min,
            "entry_max": entry_max,
            "planned_entry": planned_entry,
            "actual_entry": None,
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
            "close_reason": "",
            "tp1_partial_closed": False,
            "is_live": (mode == "live"),
            "live_order_id": None,
            "live_exchange": cfg.get("live_exchange", "binance") if mode == "live" else None
        }

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

                try:
                    live_res = exchange.create_order(
                        symbol=ccxt_symbol,
                        type="limit",
                        side=side,
                        amount=amount,
                        price=limit_price,
                        params=order_params
                    )
                except Exception as ord_e1:
                    logger.warning(f"[LiveSniper] Primary limit order attempt with Hedge/Algo params failed ({ord_e1}), retrying One-Way clean limit order...")
                    live_res = exchange.create_order(
                        symbol=ccxt_symbol,
                        type="limit",
                        side=side,
                        amount=amount,
                        price=limit_price,
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

        if mode == "paper" and (entry_min <= current_price <= entry_max):
            new_trade["status"] = "filled"
            new_trade["actual_entry"] = current_price

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
        cfg = self.state.get("config", {})
        mode = cfg.get("mode", "paper")
        if mode == "off":
            return

        trades = self.state.get("trades", [])
        updated = False

        for t in trades:
            if t["status"] in ["cancelled", "closed_tp", "closed_sl"]:
                continue

            symbol = t["symbol"]
            current_price = symbol_prices_dict.get(symbol)
            if not current_price:
                continue

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
                        
                        # Dispatch Filled Notification
                        self._send_notification(
                            f"⚡ 狙击实盘建仓成功：{symbol}",
                            f"⚡ *【实盘建仓履约通知】*\n币种：{symbol} ({sig_type.upper()})\n建仓价：${t['actual_entry']}\n杠杆：{lev}x | 保证金：${margin}\n防守位：${sl} | 目标位：${tps[0]}"
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
                is_triggered = (sl < current_price <= entry_max) if sig_type == "long" else (sl > current_price >= entry_min)
                if is_triggered:
                    filled_count = len([x for x in self.state.get("trades", []) if x["status"] in ["filled", "tp1_hit"]])
                    max_active = cfg.get("max_active_trades", 3)
                    if filled_count < max_active:
                        t["status"] = "filled"
                        t["actual_entry"] = current_price
                        logger.info(f"[SniperEngine] Order Filled for {symbol} at ${current_price}.")
                        
                        # Dispatch Paper Filled Notification
                        self._send_notification(
                            f"⚡ 狙击模拟建仓成功：{symbol}",
                            f"⚡ *【模拟盘建仓成单通知】*\n币种：{symbol} ({sig_type.upper()})\n成交价：${current_price}\n杠杆：{lev}x | 保证金：${margin}\n防守位：${sl} | 目标位：${tps[0]}"
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
            if sig_type == "long":
                if current_price <= sl:
                    if t.get("is_live"):
                        self._execute_live_market_close(symbol, "long", amount, reason=f"双保险触发：价格 ${current_price} 触及/穿透止损线 ${sl}")
                    t["status"] = "closed_sl"
                    t["closed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    t["close_reason"] = f"🛡️ 双保险生效：触发防守线止损 (${sl})" if not t.get("tp1_partial_closed") else f"🛡️ 触及保本止损线离场 (${sl})"
                    loss_pct = (sl - actual_entry) / actual_entry * lev
                    t["pnl_percent"] = round(loss_pct * 100.0, 2)
                    t["pnl_usd"] = round(margin * loss_pct, 2)
                    if not t.get("is_live"):
                        cfg["paper_account_balance"] += t["pnl_usd"]
                    updated = True
                    logger.info(f"[SniperEngine] [{symbol}] LONG Dual-Insurance SL Triggered: PnL=${t['pnl_usd']}")
                    
                    self._send_notification(
                        f"🛡️ 狙击风控触发离场：{symbol}",
                        f"🛡️ *【双保险平仓通知】*\n币种：{symbol} (LONG)\n平仓触发价：${current_price} | 止损线：${sl}\n实现盈亏：${t['pnl_usd']} USD ({t['pnl_percent']}%)\n原因：{t['close_reason']}"
                    )
                    continue

                if not t.get("tp1_partial_closed", False) and tps and current_price >= tps[0]:
                    if t.get("is_live"):
                        self._execute_live_market_close(symbol, "long", round(amount * 0.5, 4), reason=f"双保险 TP1 (${tps[0]}) 止盈平仓 50%")
                    t["tp1_partial_closed"] = True
                    t["status"] = "tp1_hit"
                    t["stop_loss"] = actual_entry
                    part_pnl = (tps[0] - actual_entry) / actual_entry * lev * 0.5 * margin
                    t["pnl_usd"] += round(part_pnl, 2)
                    if not t.get("is_live"):
                        cfg["paper_account_balance"] += round(part_pnl, 2)
                    t["close_reason"] = f"🎯 达到 TP1 (${tps[0]})，部分平仓 50% 并在保本位锁定防守。"
                    updated = True
                    
                    self._send_notification(
                        f"🎉 狙击 TP1 阶段止盈保本：{symbol}",
                        f"🎉 *【阶段止盈 & 保本推损通知】*\n币种：{symbol} (LONG)\n触发价：${current_price} | 目标位 TP1：${tps[0]}\n已平仓 50% 浮盈落袋：+${round(part_pnl, 2)} USD\n🛡️ *防守线已自动上移至建仓成本价 (${actual_entry})，已锁定无风险持仓！*"
                    )

                max_tp = max(tps) if tps else 0
                if max_tp > 0 and current_price >= max_tp:
                    rem_factor = 0.5 if t.get("tp1_partial_closed") else 1.0
                    if t.get("is_live"):
                        self._execute_live_market_close(symbol, "long", round(amount * rem_factor, 4), reason=f"双保险终极止盈 (${max_tp}) 全平出局")
                    t["status"] = "closed_tp"
                    t["closed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    rem_pnl = (max_tp - actual_entry) / actual_entry * lev * rem_factor * margin
                    t["pnl_usd"] += round(rem_pnl, 2)
                    t["pnl_percent"] = round((t["pnl_usd"] / margin) * 100.0, 2)
                    if not t.get("is_live"):
                        cfg["paper_account_balance"] += round(rem_pnl, 2)
                    t["close_reason"] = f"🎉 触发终极止盈位 (${max_tp})"
                    updated = True
                    
                    self._send_notification(
                        f"🎊 狙击终极全平止盈：{symbol}",
                        f"🎊 *【终极止盈全平通知】*\n币种：{symbol} (LONG)\n止盈触发价：${current_price} | 终极目标：${max_tp}\n累计平仓最终收益：+${t['pnl_usd']} USD (+{t['pnl_percent']}%)\n离场原因：{t['close_reason']}"
                    )

            elif sig_type == "short":
                if current_price >= sl:
                    if t.get("is_live"):
                        self._execute_live_market_close(symbol, "short", amount, reason=f"双保险触发：价格 ${current_price} 触及/穿透止损线 ${sl}")
                    t["status"] = "closed_sl"
                    t["closed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    t["close_reason"] = f"🛡️ 双保险生效：触发防守线止损 (${sl})" if not t.get("tp1_partial_closed") else f"🛡️ 触及保本止损线离场 (${sl})"
                    loss_pct = (actual_entry - sl) / actual_entry * lev
                    t["pnl_percent"] = round(loss_pct * 100.0, 2)
                    t["pnl_usd"] = round(margin * loss_pct, 2)
                    if not t.get("is_live"):
                        cfg["paper_account_balance"] += t["pnl_usd"]
                    updated = True
                    logger.info(f"[SniperEngine] [{symbol}] SHORT Dual-Insurance SL Triggered: PnL=${t['pnl_usd']}")
                    
                    self._send_notification(
                        f"🛡️ 狙击风控触发离场：{symbol}",
                        f"🛡️ *【双保险平仓通知】*\n币种：{symbol} (SHORT)\n平仓触发价：${current_price} | 止损线：${sl}\n实现盈亏：${t['pnl_usd']} USD ({t['pnl_percent']}%)\n原因：{t['close_reason']}"
                    )
                    continue

                if not t.get("tp1_partial_closed", False) and tps and current_price <= tps[0]:
                    if t.get("is_live"):
                        self._execute_live_market_close(symbol, "short", round(amount * 0.5, 4), reason=f"双保险 TP1 (${tps[0]}) 止盈平仓 50%")
                    t["tp1_partial_closed"] = True
                    t["status"] = "tp1_hit"
                    t["stop_loss"] = actual_entry
                    part_pnl = (actual_entry - tps[0]) / actual_entry * lev * 0.5 * margin
                    t["pnl_usd"] += round(part_pnl, 2)
                    if not t.get("is_live"):
                        cfg["paper_account_balance"] += round(part_pnl, 2)
                    t["close_reason"] = f"🎯 达到 TP1 (${tps[0]})，部分平仓 50% 并在保本位锁定防守。"
                    updated = True
                    
                    self._send_notification(
                        f"🎉 狙击 TP1 阶段止盈保本：{symbol}",
                        f"🎉 *【阶段止盈 & 保本推损通知】*\n币种：{symbol} (SHORT)\n触发价：${current_price} | 目标位 TP1：${tps[0]}\n已平仓 50% 浮盈落袋：+${round(part_pnl, 2)} USD\n🛡️ *防守线已自动上移至建仓成本价 (${actual_entry})，已锁定无风险持仓！*"
                    )

                min_tp = min(tps) if tps else 999999
                if min_tp < 999999 and current_price <= min_tp:
                    rem_factor = 0.5 if t.get("tp1_partial_closed") else 1.0
                    if t.get("is_live"):
                        self._execute_live_market_close(symbol, "short", round(amount * rem_factor, 4), reason=f"双保险终极止盈 (${min_tp}) 全平出局")
                    t["status"] = "closed_tp"
                    t["closed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    rem_pnl = (actual_entry - min_tp) / actual_entry * lev * rem_factor * margin
                    t["pnl_usd"] += round(rem_pnl, 2)
                    t["pnl_percent"] = round((t["pnl_usd"] / margin) * 100.0, 2)
                    if not t.get("is_live"):
                        cfg["paper_account_balance"] += round(rem_pnl, 2)
                    t["close_reason"] = f"🎉 触发终极止盈位 (${min_tp})"
                    updated = True
                    
                    self._send_notification(
                        f"🎊 狙击终极全平止盈：{symbol}",
                        f"🎊 *【终极止盈全平通知】*\n币种：{symbol} (SHORT)\n止盈触发价：${current_price} | 终极目标：${min_tp}\n累计平仓最终收益：+${t['pnl_usd']} USD (+{t['pnl_percent']}%)\n离场原因：{t['close_reason']}"
                    )

        if updated:
            self._save_state()
