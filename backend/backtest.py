"""
Historical walk-forward backtester for the Feiyang sniper strategy.

Design principles:
- Replays history through the REAL production machinery: indicators ->
  payload -> LLM analysis -> SniperEngine. No duplicated trading logic:
  simulated orders go through the exact same limit-fill / TP1-partial /
  breakeven / stop-loss / fee / leverage-cap code path as paper trading.
- The simulated engine runs in a temp dir with the daily circuit breaker
  and pending-order TTL disabled (their clocks are real-time; the
  backtest time axis is simulated).
- Runs in a background thread with progress reporting and cancellation.

Known simulation limits (documented to the user in results):
- Price ticks are hourly candle CLOSES: intra-hour wicks are invisible,
  so fills/stops evaluate on closes only.
- Funding fees (real-time 8h epochs) do not apply on the simulated axis.
"""
import os
import time
import uuid
import logging
import tempfile
import threading
from datetime import datetime

import pandas as pd
from datetime import timezone
from dotenv import load_dotenv

try:
    from data_fetcher import get_data_fetcher
    from indicators import calculate_indicators, calculate_fibonacci_levels, clean_and_compress
    from agent import FeiyangAgent, load_system_prompt
    from sniper_engine import SniperEngine
except ImportError:
    from backend.data_fetcher import get_data_fetcher
    from backend.indicators import calculate_indicators, calculate_fibonacci_levels, clean_and_compress
    from backend.agent import FeiyangAgent, load_system_prompt
    from backend.sniper_engine import SniperEngine

logger = logging.getLogger(__name__)


class BacktestRunner:
    """Manages a single background backtest job (one at a time)."""

    def __init__(self, root_dir):
        self.root_dir = root_dir
        self._lock = threading.Lock()
        self._thread = None
        self._cancel = threading.Event()
        self._state = {
            "running": False,
            "run_id": None,
            "progress_pct": 0.0,
            "step": 0,
            "total_steps": 0,
            "message": "空闲",
            "started_at": None,
            "finished_at": None,
            "error": None,
        }
        self._result = None

    # ---------------- public API ----------------
    def status(self):
        with self._lock:
            return dict(self._state)

    def result(self):
        with self._lock:
            return self._result

    def stop(self):
        if not self.status().get("running"):
            return {"status": "error", "message": "当前没有运行中的回测任务"}
        self._cancel.set()
        return {"status": "success", "message": "停止指令已下发，将在当前步骤收尾后终止..."}

    def start(self, symbol, days=14, step_hours=4, max_llm_calls=60,
              initial_balance=10000.0, analyzer=None, fetcher=None):
        with self._lock:
            if self._state["running"]:
                return {"status": "error", "message": "已有回测任务正在运行中，请先等待完成或停止"}
            run_id = uuid.uuid4().hex[:8]
            self._cancel.clear()
            self._result = None
            self._state.update({
                "running": True,
                "run_id": run_id,
                "progress_pct": 0.0,
                "step": 0,
                "total_steps": 0,
                "message": "初始化回测任务...",
                "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "finished_at": None,
                "error": None,
            })
        self._thread = threading.Thread(
            target=self._run_guarded,
            args=(symbol, days, step_hours, max_llm_calls, initial_balance, analyzer, fetcher),
            daemon=True,
        )
        self._thread.start()
        return {"status": "success", "run_id": run_id}

    # ---------------- internals ----------------
    def _set_progress(self, step, total, message):
        with self._lock:
            self._state.update({
                "step": step,
                "total_steps": total,
                "progress_pct": round(step / max(total, 1) * 100.0, 1),
                "message": message,
            })

    def _run_guarded(self, *args):
        try:
            self._run(*args)
        except Exception as e:
            logger.error(f"[Backtest] Run failed: {e}")
            with self._lock:
                self._state.update({
                    "running": False,
                    "error": str(e),
                    "message": f"回测失败：{e}",
                    "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })

    def _load_yaml_cfg(self):
        try:
            from app import load_yaml_config
            return load_yaml_config()
        except Exception:
            try:
                from backend.app import load_yaml_config
                return load_yaml_config()
            except Exception:
                return {}

    def _build_default_analyzer(self, yaml_cfg):
        load_dotenv(override=True)
        api_key = os.getenv("OPENAI_API_KEY")
        api_base = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
        if not api_key:
            raise ValueError("未配置大模型 API Key，请先在设置面板填写并保存。")
        llm_cfg = yaml_cfg.get("llm", {})
        agent = FeiyangAgent(
            api_key=api_key,
            api_base=api_base,
            model_name=llm_cfg.get("model", "gpt-4o"),
            temperature=llm_cfg.get("temperature", 0.1),
            max_tokens=llm_cfg.get("max_tokens", 3000),
            system_prompt=load_system_prompt(self.root_dir),
        )
        return agent.analyze

    def _run(self, symbol, days, step_hours, max_llm_calls, initial_balance, analyzer, fetcher):
        days = max(1, min(int(days), 90))
        step_hours = max(1, min(int(step_hours), 24))
        max_llm_calls = max(1, min(int(max_llm_calls), 500))
        initial_balance = float(initial_balance) if float(initial_balance) > 0 else 10000.0

        yaml_cfg = self._load_yaml_cfg()
        exchange_id = yaml_cfg.get("exchange", "binance")
        timeframes = yaml_cfg.get("timeframes", ["1M", "1W", "1D", "4h", "1h"])
        fib_lookback = yaml_cfg.get("fibonacci", {}).get("lookback_days", 100)

        if analyzer is None:
            self._set_progress(0, 1, "正在初始化大模型客户端...")
            analyzer = self._build_default_analyzer(yaml_cfg)
        fetcher = fetcher or get_data_fetcher(exchange_id)

        # 1. Fetch historical klines for every timeframe in ONE pass.
        #    Each replay point slices these frames by timestamp.
        self._set_progress(0, 1, f"正在拉取 {symbol} 全周期历史 K 线（约 {days} 天 + 预热）...")
        limits = {
            "1h": min(1000, 200 + days * 24),
            "4h": min(1000, 200 + days * 6),
            "1D": min(1000, fib_lookback + days + 20),
            "1W": 200,
            "1M": 60,
        }
        raw = {}
        for tf in timeframes:
            raw[tf] = fetcher.fetch_ohlcv(symbol, tf, limit=limits.get(tf, 300))
        if "1h" not in raw or raw["1h"] is None or raw["1h"].empty:
            raise ValueError("回测必须包含 1h 周期 K 线数据")

        df1h = raw["1h"]
        window_bars = days * 24
        warmup = min(200, len(df1h) - window_bars - 1)
        if warmup < 60:
            raise ValueError(f"历史数据不足以支撑 {days} 天回测（1h K 线仅 {len(df1h)} 根）")
        start_idx = len(df1h) - window_bars
        if start_idx < 0:
            start_idx = 0
        total_bars = len(df1h) - start_idx

        # 2. Create an isolated simulation engine reusing production logic
        sim_dir = tempfile.mkdtemp(prefix=f"feiyang_bt_{uuid.uuid4().hex[:6]}_")
        engine = SniperEngine(sim_dir)
        engine._send_notification = lambda *a, **k: None  # never push during simulation
        engine.update_config({
            "mode": "paper",
            "paper_account_balance": initial_balance,
            "initial_balance": initial_balance,
            "circuit_breaker_enabled": False,   # real-time clock based
            "pending_ttl_hours": 0,             # simulated time axis
        })

        # 3. Walk forward bar by bar
        llm_calls = 0
        llm_exhausted = False
        self._set_progress(0, total_bars, "开始逐根 K 线回放历史行情...")

        for offset in range(total_bars):
            if self._cancel.is_set():
                logger.info("[Backtest] Cancellation requested, stopping at bar offset", exc_info=False)
                break

            idx = start_idx + offset
            bar = df1h.iloc[idx]
            bar_ts = int(bar["timestamp"])
            bar_high = float(bar.get("high", bar["close"]))
            bar_low = float(bar.get("low", bar["close"]))
            price = float(bar["close"])

            # 3a. Drive the simulation engine with this tick (high/low/close fill & stop check)
            engine.check_market_prices({
                symbol: {
                    "high": bar_high,
                    "low": bar_low,
                    "close": price
                }
            })

            # 3b. Signal point every step_hours bars
            is_signal_point = (offset % step_hours == 0)
            if is_signal_point and not llm_exhausted:
                if llm_calls >= max_llm_calls:
                    llm_exhausted = True
                    logger.info(f"[Backtest] LLM call budget ({max_llm_calls}) exhausted; price simulation continues without new signals.")
                else:
                    try:
                        payload = self._build_payload_at(raw, timeframes, bar_ts, fib_lookback, symbol)
                        if payload is not None:
                            json_signal, _report = analyzer(payload)
                            llm_calls += 1
                            engine.process_new_signal(symbol, price, json_signal, _report)
                    except Exception as e:
                        logger.warning(f"[Backtest] Signal point failed at ts={bar_ts}: {e}")

            if offset % 6 == 0 or offset == total_bars - 1:
                bar_dt = datetime.fromtimestamp(bar_ts / 1000, tz=timezone.utc).strftime("%m-%d %H:%M")
                self._set_progress(
                    offset + 1, total_bars,
                    f"回放中 {bar_dt} UTC | 价 ${price} | LLM 诊断 {llm_calls} 次"
                )

        # 4. Force-close any residual open positions at the final close so
        #    statistics reflect a complete run (marked as backtest settlement)
        final_price = float(df1h.iloc[-1]["close"])
        self._force_close_open_trades(engine, symbol, final_price)

        # 5. Assemble the result
        stats = engine.get_dashboard_data()
        trades = engine.get_trades()
        equity_curve = self._full_equity_curve(trades, stats["account_balance"], stats["net_profit_usd"])

        final_balance = stats["account_balance"]
        self._result = {
            "run_id": self._state["run_id"],
            "symbol": symbol,
            "days": days,
            "step_hours": step_hours,
            "llm_calls_used": llm_calls,
            "llm_budget_exhausted": llm_exhausted,
            "cancelled": self._cancel.is_set(),
            "initial_balance": initial_balance,
            "final_balance": final_balance,
            "net_profit_usd": stats["net_profit_usd"],
            "net_profit_percent": round((final_balance - initial_balance) / initial_balance * 100.0, 2),
            "win_rate": stats["win_rate"],
            "profit_factor": stats["profit_factor"],
            "total_trades_count": stats["total_trades_count"],
            "winning_trades_count": stats["winning_trades_count"],
            "losing_trades_count": stats["losing_trades_count"],
            "max_drawdown_usd": stats["max_drawdown_usd"],
            "max_drawdown_percent": stats["max_drawdown_percent"],
            "total_fees_usd": stats["total_fees_usd"],
            "equity_curve": equity_curve,
            "trades": trades[:100],
            "simulation_notes": [
                "价格刻度为 1h K 线收盘价：小时内插针不可见，成交/止损按收盘价评估",
                "资金费（8 小时）基于真实时钟，回测时间轴上未计入",
                "已完整计入：限价成交、TP1 半仓保本、双保险止损、手续费与滑点、杠杆安全帽",
            ],
            "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        with self._lock:
            self._state.update({
                "running": False,
                "progress_pct": 100.0 if not self._cancel.is_set() else self._state["progress_pct"],
                "message": "回测完成 ✅" if not self._cancel.is_set() else "回测已手动停止 ⏹",
                "finished_at": self._result["finished_at"],
            })
        logger.info(f"[Backtest] Done: {symbol} {days}d, net ${stats['net_profit_usd']}, win {stats['win_rate']}%, LLM calls {llm_calls}")

    # ---------------- helpers ----------------
    def _build_payload_at(self, raw, timeframes, bar_ts, fib_lookback, symbol):
        """Slice every timeframe up to bar_ts and build the production payload."""
        processed = {}
        for tf in timeframes:
            df = raw.get(tf)
            if df is None or df.empty:
                continue
            sliced = df[df["timestamp"] <= bar_ts].tail(200)
            if len(sliced) < 30:
                return None
            # clean_and_compress needs the 'datetime' column; DataFetcher adds
            # it, but stay robust for any fetcher implementation
            if "datetime" not in sliced.columns:
                sliced = sliced.copy()
                sliced["datetime"] = pd.to_datetime(sliced["timestamp"], unit="ms")
            processed[tf] = calculate_indicators(sliced)
        daily = processed.get("1D")
        if daily is None or daily.empty:
            return None
        fib_levels = calculate_fibonacci_levels(daily, lookback=fib_lookback)
        return clean_and_compress(processed, fib_levels, symbol)

    def _force_close_open_trades(self, engine, symbol, final_price):
        """Mark residual open trades as closed at the final close for clean stats."""
        trades = engine.state.get("trades", [])
        changed = False
        for t in trades:
            if t.get("status") == "pending":
                t["status"] = "cancelled"
                t["close_reason"] = "回测结束，未成交挂单自动清算"
                changed = True
            elif t.get("status") in ("filled", "tp1_hit"):
                sig_type = t["signal_type"]
                entry = t.get("actual_entry") or t["planned_entry"]
                lev = t["leverage"]
                margin = t["margin_usd"]
                rem = 0.5 if t.get("tp1_partial_closed") else 1.0
                if sig_type == "long":
                    pct = (final_price - entry) / entry * lev
                else:
                    pct = (entry - final_price) / entry * lev
                taker_fee, _, _ = engine._fee_rates()
                fee = engine._record_fee(t, t["position_size_usd"] * rem, taker_fee)
                leg_net = round(margin * rem * pct - fee, 2)
                t["pnl_usd"] = round(t.get("pnl_usd", 0.0) + leg_net, 2)
                t["pnl_percent"] = round((t["pnl_usd"] / margin) * 100.0, 2)
                t["status"] = "closed_tp" if t["pnl_usd"] >= 0 else "closed_sl"
                t["closed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                t["close_reason"] = "回测结束，按末尾收盘价强制清算"
                cfg = engine.state.get("config", {})
                cfg["paper_account_balance"] = round(cfg.get("paper_account_balance", 0.0) + leg_net, 2)
                changed = True
        if changed:
            engine._save_state()

    def _full_equity_curve(self, trades, final_balance, total_pnl):
        closed = [t for t in trades if t.get("status") in ("closed_tp", "closed_sl")]
        closed.sort(key=lambda x: x.get("closed_at") or "")
        equity = final_balance - total_pnl
        curve = [{"t": "初始本金", "equity": round(equity, 2)}]
        for t in closed:
            equity += t.get("pnl_usd", 0.0)
            curve.append({"t": t.get("closed_at"), "equity": round(equity, 2)})
        return curve
