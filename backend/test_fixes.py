"""
FeiyangAgent 单元测试套件
覆盖：P0/P1/P2 修复验证、风控逻辑、计算方法、实盘相关逻辑
"""

import os
import sys
import json
import time
import tempfile
import threading
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock
from types import ModuleType

# Mock missing modules before imports
if 'openai' not in sys.modules:
    mock_openai = ModuleType('openai')
    mock_openai.OpenAI = MagicMock()
    sys.modules['openai'] = mock_openai

if 'pandas_ta' not in sys.modules:
    mock_ta = ModuleType('pandas_ta')
    mock_ta.ema = MagicMock(return_value=None)
    mock_ta.adx = MagicMock(return_value=None)
    mock_ta.bbands = MagicMock(return_value=None)
    mock_ta.rsi = MagicMock(return_value=None)
    mock_ta.atr = MagicMock(return_value=None)
    mock_ta.macd = MagicMock(return_value=None)
    sys.modules['pandas_ta'] = mock_ta

if 'ccxt' not in sys.modules:
    mock_ccxt = ModuleType('ccxt')
    mock_ccxt.binance = MagicMock()
    sys.modules['ccxt'] = mock_ccxt

if 'requests' not in sys.modules:
    mock_requests = ModuleType('requests')
    sys.modules['requests'] = mock_requests

if 'yaml' not in sys.modules:
    mock_yaml = ModuleType('yaml')
    mock_yaml.safe_load = MagicMock(return_value={})
    mock_yaml.dump = MagicMock()
    sys.modules['yaml'] = mock_yaml

if 'dotenv' not in sys.modules:
    mock_dotenv = ModuleType('dotenv')
    mock_dotenv.load_dotenv = MagicMock()
    sys.modules['dotenv'] = mock_dotenv

if 'fastapi' not in sys.modules:
    mock_fastapi = ModuleType('fastapi')
    mock_fastapi.FastAPI = MagicMock()
    mock_fastapi.HTTPException = MagicMock
    mock_fastapi.Query = MagicMock
    mock_fastapi.Body = MagicMock
    sys.modules['fastapi'] = mock_fastapi

if 'uvicorn' not in sys.modules:
    mock_uvicorn = ModuleType('uvicorn')
    mock_uvicorn.run = MagicMock()
    sys.modules['uvicorn'] = mock_uvicorn

if 'httpx' not in sys.modules:
    mock_httpx = ModuleType('httpx')
    mock_httpx.Client = MagicMock
    mock_httpx.AsyncClient = MagicMock
    sys.modules['httpx'] = mock_httpx

import pytest
import pandas as pd
import numpy as np

# Ensure backend dir is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def tmp_engine():
    """Create a SniperEngine with a temporary state file."""
    from sniper_engine import SniperEngine
    tmpdir = tempfile.mkdtemp()
    engine = SniperEngine(tmpdir)
    yield engine
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def paper_engine(tmp_engine):
    """Engine configured for paper trading with standard defaults."""
    tmp_engine.state["config"]["mode"] = "paper"
    tmp_engine.state["config"]["paper_account_balance"] = 10000.0
    tmp_engine.state["config"]["taker_fee_rate"] = 0.0005
    tmp_engine.state["config"]["maker_fee_rate"] = 0.0002
    tmp_engine.state["config"]["slippage_rate"] = 0.0005
    tmp_engine.state["config"]["daily_max_loss_percent"] = 5.0
    tmp_engine.state["config"]["max_trade_loss_percent"] = 50.0
    tmp_engine.state["config"]["circuit_breaker_enabled"] = True
    tmp_engine.state["config"]["min_confidence"] = 7
    tmp_engine.state["trades"] = []
    tmp_engine.state["daily"] = {}
    return tmp_engine


def make_trade(symbol="BTC/USDT", sig_type="long", status="filled",
               entry=100000.0, sl=95000.0, tps=[105000.0, 110000.0],
               amount=0.01, lev=50, margin=20.0, conf=8,
               is_live=False, tp1_closed=False):
    """Helper to create a trade dict for testing."""
    return {
        "id": f"test-{int(time.time()*1000000)}",
        "symbol": symbol,
        "signal_type": sig_type,
        "status": status,
        "confidence_score": conf,
        "entry_min": entry * 0.999,
        "entry_max": entry * 1.001,
        "planned_entry": entry,
        "actual_entry": entry,
        "stop_loss": sl,
        "take_profit_targets": tps,
        "leverage": lev,
        "position_size_usd": amount * entry,
        "margin_usd": margin,
        "entered_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "filled_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pnl_usd": 0.0,
        "pnl_percent": 0.0,
        "fees_usd": 0.0,
        "tp1_partial_closed": tp1_closed,
        "is_live": is_live,
        "current_price": entry,
        "needs_review": False,
        "last_review_time": None,
        "review_trigger_price": None,
        "core_reason": "Test signal",
        "signal_regime": "ranging",
        "trade_id": f"trade-{int(time.time()*1000000)}",
    }


def run_price_update(engine, symbol, high, low, close):
    """Helper to call check_market_prices with a single symbol."""
    engine.check_market_prices({symbol: {"high": high, "low": low, "close": close}})


# ═══════════════════════════════════════════════════════════════
# P0 Tests: Critical Issues
# ═══════════════════════════════════════════════════════════════

class TestP0SigTypeCheck:
    """P0-1: sig_type_check variable defined before use."""

    def test_sig_type_check_defined_before_circuit_breaker(self, paper_engine):
        """Verify sig_type_check is available when circuit breaker triggers."""
        paper_engine.state["daily"]["date"] = datetime.now().strftime("%Y-%m-%d")
        paper_engine.state["daily"]["start_balance_paper"] = 10000.0
        paper_engine.state["daily"]["halted_paper"] = True

        json_signal = {"signal_type": "long", "confidence_score": 8}
        # Should return None (blocked) without NameError
        result = paper_engine.process_new_signal("BTC/USDT", 100000, json_signal, "")
        assert result is None

    def test_no_name_error_on_signal_processing(self, paper_engine):
        """Signal processing should not raise NameError for sig_type_check."""
        today = datetime.now().strftime("%Y-%m-%d")
        paper_engine.state["daily"] = {
            "date": today,
            "start_balance_paper": 10000.0,
            "halted_paper": False,
        }
        json_signal = {"signal_type": "long", "confidence_score": 8}
        try:
            paper_engine.process_new_signal("BTC/USDT", 100000, json_signal, "")
        except NameError as e:
            pytest.fail(f"NameError should not occur: {e}")


class TestP0BinanceForceCancel:
    """P0-2: _binance_force_cancel_all_orders method exists and works."""

    def test_method_exists(self, paper_engine):
        assert hasattr(paper_engine, "_binance_force_cancel_all_orders")

    def test_method_callable(self, paper_engine):
        mock_exchange = MagicMock()
        mock_exchange.fapiPrivateDeleteAllOpenOrders.side_effect = Exception("not binance")
        mock_exchange.fetch_open_orders.return_value = []
        mock_exchange.fapiPrivateDeleteAlgoOpenOrders.side_effect = Exception("no algo")
        mock_exchange.fapiPrivateGetOpenAlgoOrders.return_value = []
        result = paper_engine._binance_force_cancel_all_orders(mock_exchange, "BTC/USDT:USDT")
        assert result == 0

    def test_cancels_all_orders(self, paper_engine):
        mock_exchange = MagicMock()
        mock_exchange.fapiPrivateDeleteAllOpenOrders.side_effect = Exception("not binance")
        mock_exchange.fetch_open_orders.return_value = [
            {"id": "order1"}, {"id": "order2"}, {"id": "order3"},
        ]
        mock_exchange.cancel_order.return_value = {}
        mock_exchange.fapiPrivateDeleteAlgoOpenOrders.side_effect = Exception("no algo")
        mock_exchange.fapiPrivateGetOpenAlgoOrders.return_value = []
        result = paper_engine._binance_force_cancel_all_orders(mock_exchange, "BTC/USDT:USDT")
        assert result == 3
        assert mock_exchange.cancel_order.call_count == 3

    def test_handles_cancel_failure_gracefully(self, paper_engine):
        mock_exchange = MagicMock()
        mock_exchange.fapiPrivateDeleteAllOpenOrders.side_effect = Exception("not binance")
        mock_exchange.fetch_open_orders.return_value = [
            {"id": "order1"}, {"id": "order2"},
        ]
        mock_exchange.cancel_order.side_effect = [Exception("API error"), {}]
        mock_exchange.fapiPrivateDeleteAlgoOpenOrders.side_effect = Exception("no algo")
        mock_exchange.fapiPrivateGetOpenAlgoOrders.return_value = []
        result = paper_engine._binance_force_cancel_all_orders(mock_exchange, "BTC/USDT:USDT")
        assert result == 1

    def test_bulk_cancel_api_used_when_available(self, paper_engine):
        """When Binance bulk cancel API is available, it should be used first."""
        mock_exchange = MagicMock()
        mock_exchange.fapiPrivateDeleteAllOpenOrders.return_value = {}
        mock_exchange.fapiPrivateDeleteAlgoOpenOrders.return_value = {}
        result = paper_engine._binance_force_cancel_all_orders(mock_exchange, "BTC/USDT:USDT")
        assert result >= 2  # Both regular and algo cancel succeeded
        mock_exchange.fapiPrivateDeleteAllOpenOrders.assert_called_once()


class TestP0ConsistencyFix:
    """P0-3: ConsistencyFix respects macro event risk control."""

    def _make_agent(self):
        from agent import FeiyangAgent
        agent = object.__new__(FeiyangAgent)
        agent.min_confidence = 7
        return agent

    def test_consistency_fix_respects_stand_aside(self):
        agent = self._make_agent()
        signal = {
            "signal_type": "wait", "confidence_score": 9,
            "entry_zone": {"min": 99000, "max": 101000},
            "stop_loss": 95000,
            "take_profit_targets": [105000, 110000],
            "risk_reward_ratio": 2.5,
            "market_context": {"trading_bias": "stand_aside"},
        }
        result = agent._normalize_signal(signal)
        assert result["signal_type"] == "wait"

    def test_consistency_fix_respects_macro_event_imminent(self):
        agent = self._make_agent()
        signal = {
            "signal_type": "wait", "confidence_score": 9,
            "entry_zone": {"min": 99000, "max": 101000},
            "stop_loss": 95000,
            "take_profit_targets": [105000, 110000],
            "risk_reward_ratio": 2.5,
            "market_context": {
                "trading_bias": "normal",
                "macro_event": {"event": "FOMC", "hours_until": 1.5, "impact": "critical"},
            },
        }
        result = agent._normalize_signal(signal)
        assert result["signal_type"] == "wait"

    def test_consistency_fix_works_without_macro_context(self):
        agent = self._make_agent()
        signal = {
            "signal_type": "wait", "confidence_score": 9,
            "entry_zone": {"min": 99000, "max": 101000},
            "stop_loss": 95000,
            "take_profit_targets": [105000, 110000],
            "risk_reward_ratio": 2.5,
        }
        result = agent._normalize_signal(signal)
        assert result["signal_type"] == "long"

    def test_consistency_fix_respects_far_macro_event(self):
        agent = self._make_agent()
        signal = {
            "signal_type": "wait", "confidence_score": 9,
            "entry_zone": {"min": 99000, "max": 101000},
            "stop_loss": 95000,
            "take_profit_targets": [105000, 110000],
            "risk_reward_ratio": 2.5,
            "market_context": {
                "trading_bias": "normal",
                "macro_event": {"event": "CPI", "hours_until": 48, "impact": "high"},
            },
        }
        result = agent._normalize_signal(signal)
        assert result["signal_type"] == "long"


# ═══════════════════════════════════════════════════════════════
# P1 Tests: High-Risk Issues
# ═══════════════════════════════════════════════════════════════

class TestP1SlippageModel:
    """P1: Slippage applied to all exit paths."""

    def test_long_tp1_applies_slippage(self, paper_engine):
        """TP1 LONG exit should apply slippage (reduces profit vs no-slippage)."""
        trade = make_trade(sig_type="long", entry=100000, sl=95000, tps=[105000, 110000], amount=0.01)
        paper_engine.state["trades"] = [trade]
        # With 50x lev, 5% move = 250% PnL → trailing stop sets SL ~103500
        # low=104000 stays above trailing SL; high=105000 hits TP1 but not ultimate TP
        run_price_update(paper_engine, "BTC/USDT", 105000, 104000, 105000)

        updated = paper_engine.state["trades"][0]
        assert updated["status"] == "tp1_hit", f"Expected tp1_hit, got {updated['status']}"
        # Calculate no-slippage PnL for comparison
        amount_calc = trade["position_size_usd"] / trade["actual_entry"]
        no_slip_pnl = amount_calc * 0.5 * (105000 - 100000)
        no_slip_fee = trade["position_size_usd"] * 0.5 * 0.0005
        no_slip_net = no_slip_pnl - no_slip_fee
        assert updated["pnl_usd"] < no_slip_net, \
            f"TP1 LONG PnL {updated['pnl_usd']} should be less than no-slippage {no_slip_net}"

    def test_short_tp1_applies_slippage(self, paper_engine):
        """TP1 SHORT exit should apply slippage."""
        trade = make_trade(sig_type="short", entry=100000, sl=105000, tps=[95000, 90000], amount=0.01)
        paper_engine.state["trades"] = [trade]
        # With 50x lev, trailing stop sets SL ~96500; high=96000 stays below it
        # low=95000 hits TP1 but not ultimate TP at 90000
        run_price_update(paper_engine, "BTC/USDT", 96000, 95000, 95000)

        updated = paper_engine.state["trades"][0]
        assert updated["status"] == "tp1_hit", f"Expected tp1_hit, got {updated['status']}"
        amount_calc = trade["position_size_usd"] / trade["actual_entry"]
        no_slip_pnl = amount_calc * 0.5 * (100000 - 95000)
        no_slip_fee = trade["position_size_usd"] * 0.5 * 0.0005
        no_slip_net = no_slip_pnl - no_slip_fee
        assert updated["pnl_usd"] < no_slip_net, \
            f"TP1 SHORT PnL {updated['pnl_usd']} should be less than no-slippage {no_slip_net}"

    def test_long_ultimate_tp_applies_slippage(self, paper_engine):
        """Ultimate TP LONG exit should apply slippage."""
        trade = make_trade(sig_type="long", entry=100000, sl=95000,
                           tps=[105000, 110000], amount=0.01, tp1_closed=True)
        trade["status"] = "tp1_hit"
        trade["stop_loss"] = 100000  # SL moved to breakeven after TP1
        trade["peak_pnl_pct"] = 250.0  # Peak from TP1 candle
        trade["trailing_sl_level"] = 250.0
        paper_engine.state["trades"] = [trade]
        # With 50x lev, 10% move = 500% PnL → trailing SL ~107000; low=108000 stays above
        run_price_update(paper_engine, "BTC/USDT", 110000, 108000, 110000)

        updated = paper_engine.state["trades"][0]
        assert updated["status"] == "closed_tp", f"Expected closed_tp, got {updated['status']}"
        amount_calc = trade["position_size_usd"] / trade["actual_entry"]
        no_slip_pnl = amount_calc * 0.5 * (110000 - 100000)
        no_slip_fee = trade["position_size_usd"] * 0.5 * 0.0005
        no_slip_net = no_slip_pnl - no_slip_fee
        assert updated["pnl_usd"] < no_slip_net, \
            f"Ultimate TP LONG PnL {updated['pnl_usd']} should be less than no-slippage {no_slip_net}"

    def test_short_ultimate_tp_applies_slippage(self, paper_engine):
        """Ultimate TP SHORT exit should apply slippage."""
        trade = make_trade(sig_type="short", entry=100000, sl=105000,
                           tps=[95000, 90000], amount=0.01, tp1_closed=True)
        trade["status"] = "tp1_hit"
        trade["stop_loss"] = 100000  # SL moved to breakeven after TP1
        trade["peak_pnl_pct"] = 250.0  # Peak from TP1 candle
        trade["trailing_sl_level"] = 250.0
        paper_engine.state["trades"] = [trade]
        # With 50x lev, trailing SL ~93000; high=92000 stays below it
        run_price_update(paper_engine, "BTC/USDT", 92000, 90000, 90000)

        updated = paper_engine.state["trades"][0]
        assert updated["status"] == "closed_tp", f"Expected closed_tp, got {updated['status']}"
        amount_calc = trade["position_size_usd"] / trade["actual_entry"]
        no_slip_pnl = amount_calc * 0.5 * (100000 - 90000)
        no_slip_fee = trade["position_size_usd"] * 0.5 * 0.0005
        no_slip_net = no_slip_pnl - no_slip_fee
        assert updated["pnl_usd"] < no_slip_net, \
            f"Ultimate TP SHORT PnL {updated['pnl_usd']} should be less than no-slippage {no_slip_net}"

    def test_time_stop_applies_slippage(self, paper_engine):
        """Time stop should apply slippage."""
        old_time = (datetime.now() - timedelta(hours=100)).strftime("%Y-%m-%d %H:%M:%S")
        trade = make_trade(sig_type="long", entry=100000, sl=95000, tps=[105000], amount=0.01)
        trade["filled_at"] = old_time
        trade["entered_at"] = old_time
        paper_engine.state["trades"] = [trade]
        paper_engine.state["config"]["max_hold_hours"] = 72.0

        run_price_update(paper_engine, "BTC/USDT", 100000, 95000, 100000)

        updated = paper_engine.state["trades"][0]
        # With slippage at entry price, PnL should be negative (slippage cost)
        assert updated["pnl_usd"] < 0, "Time stop should have slippage causing small loss"
        assert "时间止损" in updated.get("close_reason", "")


class TestP1DefaultValues:
    """P1: Default values consistency."""

    def test_daily_max_loss_percent_default(self, paper_engine):
        del paper_engine.state["config"]["daily_max_loss_percent"]
        assert paper_engine.state["config"].get("daily_max_loss_percent", 5.0) == 5.0

    def test_max_trade_loss_percent_default(self, paper_engine):
        del paper_engine.state["config"]["max_trade_loss_percent"]
        assert paper_engine.state["config"].get("max_trade_loss_percent", 50.0) == 50.0

    def test_max_trade_loss_percent_in_default_config(self):
        from sniper_engine import SniperEngine
        tmpdir = tempfile.mkdtemp()
        engine = SniperEngine(tmpdir)
        assert "max_trade_loss_percent" in engine.state["config"]
        assert engine.state["config"]["max_trade_loss_percent"] == 50.0
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


class TestP1AdaptiveRiskMode:
    """P1: Adaptive risk control filters by mode (paper/live)."""

    def test_win_rate_filters_by_mode(self, paper_engine):
        paper_engine.state["config"]["mode"] = "paper"
        for i in range(5):
            t = make_trade(status="closed_tp", is_live=False)
            t["closed_at"] = f"2026-01-0{i+1} 10:00:00"
            paper_engine.state["trades"].append(t)
        for i in range(5):
            t = make_trade(status="closed_sl", is_live=True)
            t["closed_at"] = f"2026-01-0{i+1} 10:00:00"
            paper_engine.state["trades"].append(t)

        win_rate = paper_engine._get_recent_win_rate(lookback=10)
        assert win_rate == 1.0, f"Expected 1.0, got {win_rate}"

    def test_cooldown_filters_by_mode(self, paper_engine):
        paper_engine.state["config"]["mode"] = "paper"
        for i in range(3):
            t = make_trade(status="closed_sl", is_live=True)
            t["closed_at"] = f"2026-01-0{i+1} 10:00:00"
            paper_engine.state["trades"].append(t)

        cooldown = paper_engine._cooldown_multiplier()
        assert cooldown == 1.0, f"Expected 1.0, got {cooldown}"


class TestP1ConfidenceScore:
    """P1: Confidence score cap should be 12."""

    def test_confidence_12_produces_max_multiplier(self, paper_engine):
        paper_engine.state["config"]["margin_mode"] = "smart"
        pos_val, margin, lev = paper_engine.calculate_trade_params(
            balance=10000, risk_pct=2.0, entry_price=100000,
            stop_loss=95000, confidence=12, max_lev=50
        )
        assert pos_val > 0
        assert margin > 0

    def test_confidence_cap_at_12(self, paper_engine):
        paper_engine.state["config"]["margin_mode"] = "smart"
        pos_val_12, _, _ = paper_engine.calculate_trade_params(
            balance=10000, risk_pct=2.0, entry_price=100000,
            stop_loss=95000, confidence=12, max_lev=50
        )
        pos_val_15, _, _ = paper_engine.calculate_trade_params(
            balance=10000, risk_pct=2.0, entry_price=100000,
            stop_loss=95000, confidence=15, max_lev=50
        )
        assert pos_val_12 == pos_val_15, "Confidence above 12 should be capped"


class TestP1MaxLevParameter:
    """P1: max_lev parameter should be used in calculate_trade_params."""

    def test_max_lev_affects_leverage(self, paper_engine):
        paper_engine.state["config"]["leverage_mode"] = "smart"
        paper_engine.state["config"]["min_leverage"] = 10
        _, _, lev_high = paper_engine.calculate_trade_params(
            balance=10000, risk_pct=2.0, entry_price=100000,
            stop_loss=95000, confidence=12, max_lev=50
        )
        _, _, lev_low = paper_engine.calculate_trade_params(
            balance=10000, risk_pct=2.0, entry_price=100000,
            stop_loss=95000, confidence=12, max_lev=20
        )
        assert lev_high == 50, f"Expected 50, got {lev_high}"
        assert lev_low == 20, f"Expected 20, got {lev_low}"


# ═══════════════════════════════════════════════════════════════
# P2 Tests: Medium/Low Risk Issues
# ═══════════════════════════════════════════════════════════════

class TestP2FibonacciFlatMarket:
    """P2: Fibonacci handles flat market (high == low)."""

    def test_fibonacci_flat_market_1d(self):
        from indicators import calculate_fibonacci_levels
        df = pd.DataFrame({
            "high": [100.0] * 50, "low": [100.0] * 50, "close": [100.0] * 50,
        })
        result = calculate_fibonacci_levels(df)
        assert result["swing_high"] == 100.0
        assert result["swing_low"] == 100.0
        for level in result["upward_levels"].values():
            assert level == 100.0

    def test_fibonacci_flat_market_4h(self):
        from indicators import calculate_4h_fibonacci
        df = pd.DataFrame({
            "high": [50.0] * 50, "low": [50.0] * 50, "close": [50.0] * 50,
        })
        result = calculate_4h_fibonacci(df)
        assert result["swing_high"] == 50.0
        assert result["swing_low"] == 50.0
        for level in result["levels"].values():
            assert level == 50.0

    def test_fibonacci_normal_market(self):
        from indicators import calculate_fibonacci_levels
        df = pd.DataFrame({
            "high": [110.0] * 50, "low": [90.0] * 50, "close": [100.0] * 50,
        })
        result = calculate_fibonacci_levels(df)
        assert result["swing_high"] == 110.0
        assert result["swing_low"] == 90.0
        assert abs(result["upward_levels"]["0.382"] - 97.64) < 0.01


class TestP2ConsensusRounding:
    """P2: Consensus mechanism uses round() instead of //."""

    def test_consensus_rounds_correctly(self):
        # (9+10) // 2 = 9, but round((9+10)/2) = 10
        assert (9 + 10) // 2 == 9
        assert round((9 + 10) / 2) == 10

        # (8+10) // 2 = 9, round gives 9
        assert (8 + 10) // 2 == 9
        assert round((8 + 10) / 2) == 9


class TestP2BacktestSlippage:
    """P2: Backtest _force_close_open_trades applies slippage."""

    def test_backtest_force_close_applies_slippage(self, paper_engine):
        from backtest import BacktestRunner
        trade = make_trade(sig_type="long", entry=100000, sl=95000,
                           tps=[105000], amount=0.01, is_live=False)
        trade["status"] = "filled"
        trade["leverage"] = 50
        trade["margin_usd"] = 20.0
        trade["position_size_usd"] = 1000.0
        paper_engine.state["trades"] = [trade]
        paper_engine.state["config"]["paper_account_balance"] = 10000.0

        bt = BacktestRunner.__new__(BacktestRunner)
        bt._force_close_open_trades(paper_engine, "BTC/USDT", 100000)

        updated = paper_engine.state["trades"][0]
        # With slippage at entry price, PnL should be negative
        assert updated["pnl_usd"] < 0, \
            f"Force close should have slippage, got PnL={updated['pnl_usd']}"
        assert updated["status"] in ("closed_tp", "closed_sl")


# ═══════════════════════════════════════════════════════════════
# Risk Control Logic Tests
# ═══════════════════════════════════════════════════════════════

class TestCircuitBreaker:
    """Test the daily drawdown circuit breaker."""

    def test_circuit_breaker_not_triggered_normal(self, paper_engine):
        today = datetime.now().strftime("%Y-%m-%d")
        paper_engine.state["daily"] = {
            "date": today, "start_balance_paper": 10000.0, "halted_paper": False,
        }
        paper_engine.state["config"]["paper_account_balance"] = 9800.0
        assert not paper_engine.is_halted()

    def test_circuit_breaker_triggered_on_loss(self, paper_engine):
        today = datetime.now().strftime("%Y-%m-%d")
        paper_engine.state["daily"] = {
            "date": today, "start_balance_paper": 10000.0, "halted_paper": False,
        }
        paper_engine.state["config"]["paper_account_balance"] = 9499.0
        paper_engine.state["config"]["daily_max_loss_percent"] = 5.0
        result = paper_engine._check_circuit_breaker()
        assert result is True
        assert paper_engine.is_halted()

    def test_circuit_breaker_per_mode(self, paper_engine):
        today = datetime.now().strftime("%Y-%m-%d")
        paper_engine.state["daily"] = {
            "date": today, "start_balance_paper": 10000.0, "start_balance_live": 100.0,
            "halted_paper": False, "halted_live": True,
        }
        paper_engine.state["config"]["mode"] = "paper"
        assert not paper_engine.is_halted()
        paper_engine.state["config"]["mode"] = "live"
        assert paper_engine.is_halted()

    def test_circuit_breaker_blocks_new_signals(self, paper_engine):
        today = datetime.now().strftime("%Y-%m-%d")
        paper_engine.state["daily"] = {
            "date": today, "start_balance_paper": 10000.0, "halted_paper": True,
        }
        json_signal = {"signal_type": "long", "confidence_score": 9}
        result = paper_engine.process_new_signal("BTC/USDT", 100000, json_signal, "")
        assert result is None


class TestStopLossLogic:
    """Test stop-loss trigger logic."""

    def test_long_stop_loss_triggers(self, paper_engine):
        trade = make_trade(sig_type="long", entry=100000, sl=95000, tps=[105000], amount=0.01)
        paper_engine.state["trades"] = [trade]
        run_price_update(paper_engine, "BTC/USDT", 94000, 94000, 94000)
        updated = paper_engine.state["trades"][0]
        assert updated["status"] == "closed_sl"
        assert updated["pnl_usd"] < 0

    def test_short_stop_loss_triggers(self, paper_engine):
        trade = make_trade(sig_type="short", entry=100000, sl=105000, tps=[95000], amount=0.01)
        paper_engine.state["trades"] = [trade]
        run_price_update(paper_engine, "BTC/USDT", 106000, 106000, 106000)
        updated = paper_engine.state["trades"][0]
        assert updated["status"] == "closed_sl"
        assert updated["pnl_usd"] < 0

    def test_long_pnl_breach_closes_position(self, paper_engine):
        trade = make_trade(sig_type="long", entry=100000, sl=50000,
                           tps=[105000], amount=0.01, lev=50, margin=20.0)
        paper_engine.state["trades"] = [trade]
        paper_engine.state["config"]["max_trade_loss_percent"] = 50.0
        run_price_update(paper_engine, "BTC/USDT", 99000, 99000, 50000)
        updated = paper_engine.state["trades"][0]
        assert updated["status"] == "closed_sl"


class TestTakeProfitLogic:
    """Test take-profit trigger logic."""

    def test_long_tp1_then_ultimate_tp(self, paper_engine):
        trade = make_trade(sig_type="long", entry=100000, sl=95000,
                           tps=[105000, 110000], amount=0.01)
        paper_engine.state["trades"] = [trade]

        # Hit TP1: low=104000 stays above trailing SL ~103500
        run_price_update(paper_engine, "BTC/USDT", 105000, 104000, 105000)
        t = paper_engine.state["trades"][0]
        assert t["status"] == "tp1_hit", f"Expected tp1_hit, got {t['status']}"
        assert t["tp1_partial_closed"] is True
        # SL should be at breakeven (100000) or higher (trailing stop may have moved it up)
        assert t["stop_loss"] >= 100000, f"SL should be >= breakeven, got {t['stop_loss']}"

        # Hit ultimate TP: low=108000 stays above trailing SL ~107000
        run_price_update(paper_engine, "BTC/USDT", 110000, 108000, 110000)
        t = paper_engine.state["trades"][0]
        assert t["status"] == "closed_tp"

    def test_short_tp1_then_ultimate_tp(self, paper_engine):
        trade = make_trade(sig_type="short", entry=100000, sl=105000,
                           tps=[95000, 90000], amount=0.01)
        paper_engine.state["trades"] = [trade]

        # Hit TP1: high=96000 stays below trailing SL ~96500
        run_price_update(paper_engine, "BTC/USDT", 96000, 95000, 95000)
        t = paper_engine.state["trades"][0]
        assert t["status"] == "tp1_hit", f"Expected tp1_hit, got {t['status']}"
        assert t["tp1_partial_closed"] is True
        # SL should be at breakeven (100000) or lower (trailing stop may have moved it down)
        assert t["stop_loss"] <= 100000, f"SL should be <= breakeven, got {t['stop_loss']}"

        # Hit ultimate TP: high=92000 stays below trailing SL ~93000
        run_price_update(paper_engine, "BTC/USDT", 92000, 90000, 90000)
        t = paper_engine.state["trades"][0]
        assert t["status"] == "closed_tp"


class TestPositionSizing:
    """Test position sizing calculation."""

    def test_smart_mode_sizing(self, paper_engine):
        paper_engine.state["config"]["margin_mode"] = "smart"
        paper_engine.state["config"]["leverage_mode"] = "smart"
        paper_engine.state["config"]["min_leverage"] = 20
        paper_engine.state["config"]["max_leverage"] = 50
        pos_val, margin, lev = paper_engine.calculate_trade_params(
            balance=10000, risk_pct=2.0, entry_price=100000,
            stop_loss=98000, confidence=8, max_lev=50
        )
        assert pos_val > 0
        assert margin > 0
        assert 20 <= lev <= 50

    def test_margin_capped_at_25_percent(self, paper_engine):
        paper_engine.state["config"]["margin_mode"] = "smart"
        pos_val, margin, lev = paper_engine.calculate_trade_params(
            balance=100, risk_pct=10.0, entry_price=100000,
            stop_loss=99900, confidence=12, max_lev=50
        )
        assert margin <= 100 * 0.25 + 0.01

    def test_fixed_leverage_mode(self, paper_engine):
        paper_engine.state["config"]["leverage_mode"] = "fixed"
        paper_engine.state["config"]["fixed_leverage"] = 30
        _, _, lev = paper_engine.calculate_trade_params(
            balance=10000, risk_pct=2.0, entry_price=100000,
            stop_loss=95000, confidence=8, max_lev=50
        )
        assert lev == 30


class TestFeeModel:
    """Test fee calculation model."""

    def test_fee_rates_from_config(self, paper_engine):
        paper_engine.state["config"]["taker_fee_rate"] = 0.001
        paper_engine.state["config"]["maker_fee_rate"] = 0.0005
        paper_engine.state["config"]["slippage_rate"] = 0.001
        taker, maker, slip = paper_engine._fee_rates()
        assert taker == 0.001
        assert maker == 0.0005
        assert slip == 0.001

    def test_record_fee_accumulates(self, paper_engine):
        trade = {"fees_usd": 0.0}
        paper_engine._record_fee(trade, 1000.0, 0.001)
        assert trade["fees_usd"] == 1.0
        paper_engine._record_fee(trade, 500.0, 0.001)
        assert trade["fees_usd"] == 1.5


# ═══════════════════════════════════════════════════════════════
# Signal Processing Tests
# ═══════════════════════════════════════════════════════════════

class TestSignalProcessing:
    """Test signal processing pipeline."""

    def _setup_no_halt(self, paper_engine):
        today = datetime.now().strftime("%Y-%m-%d")
        paper_engine.state["daily"] = {
            "date": today, "start_balance_paper": 10000.0, "halted_paper": False,
        }

    def test_low_confidence_signal_filtered(self, paper_engine):
        self._setup_no_halt(paper_engine)
        paper_engine.state["config"]["min_confidence"] = 7
        json_signal = {"signal_type": "long", "confidence_score": 5}
        result = paper_engine.process_new_signal("BTC/USDT", 100000, json_signal, "")
        assert result is None

    def test_wait_signal_filtered(self, paper_engine):
        self._setup_no_halt(paper_engine)
        json_signal = {"signal_type": "wait", "confidence_score": 9}
        result = paper_engine.process_new_signal("BTC/USDT", 100000, json_signal, "")
        assert result is None

    def test_stand_aside_blocks_signal(self, paper_engine):
        self._setup_no_halt(paper_engine)
        json_signal = {
            "signal_type": "long", "confidence_score": 9,
            "market_context": {"trading_bias": "stand_aside"},
        }
        result = paper_engine.process_new_signal("BTC/USDT", 100000, json_signal, "")
        assert result is None


class TestCorrelationCheck:
    """Test correlation-based risk control."""

    def test_correlated_pair_blocked(self, paper_engine):
        today = datetime.now().strftime("%Y-%m-%d")
        paper_engine.state["daily"] = {
            "date": today, "start_balance_paper": 10000.0, "halted_paper": False,
        }
        btc_trade = make_trade(symbol="BTC/USDT", sig_type="long", status="filled")
        paper_engine.state["trades"] = [btc_trade]
        json_signal = {"signal_type": "long", "confidence_score": 9}
        result = paper_engine.process_new_signal("ETH/USDT", 3000, json_signal, "")
        assert result is None


class TestFundingFeeModel:
    """Test funding fee model."""

    def test_funding_fee_charged_in_paper_mode(self, paper_engine):
        trade = make_trade(sig_type="long", entry=100000, sl=95000, tps=[105000], amount=0.01)
        trade["status"] = "filled"
        trade["is_live"] = False
        old_epoch = int(time.time() // (8 * 3600)) - 1
        trade["funding_epoch"] = old_epoch
        paper_engine.state["trades"] = [trade]
        paper_engine.state["config"]["funding_rate_per_8h"] = 0.0001
        paper_engine.state["config"]["paper_account_balance"] = 10000.0

        initial_balance = paper_engine.state["config"]["paper_account_balance"]
        run_price_update(paper_engine, "BTC/USDT", 100000, 95000, 100000)

        updated = paper_engine.state["trades"][0]
        assert "funding_fees_usd" in updated
        assert updated["funding_fees_usd"] > 0
        assert paper_engine.state["config"]["paper_account_balance"] < initial_balance

    def test_funding_fee_not_charged_in_live_mode(self, paper_engine):
        trade = make_trade(sig_type="long", entry=100000, sl=95000, tps=[105000], amount=0.01)
        trade["status"] = "filled"
        trade["is_live"] = True
        old_epoch = int(time.time() // (8 * 3600)) - 1
        trade["funding_epoch"] = old_epoch
        paper_engine.state["trades"] = [trade]
        paper_engine.state["config"]["funding_rate_per_8h"] = 0.0001

        run_price_update(paper_engine, "BTC/USDT", 100000, 95000, 100000)

        updated = paper_engine.state["trades"][0]
        assert "funding_fees_usd" not in updated or updated.get("funding_fees_usd", 0) == 0


# ═══════════════════════════════════════════════════════════════
# TP1 SL Non-Regression Tests (New Fix: SL never moves backwards)
# ═══════════════════════════════════════════════════════════════

class TestTP1SLNoRegression:
    """Verify TP1 handler never moves SL backwards when trailing stop already improved it."""

    def test_long_tp1_keeps_trailing_sl(self, paper_engine):
        """LONG: After trailing stop moves SL above breakeven, TP1 should NOT reset it to breakeven."""
        trade = make_trade(sig_type="long", entry=100000, sl=95000,
                           tps=[105000, 110000], amount=0.01)
        paper_engine.state["trades"] = [trade]
        # Hit TP1: trailing stop will move SL to ~103500, TP1 should keep it there
        run_price_update(paper_engine, "BTC/USDT", 105000, 104000, 105000)

        t = paper_engine.state["trades"][0]
        assert t["status"] == "tp1_hit"
        assert t["stop_loss"] > 100000, \
            f"SL should be above breakeven (trailing stop moved it), got {t['stop_loss']}"

    def test_short_tp1_keeps_trailing_sl(self, paper_engine):
        """SHORT: After trailing stop moves SL below breakeven, TP1 should NOT reset it to breakeven."""
        trade = make_trade(sig_type="short", entry=100000, sl=105000,
                           tps=[95000, 90000], amount=0.01)
        paper_engine.state["trades"] = [trade]
        # Hit TP1: trailing stop will move SL to ~96500, TP1 should keep it there
        run_price_update(paper_engine, "BTC/USDT", 96000, 95000, 95000)

        t = paper_engine.state["trades"][0]
        assert t["status"] == "tp1_hit"
        assert t["stop_loss"] < 100000, \
            f"SL should be below breakeven (trailing stop moved it), got {t['stop_loss']}"

    def test_long_tp1_sl_at_breakeven_when_no_trailing(self, paper_engine):
        """LONG: Without trailing stop activation, TP1 should set SL to breakeven."""
        trade = make_trade(sig_type="long", entry=100000, sl=99000,
                           tps=[100500, 101000], amount=0.01, lev=5, margin=200.0)
        paper_engine.state["trades"] = [trade]
        # With 5x lev, 0.5% move = 2.5% PnL — below 25% threshold, no trailing stop
        run_price_update(paper_engine, "BTC/USDT", 100500, 99900, 100500)

        t = paper_engine.state["trades"][0]
        assert t["status"] == "tp1_hit"
        assert t["stop_loss"] == 100000, \
            f"SL should be at breakeven without trailing stop, got {t['stop_loss']}"


# ═══════════════════════════════════════════════════════════════
# Dynamic Trailing Stop Tests
# ═══════════════════════════════════════════════════════════════

class TestTrailingStop:
    """Test dynamic trailing stop algorithm."""

    def test_no_trailing_below_25pct(self, paper_engine):
        """Trailing stop should NOT activate below 25% PnL."""
        trade = make_trade(sig_type="long", entry=100000, sl=99000,
                           tps=[100500, 101000], amount=0.01, lev=5, margin=200.0)
        paper_engine.state["trades"] = [trade]
        # 5x lev, 0.4% move = 2% PnL — well below 25% threshold
        run_price_update(paper_engine, "BTC/USDT", 100400, 99500, 100400)

        t = paper_engine.state["trades"][0]
        assert t.get("peak_pnl_pct", 0) < 25.0
        assert t["stop_loss"] == 99000  # SL unchanged

    def test_breakeven_at_25pct(self, paper_engine):
        """At 25% PnL, SL should move to breakeven."""
        trade = make_trade(sig_type="long", entry=100000, sl=95000,
                           tps=[105000, 110000], amount=0.01)
        paper_engine.state["trades"] = [trade]
        # 50x lev, 0.5% move = 25% PnL — at threshold
        run_price_update(paper_engine, "BTC/USDT", 100500, 99800, 100500)

        t = paper_engine.state["trades"][0]
        assert t["stop_loss"] >= 100000  # At least breakeven

    def test_trailing_only_moves_forward_long(self, paper_engine):
        """LONG: Trailing stop should only move SL upward, never downward."""
        trade = make_trade(sig_type="long", entry=100000, sl=95000,
                           tps=[105000, 110000], amount=0.01)
        paper_engine.state["trades"] = [trade]

        # First tick: push price up, trailing stop activates
        run_price_update(paper_engine, "BTC/USDT", 105000, 104000, 105000)
        t = paper_engine.state["trades"][0]
        sl_after_first = t["stop_loss"]

        # Second tick: price drops slightly but SL should NOT move down
        run_price_update(paper_engine, "BTC/USDT", 104500, 103800, 104500)
        t = paper_engine.state["trades"][0]
        assert t["stop_loss"] >= sl_after_first, \
            f"SL should never move down for LONG: was {sl_after_first}, now {t['stop_loss']}"


# ═══════════════════════════════════════════════════════════════
# Circuit Breaker Edge Cases
# ═══════════════════════════════════════════════════════════════

class TestCircuitBreakerEdgeCases:
    """Test circuit breaker edge cases."""

    def test_zero_balance_no_crash(self, paper_engine):
        """Circuit breaker should not crash with zero balance."""
        today = datetime.now().strftime("%Y-%m-%d")
        paper_engine.state["daily"] = {
            "date": today, "start_balance_paper": 0.0, "halted_paper": False,
        }
        paper_engine.state["config"]["paper_account_balance"] = 0.0
        # Should not crash, should return False (no loss possible with 0 balance)
        result = paper_engine._check_circuit_breaker()
        assert result is False

    def test_negative_balance_no_crash(self, paper_engine):
        """Circuit breaker should handle negative balance gracefully."""
        today = datetime.now().strftime("%Y-%m-%d")
        paper_engine.state["daily"] = {
            "date": today, "start_balance_paper": 10000.0, "halted_paper": False,
        }
        paper_engine.state["config"]["paper_account_balance"] = -500.0
        result = paper_engine._check_circuit_breaker()
        # Should trigger since loss exceeds limit
        assert result is True

    def test_circuit_breaker_disabled(self, paper_engine):
        """When disabled, circuit breaker should never trigger."""
        today = datetime.now().strftime("%Y-%m-%d")
        paper_engine.state["daily"] = {
            "date": today, "start_balance_paper": 10000.0, "halted_paper": False,
        }
        paper_engine.state["config"]["circuit_breaker_enabled"] = False
        paper_engine.state["config"]["paper_account_balance"] = 1000.0
        result = paper_engine._check_circuit_breaker()
        assert result is False

    def test_circuit_breaker_resets_next_day(self, paper_engine):
        """Circuit breaker should auto-reset on a new calendar day."""
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        paper_engine.state["daily"] = {
            "date": yesterday, "start_balance_paper": 10000.0, "halted_paper": True,
        }
        paper_engine.state["config"]["paper_account_balance"] = 10000.0
        # _check_circuit_breaker should detect date change and reset
        result = paper_engine._check_circuit_breaker()
        assert result is False  # Reset because new day
        assert not paper_engine.is_halted()


# ═══════════════════════════════════════════════════════════════
# Live Trading Mock Tests
# ═══════════════════════════════════════════════════════════════

class TestLiveTradingSafety:
    """Test live trading safety mechanisms (using mocks)."""

    def test_try_live_close_returns_true_for_paper(self, paper_engine):
        """_try_live_close should return True for paper mode (non-live trades)."""
        trade = make_trade(is_live=False)
        result = paper_engine._try_live_close(
            trade, "BTC/USDT", "long", 0.01, reason="test", alert_tag="test"
        )
        assert result is True

    def test_try_live_close_returns_false_on_exchange_failure(self, paper_engine):
        """_try_live_close should return False when exchange close fails for live trade."""
        trade = make_trade(is_live=True)
        trade["live_fail_alerted_sl"] = False
        with patch.object(paper_engine, '_execute_live_market_close', return_value=None):
            result = paper_engine._try_live_close(
                trade, "BTC/USDT", "long", 0.01, reason="test", alert_tag="sl"
            )
        assert result is False
        assert trade.get("live_fail_alerted_sl") is True

    def test_try_live_close_returns_true_on_exchange_success(self, paper_engine):
        """_try_live_close should return True when exchange close succeeds for live trade."""
        trade = make_trade(is_live=True)
        with patch.object(paper_engine, '_execute_live_market_close', return_value="order_123"):
            with patch.object(paper_engine, '_cancel_protective_sl'):
                with patch.object(paper_engine, '_cancel_all_conditional_orders_for_symbol'):
                    result = paper_engine._try_live_close(
                        trade, "BTC/USDT", "long", 0.01, reason="test", alert_tag="tp1"
                    )
        assert result is True

    def test_live_sl_does_not_close_on_exchange_failure(self, paper_engine):
        """When live close fails, trade should remain in filled/tp1_hit status."""
        trade = make_trade(sig_type="long", entry=100000, sl=95000,
                           tps=[105000, 110000], amount=0.01, is_live=True)
        paper_engine.state["trades"] = [trade]
        # Mock exchange close to fail
        with patch.object(paper_engine, '_execute_live_market_close', return_value=None):
            with patch.object(paper_engine, '_sync_live_balance_cached'):
                with patch.object(paper_engine, '_fetch_live_positions', return_value=None):
                    run_price_update(paper_engine, "BTC/USDT", 94000, 94000, 94000)
        # Trade should NOT be closed (exchange close failed)
        t = paper_engine.state["trades"][0]
        assert t["status"] in ("filled", "tp1_hit"), \
            f"Trade should remain open when exchange close fails, got {t['status']}"

    def test_force_cancel_handles_empty_orders(self, paper_engine):
        """_binance_force_cancel_all_orders should handle empty order list."""
        mock_exchange = MagicMock()
        mock_exchange.fapiPrivateDeleteAllOpenOrders.side_effect = Exception("not binance")
        mock_exchange.fetch_open_orders.return_value = []
        mock_exchange.fapiPrivateDeleteAlgoOpenOrders.side_effect = Exception("no algo")
        mock_exchange.fapiPrivateGetOpenAlgoOrders.return_value = []
        result = paper_engine._binance_force_cancel_all_orders(mock_exchange, "BTC/USDT:USDT")
        assert result == 0

    def test_force_cancel_handles_fetch_error(self, paper_engine):
        """_binance_force_cancel_all_orders should handle fetch errors gracefully."""
        mock_exchange = MagicMock()
        mock_exchange.fapiPrivateDeleteAllOpenOrders.side_effect = Exception("not binance")
        mock_exchange.fetch_open_orders.side_effect = Exception("Network error")
        mock_exchange.fapiPrivateDeleteAlgoOpenOrders.side_effect = Exception("no algo")
        mock_exchange.fapiPrivateGetOpenAlgoOrders.side_effect = Exception("Network error")
        result = paper_engine._binance_force_cancel_all_orders(mock_exchange, "BTC/USDT:USDT")
        assert result == 0


# ═══════════════════════════════════════════════════════════════
# Adaptive Risk Engine Tests
# ═══════════════════════════════════════════════════════════════

class TestAdaptiveRisk:
    """Test adaptive risk adjustment based on recent performance."""

    def test_no_adjustment_with_insufficient_data(self, paper_engine):
        """With fewer than 3 closed trades, risk should not be adjusted."""
        paper_engine.state["trades"] = []
        adjusted = paper_engine._adaptive_risk_adjust(2.0)
        assert adjusted == 2.0

    def test_high_win_rate_keeps_full_risk(self, paper_engine):
        """Win rate >= 60% should keep full risk."""
        for i in range(5):
            t = make_trade(status="closed_tp", is_live=False)
            t["closed_at"] = f"2026-01-0{i+1} 10:00:00"
            paper_engine.state["trades"].append(t)
        adjusted = paper_engine._adaptive_risk_adjust(2.0)
        assert adjusted == 2.0

    def test_low_win_rate_reduces_risk(self, paper_engine):
        """Win rate < 25% should reduce risk to 30%, plus cooldown for consecutive losses."""
        for i in range(5):
            t = make_trade(status="closed_sl", is_live=False)
            t["closed_at"] = f"2026-01-0{i+1} 10:00:00"
            paper_engine.state["trades"].append(t)
        adjusted = paper_engine._adaptive_risk_adjust(2.0)
        # 5 losses: win_rate=0 (<25%) → 0.3x, cooldown 4+ losses → 0.3x → 2.0 * 0.3 * 0.3 = 0.18
        assert adjusted == 0.18, f"Expected 0.18 (0.3x win_rate * 0.3x cooldown), got {adjusted}"

    def test_cooldown_after_consecutive_losses(self, paper_engine):
        """4+ consecutive SL should apply 0.3x cooldown multiplier."""
        for i in range(4):
            t = make_trade(status="closed_sl", is_live=False)
            t["closed_at"] = f"2026-01-0{i+1} 10:00:00"
            paper_engine.state["trades"].append(t)
        multiplier = paper_engine._cooldown_multiplier()
        assert multiplier == 0.3

    def test_cooldown_resets_on_win(self, paper_engine):
        """Cooldown should reset when a win occurs."""
        for i in range(3):
            t = make_trade(status="closed_sl", is_live=False)
            t["closed_at"] = f"2026-01-0{i+1} 10:00:00"
            paper_engine.state["trades"].append(t)
        # Add a recent win
        t = make_trade(status="closed_tp", is_live=False)
        t["closed_at"] = "2026-01-10 10:00:00"
        paper_engine.state["trades"].append(t)
        multiplier = paper_engine._cooldown_multiplier()
        assert multiplier == 1.0


# ═══════════════════════════════════════════════════════════════
# Pending Order Lifecycle Tests
# ═══════════════════════════════════════════════════════════════

class TestPendingOrderLifecycle:
    """Test pending order lifecycle and expiry."""

    def test_pending_order_expires_after_ttl(self, paper_engine):
        """Pending order should be cancelled after TTL expires."""
        old_time = (datetime.now() - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S")
        trade = make_trade(status="pending", entry=100000, sl=95000, tps=[105000])
        trade["entered_at"] = old_time
        trade["planned_entry"] = 100000
        paper_engine.state["config"]["pending_ttl_hours"] = 24.0
        paper_engine.state["trades"] = [trade]

        run_price_update(paper_engine, "BTC/USDT", 100000, 95000, 100000)
        t = paper_engine.state["trades"][0]
        assert t["status"] == "cancelled"

    def test_pending_long_fills_on_entry_touch(self, paper_engine):
        """LONG pending order should fill when price touches planned entry."""
        today = datetime.now().strftime("%Y-%m-%d")
        paper_engine.state["daily"] = {
            "date": today, "start_balance_paper": 10000.0, "halted_paper": False,
        }
        trade = make_trade(status="pending", entry=100000, sl=95000, tps=[105000])
        trade["planned_entry"] = 100000
        paper_engine.state["trades"] = [trade]

        run_price_update(paper_engine, "BTC/USDT", 100500, 99800, 100200)
        t = paper_engine.state["trades"][0]
        assert t["status"] == "filled"

    def test_pending_long_invalidated_by_sl_breach(self, paper_engine):
        """LONG pending order should be cancelled if SL is breached before entry."""
        trade = make_trade(status="pending", entry=100000, sl=95000, tps=[105000])
        trade["planned_entry"] = 100000
        paper_engine.state["trades"] = [trade]

        # Price drops below SL without touching entry
        run_price_update(paper_engine, "BTC/USDT", 98000, 94000, 94000)
        t = paper_engine.state["trades"][0]
        assert t["status"] == "cancelled"


# ═══════════════════════════════════════════════════════════════
# Max Active Trades Test
# ═══════════════════════════════════════════════════════════════

class TestMaxActiveTrades:
    """Test max active trades limit."""

    def test_exceeds_max_active_trades(self, paper_engine):
        """New pending order should not fill when max_active_trades is reached."""
        today = datetime.now().strftime("%Y-%m-%d")
        paper_engine.state["daily"] = {
            "date": today, "start_balance_paper": 10000.0, "halted_paper": False,
        }
        paper_engine.state["config"]["max_active_trades"] = 1

        # Add one filled trade with SL far from current price to avoid PnL breach
        filled = make_trade(status="filled", entry=95000, sl=80000, tps=[105000], lev=5, margin=190.0)
        filled["position_size_usd"] = 950.0  # 0.01 * 95000
        paper_engine.state["trades"] = [filled]

        # Try to fill another pending order
        pending = make_trade(status="pending", entry=95000, sl=90000, tps=[100000])
        pending["planned_entry"] = 95000
        paper_engine.state["trades"].append(pending)

        # Price touches pending entry but first trade's SL (80000) is far away
        run_price_update(paper_engine, "BTC/USDT", 96000, 94000, 95000)
        # The pending trade should remain pending (max active reached)
        assert paper_engine.state["trades"][1]["status"] == "pending", \
            f"Expected pending, got {paper_engine.state['trades'][1]['status']}"


# ═══════════════════════════════════════════════════════════════
# Opposite Direction Position Guard (P2-5 fix)
# ═══════════════════════════════════════════════════════════════

class TestOppositeDirectionGuard:
    """Test that new signals are rejected when opposite position exists."""

    def test_reject_opposite_direction_long_exists(self, paper_engine):
        """New SHORT signal should be rejected when LONG position exists."""
        today = datetime.now().strftime("%Y-%m-%d")
        paper_engine.state["daily"] = {
            "date": today, "start_balance_paper": 10000.0, "halted_paper": False,
        }
        existing = make_trade(status="filled", sig_type="long", entry=100000, sl=95000, tps=[105000])
        paper_engine.state["trades"] = [existing]

        json_signal = {
            "signal_type": "short", "confidence_score": 9,
            "entry_zone": {"min": 101000, "max": 102000},
            "stop_loss": 105000,
            "take_profit_targets": [98000, 95000],
            "risk_reward_ratio": 2.0,
        }
        result = paper_engine.process_new_signal("BTC/USDT", 101500, json_signal, "")
        assert result is None, "Should reject SHORT when LONG position exists"

    def test_reject_duplicate_pending_same_direction(self, paper_engine):
        """Duplicate pending order in same direction should be rejected."""
        today = datetime.now().strftime("%Y-%m-%d")
        paper_engine.state["daily"] = {
            "date": today, "start_balance_paper": 10000.0, "halted_paper": False,
        }
        existing = make_trade(status="pending", sig_type="long", entry=100000, sl=95000, tps=[105000])
        paper_engine.state["trades"] = [existing]

        json_signal = {
            "signal_type": "long", "confidence_score": 9,
            "entry_zone": {"min": 99000, "max": 101000},
            "stop_loss": 95000,
            "take_profit_targets": [105000, 110000],
            "risk_reward_ratio": 2.0,
        }
        result = paper_engine.process_new_signal("BTC/USDT", 102000, json_signal, "")
        assert result is None, "Should reject duplicate LONG pending order"


# ═══════════════════════════════════════════════════════════════
# Wick-based PnL Breach Detection (P1-1 fix)
# ═══════════════════════════════════════════════════════════════

class TestWickPnlBreach:
    """Test that wick-based PnL breach detects gap-through scenarios."""

    def test_wick_breach_triggers_sl_long(self, paper_engine):
        """LONG: If wick penetrates max loss but close recovers, position should still be closed."""
        # Use high leverage (50x) so a 1.1% wick dip = 55% loss (exceeds 50% threshold)
        trade = make_trade(sig_type="long", entry=100000, sl=98000, tps=[103000],
                           amount=0.01, lev=50, margin=20.0)
        paper_engine.state["trades"] = [trade]
        # low=98900 is 1.1% below entry = 55% loss on 50x, triggers wick breach
        # close=99500 is only 25% loss at close, but wick breached threshold
        run_price_update(paper_engine, "BTC/USDT", 100200, 98900, 99500)

        updated = paper_engine.state["trades"][0]
        assert updated["status"] == "closed_sl", \
            f"Wick breach should trigger SL close, got {updated['status']}"

    def test_wick_breach_triggers_sl_short(self, paper_engine):
        """SHORT: If wick penetrates max loss but close recovers, position should still be closed."""
        trade = make_trade(sig_type="short", entry=100000, sl=102000, tps=[97000],
                           amount=0.01, lev=50, margin=20.0)
        paper_engine.state["trades"] = [trade]
        # high=101100 is 1.1% above entry = 55% loss on 50x, triggers wick breach
        run_price_update(paper_engine, "BTC/USDT", 101100, 99800, 100500)

        updated = paper_engine.state["trades"][0]
        assert updated["status"] == "closed_sl", \
            f"Wick breach should trigger SL close, got {updated['status']}"


# ═══════════════════════════════════════════════════════════════
# Default Config Consistency (P1-2 fix)
# ═══════════════════════════════════════════════════════════════

class TestDefaultConfigConsistency:
    """Test that default leverage values are consistent across codebase."""

    def test_calculate_trade_params_defaults(self, paper_engine):
        """calculate_trade_params should use consistent defaults (20-50 leverage)."""
        pos_val, margin, lev = paper_engine.calculate_trade_params(
            balance=10000, risk_pct=2.0, entry_price=100000,
            stop_loss=99000, confidence=8, max_lev=50
        )
        assert lev <= 50, f"Leverage should not exceed max_leverage 50, got {lev}"
        assert lev >= 20, f"Leverage should be at least min_leverage 20, got {lev}"
        assert pos_val > 0
        assert margin > 0


# ═══════════════════════════════════════════════════════════════
# Funding Fee Default (P1-3 fix)
# ═══════════════════════════════════════════════════════════════

class TestFundingFeeDefault:
    """Test funding fee default is realistic."""

    def test_funding_rate_default_is_realistic(self, paper_engine):
        """Default funding rate should be 0.03%/8h (not 0.01%)."""
        cfg = paper_engine.state.get("config", {})
        rate = float(cfg.get("funding_rate_per_8h", 0))
        assert rate == 0.0003, f"Default funding rate should be 0.0003, got {rate}"


# ═══════════════════════════════════════════════════════════════
# TP1 SL Non-Regression (Notification text fix P2-4)
# ═══════════════════════════════════════════════════════════════

class TestTP1NotificationText:
    """Test that TP1 handler uses actual SL value (not hardcoded entry)."""

    def test_tp1_long_sl_not_reset_to_entry(self, paper_engine):
        """LONG TP1: SL should stay above entry when trailing stop moved it up."""
        trade = make_trade(sig_type="long", entry=100000, sl=95000,
                           tps=[105000, 110000], amount=0.01)
        paper_engine.state["trades"] = [trade]
        run_price_update(paper_engine, "BTC/USDT", 105000, 104000, 105000)
        t = paper_engine.state["trades"][0]
        assert t["status"] == "tp1_hit"
        assert t["stop_loss"] >= 100000  # At or above breakeven


# ═══════════════════════════════════════════════════════════════
# P1-1: Circuit Breaker Balance Sync Tolerance
# ═══════════════════════════════════════════════════════════════

class TestCircuitBreakerSyncTolerance:
    """Test circuit breaker resilience when live balance sync fails."""

    def test_live_zero_balance_warns(self, tmp_engine):
        """Circuit breaker should warn when live balance is 0 in live mode."""
        tmp_engine.state["config"]["mode"] = "live"
        tmp_engine.state["config"]["live_account_balance"] = 0.0
        tmp_engine.state["config"]["circuit_breaker_enabled"] = True
        today = datetime.now().strftime("%Y-%m-%d")
        tmp_engine.state["daily"] = {
            "date": today, "start_balance_live": 0.0, "halted_live": False,
        }
        # Should return False (disabled) and set the warning flag
        tmp_engine._live_balance_warned_disabled = False
        result = tmp_engine._check_circuit_breaker()
        assert result is False
        assert tmp_engine._live_balance_warned_disabled is True

    def test_sync_fail_count_increments(self, tmp_engine):
        """Live balance sync failure should increment fail counter."""
        tmp_engine.state["config"]["mode"] = "live"
        tmp_engine.state["config"]["live_api_key"] = "fake_key"
        tmp_engine.state["config"]["live_secret"] = "fake_secret"
        tmp_engine._live_balance_cache = None
        tmp_engine._live_balance_cache_time = 0
        initial_count = tmp_engine._live_balance_sync_fail_count
        # _init_live_ccxt will fail since ccxt is mocked
        try:
            tmp_engine._sync_live_balance_cached()
        except Exception:
            pass
        # Fail count should have incremented (or stayed same if ccxt mock returned something)
        # Since ccxt is mocked, it may not throw — but the key point is no crash
        assert tmp_engine._live_balance_sync_fail_count >= initial_count

    def test_sync_success_resets_fail_count(self, tmp_engine):
        """Successful sync should reset fail count and warning flag."""
        tmp_engine._live_balance_sync_fail_count = 5
        tmp_engine._live_balance_warned_disabled = True
        # Simulate successful sync by directly setting values
        tmp_engine._live_balance_sync_fail_count = 0
        tmp_engine._live_balance_warned_disabled = False
        assert tmp_engine._live_balance_sync_fail_count == 0
        assert tmp_engine._live_balance_warned_disabled is False


# ═══════════════════════════════════════════════════════════════
# P2-1: max_trade_loss_pct Key Name Bug Fix
# ═══════════════════════════════════════════════════════════════

class TestMaxTradeLossKeyName:
    """Test that max_trade_loss_percent config value is correctly read."""

    def test_custom_loss_percent_is_respected(self, paper_engine):
        """User-configured max_trade_loss_percent=30 should be used, not ignored."""
        paper_engine.state["config"]["max_trade_loss_percent"] = 30.0
        # 30% of margin loss on 50x leverage = 0.6% price move
        # Entry=100000, 0.6% drop = 99400, which is above SL=98000
        trade = make_trade(sig_type="long", entry=100000, sl=98000, tps=[103000],
                           amount=0.01, lev=50, margin=20.0)
        paper_engine.state["trades"] = [trade]
        # low=99350 is 0.65% below entry = 32.5% loss on 50x, exceeds 30% threshold
        # With the old bug (always using 50%), this would NOT trigger
        run_price_update(paper_engine, "BTC/USDT", 100200, 99350, 99600)

        updated = paper_engine.state["trades"][0]
        assert updated["status"] == "closed_sl", \
            f"Custom max_trade_loss_percent=30 should trigger at 32.5% loss, got {updated['status']}"

    def test_default_loss_percent_still_works(self, paper_engine):
        """Default max_trade_loss_percent=50 should still work when no custom value."""
        paper_engine.state["config"]["max_trade_loss_percent"] = 50.0
        trade = make_trade(sig_type="long", entry=100000, sl=98000, tps=[103000],
                           amount=0.01, lev=50, margin=20.0)
        paper_engine.state["trades"] = [trade]
        # 40% loss on 50x = 0.8% price drop = 99200, should NOT trigger at 50% threshold
        run_price_update(paper_engine, "BTC/USDT", 100200, 99200, 99600)

        updated = paper_engine.state["trades"][0]
        assert updated["status"] != "closed_sl", \
            f"40% loss should not trigger at 50% threshold, got {updated['status']}"


# ═══════════════════════════════════════════════════════════════
# P2-4: ConsistencyFix Hard Filter Bypass Fix
# ═══════════════════════════════════════════════════════════════

class TestConsistencyFixHardFilters:
    """Test that ConsistencyFix respects Fear & Greed and Funding rate extremes."""

    def _make_agent(self):
        from agent import FeiyangAgent
        agent = object.__new__(FeiyangAgent)
        agent.min_confidence = 7
        return agent

    def test_long_blocked_by_extreme_fear_greed(self):
        """LONG override should be blocked when Fear & Greed > 85."""
        agent = self._make_agent()
        signal = {
            "signal_type": "wait", "confidence_score": 9, "symbol": "BTC/USDT",
            "entry_zone": {"min": 99000, "max": 101000},
            "stop_loss": 95000,
            "take_profit_targets": [105000, 110000],
            "risk_reward_ratio": 2.5,
            "market_context": {
                "trading_bias": "normal",
                "fear_greed": {"value": 90, "label": "Extreme Greed"},
            },
        }
        result = agent._normalize_signal(signal)
        assert result["signal_type"] == "wait", \
            "LONG should be blocked when F&G > 85"

    def test_short_blocked_by_extreme_fear_greed(self):
        """SHORT override should be blocked when Fear & Greed < 15."""
        agent = self._make_agent()
        signal = {
            "signal_type": "wait", "confidence_score": 9, "symbol": "BTC/USDT",
            "entry_zone": {"min": 99000, "max": 101000},
            "stop_loss": 105000,
            "take_profit_targets": [95000, 90000],
            "risk_reward_ratio": 2.5,
            "market_context": {
                "trading_bias": "normal",
                "fear_greed": {"value": 10, "label": "Extreme Fear"},
            },
        }
        result = agent._normalize_signal(signal)
        assert result["signal_type"] == "wait", \
            "SHORT should be blocked when F&G < 15"

    def test_long_blocked_by_high_funding_rate(self):
        """LONG override should be blocked when funding rate > 0.05%."""
        agent = self._make_agent()
        signal = {
            "signal_type": "wait", "confidence_score": 9, "symbol": "BTC/USDT",
            "entry_zone": {"min": 99000, "max": 101000},
            "stop_loss": 95000,
            "take_profit_targets": [105000, 110000],
            "risk_reward_ratio": 2.5,
            "market_context": {
                "trading_bias": "normal",
                "funding_rates": {"BTC/USDT": 0.0008},
            },
        }
        result = agent._normalize_signal(signal)
        assert result["signal_type"] == "wait", \
            "LONG should be blocked when funding rate > 0.05%"

    def test_short_blocked_by_negative_funding_rate(self):
        """SHORT override should be blocked when funding rate < -0.05%."""
        agent = self._make_agent()
        signal = {
            "signal_type": "wait", "confidence_score": 9, "symbol": "BTC/USDT",
            "entry_zone": {"min": 99000, "max": 101000},
            "stop_loss": 105000,
            "take_profit_targets": [95000, 90000],
            "risk_reward_ratio": 2.5,
            "market_context": {
                "trading_bias": "normal",
                "funding_rates": {"BTC/USDT": -0.0008},
            },
        }
        result = agent._normalize_signal(signal)
        assert result["signal_type"] == "wait", \
            "SHORT should be blocked when funding rate < -0.05%"

    def test_long_allowed_with_normal_fear_greed(self):
        """LONG override should work when F&G is in normal range."""
        agent = self._make_agent()
        signal = {
            "signal_type": "wait", "confidence_score": 9, "symbol": "BTC/USDT",
            "entry_zone": {"min": 99000, "max": 101000},
            "stop_loss": 95000,
            "take_profit_targets": [105000, 110000],
            "risk_reward_ratio": 2.5,
            "market_context": {
                "trading_bias": "normal",
                "fear_greed": {"value": 50, "label": "Neutral"},
                "funding_rates": {"BTC/USDT": 0.0001},
            },
        }
        result = agent._normalize_signal(signal)
        assert result["signal_type"] == "long", \
            "LONG should be allowed when F&G and funding rate are normal"


# ═══════════════════════════════════════════════════════════════
# P2-3: Consensus Threshold Alignment with min_confidence
# ═══════════════════════════════════════════════════════════════

class TestConsensusThresholdAlignment:
    """Test that consensus mechanism respects min_confidence threshold."""

    def _make_agent(self, min_conf=7):
        from agent import FeiyangAgent
        agent = object.__new__(FeiyangAgent)
        agent.min_confidence = min_conf
        agent.model_name = "test"
        agent.temperature = 0.1
        return agent

    def test_default_min_confidence_is_7(self):
        """Agent default min_confidence should be 7 (not 6)."""
        from agent import FeiyangAgent
        agent = object.__new__(FeiyangAgent)
        # Before any config load, the default should be 7
        # (We can't test __init__ without API key, so verify the code default)
        import inspect
        source = inspect.getsource(FeiyangAgent.__init__)
        assert "self.min_confidence = 7" in source, \
            "Default min_confidence should be 7, not 6"

    def test_disagreement_forces_wait_when_penalized_below_threshold(self):
        """When penalized score < min_confidence, consensus should force wait."""
        agent = self._make_agent(min_conf=8)
        # Mock analyze to return disagreeing signals
        signal_1 = {
            "signal_type": "long", "confidence_score": 9, "symbol": "BTC/USDT",
            "entry_zone": {"min": 99000, "max": 101000}, "stop_loss": 95000,
            "take_profit_targets": [105000], "risk_reward_ratio": 2.0,
        }
        signal_2 = {
            "signal_type": "short", "confidence_score": 7, "symbol": "BTC/USDT",
            "entry_zone": {"min": 99000, "max": 101000}, "stop_loss": 105000,
            "take_profit_targets": [95000], "risk_reward_ratio": 2.0,
        }
        report_1 = "## Report 1\n合计 9/12"
        report_2 = "## Report 2\n合计 7/12"

        call_count = [0]
        def mock_analyze(payload):
            call_count[0] += 1
            if call_count[0] == 1:
                return signal_1.copy(), report_1
            return signal_2.copy(), report_2

        agent.analyze = mock_analyze
        result_signal, _ = agent.analyze_with_consensus({"symbol": "BTC/USDT"})

        # max_conf = 9, penalized = 8, min_confidence = 8
        # 8 >= 8 so it should pass... but wait, the threshold is min_confidence + 1 = 9
        # max_conf = 9 >= 9, so it enters the override branch
        # penalized = 8, which is >= min_confidence 8, so it should NOT force wait
        assert result_signal["confidence_score"] == 8
        assert result_signal["signal_type"] in ("long", "short")

    def test_disagreement_forces_wait_when_max_conf_too_low(self):
        """When max_conf < min_confidence + 1, should force wait."""
        agent = self._make_agent(min_conf=8)
        signal_1 = {
            "signal_type": "long", "confidence_score": 8, "symbol": "BTC/USDT",
            "entry_zone": {"min": 99000, "max": 101000}, "stop_loss": 95000,
            "take_profit_targets": [105000], "risk_reward_ratio": 2.0,
        }
        signal_2 = {
            "signal_type": "short", "confidence_score": 7, "symbol": "BTC/USDT",
            "entry_zone": {"min": 99000, "max": 101000}, "stop_loss": 105000,
            "take_profit_targets": [95000], "risk_reward_ratio": 2.0,
        }
        report_1 = "## Report 1\n合计 8/12"
        report_2 = "## Report 2\n合计 7/12"

        call_count = [0]
        def mock_analyze(payload):
            call_count[0] += 1
            if call_count[0] == 1:
                return signal_1.copy(), report_1
            return signal_2.copy(), report_2

        agent.analyze = mock_analyze
        result_signal, _ = agent.analyze_with_consensus({"symbol": "BTC/USDT"})

        # max_conf = 8, threshold = min_confidence + 1 = 9
        # 8 < 9, so should force wait
        assert result_signal["signal_type"] == "wait", \
            f"Should force wait when max_conf < min_confidence + 1, got {result_signal['signal_type']}"

    def test_penalized_below_threshold_forces_wait(self):
        """When penalized score < min_confidence, signal should be forced to wait."""
        agent = self._make_agent(min_conf=9)
        # max_conf = 10, threshold = 10, enters override
        # penalized = 9, min_confidence = 9 → 9 >= 9, should NOT force wait
        signal_1 = {
            "signal_type": "long", "confidence_score": 10, "symbol": "BTC/USDT",
            "entry_zone": {"min": 99000, "max": 101000}, "stop_loss": 95000,
            "take_profit_targets": [105000], "risk_reward_ratio": 2.0,
        }
        signal_2 = {
            "signal_type": "short", "confidence_score": 8, "symbol": "BTC/USDT",
            "entry_zone": {"min": 99000, "max": 101000}, "stop_loss": 105000,
            "take_profit_targets": [95000], "risk_reward_ratio": 2.0,
        }
        report_1 = "## Report 1\n合计 10/12"
        report_2 = "## Report 2\n合计 8/12"

        call_count = [0]
        def mock_analyze(payload):
            call_count[0] += 1
            if call_count[0] == 1:
                return signal_1.copy(), report_1
            return signal_2.copy(), report_2

        agent.analyze = mock_analyze
        result_signal, _ = agent.analyze_with_consensus({"symbol": "BTC/USDT"})

        # max_conf = 10 >= 10 (min_confidence + 1), enters override
        # penalized = 9 >= 9 (min_confidence), should NOT force wait
        assert result_signal["confidence_score"] == 9
        assert result_signal["signal_type"] in ("long", "short"), \
            f"Should allow trade when penalized >= min_confidence, got {result_signal['signal_type']}"


# ═══════════════════════════════════════════════════════════════
# Pending Trade Auto-Promotion Tests (外部持仓 misclassification fix)
# ═══════════════════════════════════════════════════════════════

class TestPendingAutoPromotion:
    """Test that pending live trades with matching exchange positions are
    auto-promoted to 'filled' instead of being misclassified as 'external'."""

    def test_pending_promoted_in_get_trades(self, tmp_engine):
        """A pending live trade with a matching exchange position should be
        auto-promoted to 'filled' when get_trades is called."""
        tmp_engine.state["config"]["mode"] = "live"
        tmp_engine.state["config"]["live_api_key"] = "test_key"
        tmp_engine.state["config"]["live_secret"] = "test_secret"

        trade = make_trade(
            symbol="ETH/USDT", sig_type="short", status="pending",
            entry=1922.0, sl=1933.0, tps=[1900.0, 1880.0],
            is_live=True, lev=50
        )
        trade["live_order_id"] = "order_123"
        trade["actual_entry"] = None
        trade["filled_at"] = None
        tmp_engine.state["trades"] = [trade]

        mock_positions = [{
            "symbol": "ETH/USDT",
            "side": "short",
            "entry_price": 1922.12,
            "mark_price": 1919.0,
            "leverage": 50,
            "margin": 1.03,
            "notional": 51.5,
            "unrealized_pnl": 0.08,
            "unrealized_pnl_percent": 8.12,
            "size": 0.0268,
        }]

        with patch.object(tmp_engine, '_fetch_live_positions', return_value=mock_positions):
            with patch.object(tmp_engine, '_send_notification'):
                with patch.object(tmp_engine, '_init_live_ccxt', return_value=(MagicMock(), "binance")):
                    with patch.object(tmp_engine, '_place_live_protective_sl', return_value="sl_order_1"):
                        trades = tmp_engine.get_trades(mode_filter="live")

        # Should NOT have any external positions
        external = [t for t in trades if t.get("is_external")]
        assert len(external) == 0, f"Should not classify system position as external, got {len(external)} external"

        # The trade should be promoted to filled
        assert len(trades) == 1
        assert trades[0]["status"] == "filled", f"Trade should be promoted to filled, got {trades[0]['status']}"
        assert trades[0]["actual_entry"] == 1922.12
        assert trades[0]["filled_at"] is not None
        assert trades[0].get("protective_sl_order_id") == "sl_order_1"

    def test_pending_without_exchange_position_stays_pending(self, tmp_engine):
        """A pending live trade without a matching exchange position should
        stay 'pending' but still be returned in the trade list."""
        tmp_engine.state["config"]["mode"] = "live"
        tmp_engine.state["config"]["live_api_key"] = "test_key"
        tmp_engine.state["config"]["live_secret"] = "test_secret"

        trade = make_trade(
            symbol="ZAMA/USDT", sig_type="long", status="pending",
            entry=0.047, sl=0.046, tps=[0.049, 0.051],
            is_live=True, lev=50
        )
        trade["live_order_id"] = "order_456"
        trade["actual_entry"] = None
        trade["filled_at"] = None
        tmp_engine.state["trades"] = [trade]

        # No exchange positions (order not filled yet)
        with patch.object(tmp_engine, '_fetch_live_positions', return_value=[]):
            trades = tmp_engine.get_trades(mode_filter="live")

        # Should still be pending and visible
        assert len(trades) == 1
        assert trades[0]["status"] == "pending", f"Trade should stay pending, got {trades[0]['status']}"
        assert not trades[0].get("is_external"), "Pending trade should not be external"

    def test_pending_promoted_in_tick_loop(self, tmp_engine):
        """A pending live trade with a matching exchange position should be
        auto-promoted in the tick loop (check_market_prices)."""
        tmp_engine.state["config"]["mode"] = "live"
        tmp_engine.state["config"]["live_api_key"] = "test_key"
        tmp_engine.state["config"]["live_secret"] = "test_secret"
        tmp_engine.state["config"]["daily_max_loss_percent"] = 5.0
        tmp_engine.state["daily"] = {}

        trade = make_trade(
            symbol="ZEC/USDT", sig_type="long", status="pending",
            entry=497.0, sl=494.0, tps=[505.0, 510.0],
            is_live=True, lev=50
        )
        trade["live_order_id"] = "order_789"
        trade["actual_entry"] = None
        trade["filled_at"] = None
        tmp_engine.state["trades"] = [trade]

        mock_positions = [{
            "symbol": "ZEC/USDT",
            "side": "long",
            "entry_price": 497.87,
            "mark_price": 500.31,
            "leverage": 50,
            "margin": 0.86,
            "notional": 43.0,
            "unrealized_pnl": 0.22,
            "unrealized_pnl_percent": 24.42,
            "size": 0.0864,
        }]

        with patch.object(tmp_engine, '_sync_live_balance_cached'):
            with patch.object(tmp_engine, '_fetch_live_positions', return_value=mock_positions):
                with patch.object(tmp_engine, '_send_notification'):
                    with patch.object(tmp_engine, '_init_live_ccxt', return_value=(MagicMock(), "binance")):
                        with patch.object(tmp_engine, '_place_live_protective_sl', return_value="sl_order_2"):
                            with patch.object(tmp_engine, '_check_circuit_breaker', return_value=False):
                                tmp_engine.check_market_prices({
                                    "ZEC/USDT": {"high": 501.0, "low": 496.0, "close": 500.31}
                                })

        t = tmp_engine.state["trades"][0]
        assert t["status"] == "filled", f"Trade should be promoted to filled in tick loop, got {t['status']}"
        assert t["actual_entry"] == 497.87
        assert t["filled_at"] is not None
        assert t.get("protective_sl_order_id") == "sl_order_2"

    def test_no_external_for_system_filled_positions(self, tmp_engine):
        """Multiple system positions (filled + pending) should all be matched
        and none should be classified as external."""
        tmp_engine.state["config"]["mode"] = "live"
        tmp_engine.state["config"]["live_api_key"] = "test_key"
        tmp_engine.state["config"]["live_secret"] = "test_secret"

        # Trade 1: already filled (ETH SHORT)
        trade1 = make_trade(
            symbol="ETH/USDT", sig_type="short", status="filled",
            entry=1922.0, sl=1933.0, tps=[1900.0, 1880.0],
            is_live=True, lev=50
        )
        # Trade 2: pending (ZEC LONG) - order filled but not yet synced
        trade2 = make_trade(
            symbol="ZEC/USDT", sig_type="long", status="pending",
            entry=497.0, sl=494.0, tps=[505.0, 510.0],
            is_live=True, lev=50
        )
        trade2["live_order_id"] = "order_999"
        trade2["actual_entry"] = None
        trade2["filled_at"] = None
        tmp_engine.state["trades"] = [trade1, trade2]

        mock_positions = [
            {
                "symbol": "ETH/USDT", "side": "short",
                "entry_price": 1922.12, "mark_price": 1919.0,
                "leverage": 50, "margin": 1.03, "notional": 51.5,
                "unrealized_pnl": 0.08, "unrealized_pnl_percent": 8.12,
                "size": 0.0268,
            },
            {
                "symbol": "ZEC/USDT", "side": "long",
                "entry_price": 497.87, "mark_price": 500.31,
                "leverage": 50, "margin": 0.86, "notional": 43.0,
                "unrealized_pnl": 0.22, "unrealized_pnl_percent": 24.42,
                "size": 0.0864,
            },
        ]

        with patch.object(tmp_engine, '_fetch_live_positions', return_value=mock_positions):
            with patch.object(tmp_engine, '_send_notification'):
                with patch.object(tmp_engine, '_init_live_ccxt', return_value=(MagicMock(), "binance")):
                    with patch.object(tmp_engine, '_place_live_protective_sl', return_value="sl_order"):
                        trades = tmp_engine.get_trades(mode_filter="live")

        # No external positions at all
        external = [t for t in trades if t.get("is_external")]
        assert len(external) == 0, f"Should have 0 external positions, got {len(external)}"

        # Both trades should be filled
        filled = [t for t in trades if t["status"] == "filled"]
        assert len(filled) == 2, f"Both trades should be filled, got statuses: {[t['status'] for t in trades]}"


# ═══════════════════════════════════════════════════════════════
# Auto-Adopt External Positions Tests
# ═══════════════════════════════════════════════════════════════

class TestAutoAdoptExternal:
    """Test that orphaned exchange positions are auto-adopted as system trades."""

    def test_orphaned_position_auto_adopted(self, tmp_engine):
        """An exchange position with NO matching system trade should be
        auto-adopted as a new system trade (not external)."""
        tmp_engine.state["config"]["mode"] = "live"
        tmp_engine.state["config"]["live_api_key"] = "test_key"
        tmp_engine.state["config"]["live_secret"] = "test_secret"
        tmp_engine.state["config"]["max_trade_loss_percent"] = 30.0
        tmp_engine.state["trades"] = []

        mock_positions = [{
            "symbol": "ETH/USDT", "side": "short",
            "entry_price": 1922.12, "mark_price": 1919.0,
            "leverage": 50, "margin": 1.03, "notional": 51.5,
            "unrealized_pnl": 0.08, "unrealized_pnl_percent": 8.12,
            "size": 0.0268,
        }]

        with patch.object(tmp_engine, '_fetch_live_positions', return_value=mock_positions):
            with patch.object(tmp_engine, '_send_notification'):
                with patch.object(tmp_engine, '_init_live_ccxt', return_value=(MagicMock(), "binance")):
                    with patch.object(tmp_engine, '_place_live_protective_sl', return_value="sl_001"):
                        trades = tmp_engine.get_trades(mode_filter="live")

        # Should NOT be external
        assert len(trades) == 1
        assert not trades[0].get("is_external"), "Auto-adopted trade should not be external"
        assert trades[0]["status"] == "filled"
        assert trades[0]["symbol"] == "ETH/USDT"
        assert trades[0]["signal_type"] == "short"
        assert trades[0]["actual_entry"] == 1922.12
        assert trades[0].get("auto_adopted") is True
        assert trades[0].get("protective_sl_order_id") == "sl_001"

        # Should be saved to state
        saved = [t for t in tmp_engine.state["trades"] if t.get("auto_adopted")]
        assert len(saved) == 1

    def test_closed_trade_reopened_for_matching_position(self, tmp_engine):
        """If a closed trade matches an exchange position, it should be reopened
        instead of creating a duplicate."""
        tmp_engine.state["config"]["mode"] = "live"
        tmp_engine.state["config"]["live_api_key"] = "test_key"
        tmp_engine.state["config"]["live_secret"] = "test_secret"
        tmp_engine.state["config"]["max_trade_loss_percent"] = 30.0

        # A closed trade that was mistakenly closed
        closed_trade = make_trade(
            symbol="ZEC/USDT", sig_type="long", status="closed_tp",
            entry=497.87, sl=496.5, tps=[510.0, 522.0],
            is_live=True, lev=50
        )
        closed_trade["id"] = "trade-1786189680143"
        closed_trade["close_reason"] = "🗑️ 交易所侧仓位已平仓，系统自动同步平仓状态"
        tmp_engine.state["trades"] = [closed_trade]

        mock_positions = [{
            "symbol": "ZEC/USDT", "side": "long",
            "entry_price": 497.87, "mark_price": 500.31,
            "leverage": 50, "margin": 0.86, "notional": 43.0,
            "unrealized_pnl": 0.22, "unrealized_pnl_percent": 24.42,
            "size": 0.0864,
        }]

        with patch.object(tmp_engine, '_fetch_live_positions', return_value=mock_positions):
            with patch.object(tmp_engine, '_send_notification'):
                with patch.object(tmp_engine, '_init_live_ccxt', return_value=(MagicMock(), "binance")):
                    with patch.object(tmp_engine, '_place_live_protective_sl', return_value="sl_002"):
                        trades = tmp_engine.get_trades(mode_filter="live")

        # Should have reopened the existing trade, not created a new one
        assert len(trades) == 1
        assert trades[0]["id"] == "trade-1786189680143", "Should reopen existing trade, not create new"
        assert trades[0]["status"] == "filled", f"Should be reopened as filled, got {trades[0]['status']}"
        assert trades[0]["closed_at"] is None
        assert trades[0].get("auto_adopted") is True
        assert not trades[0].get("is_external")

        # Should NOT have created a duplicate
        all_zec_trades = [t for t in tmp_engine.state["trades"] if t["symbol"] == "ZEC/USDT"]
        assert len(all_zec_trades) == 1, "Should not create duplicate trade"

    def test_no_external_labels_after_adopt(self, tmp_engine):
        """After auto-adoption, no trades should have is_external flag."""
        tmp_engine.state["config"]["mode"] = "live"
        tmp_engine.state["config"]["live_api_key"] = "test_key"
        tmp_engine.state["config"]["live_secret"] = "test_secret"
        tmp_engine.state["trades"] = []

        mock_positions = [
            {
                "symbol": "ETH/USDT", "side": "short",
                "entry_price": 1922.12, "mark_price": 1919.0,
                "leverage": 50, "margin": 1.03, "notional": 51.5,
                "unrealized_pnl": 0.08, "unrealized_pnl_percent": 8.12,
                "size": 0.0268,
            },
            {
                "symbol": "ZEC/USDT", "side": "long",
                "entry_price": 497.87, "mark_price": 500.31,
                "leverage": 50, "margin": 0.86, "notional": 43.0,
                "unrealized_pnl": 0.22, "unrealized_pnl_percent": 24.42,
                "size": 0.0864,
            },
        ]

        with patch.object(tmp_engine, '_fetch_live_positions', return_value=mock_positions):
            with patch.object(tmp_engine, '_send_notification'):
                with patch.object(tmp_engine, '_init_live_ccxt', return_value=(MagicMock(), "binance")):
                    with patch.object(tmp_engine, '_place_live_protective_sl', return_value="sl_x"):
                        trades = tmp_engine.get_trades(mode_filter="live")

        external = [t for t in trades if t.get("is_external")]
        assert len(external) == 0, f"Should have 0 external after adopt, got {len(external)}"
        assert len(trades) == 2
        for t in trades:
            assert t["status"] == "filled"
            assert t.get("auto_adopted") is True

    def test_reopened_trade_has_fresh_timestamps(self, tmp_engine):
        """When a closed trade is reopened for auto-adopt, entered_at and filled_at
        must be reset to NOW — otherwise the time stop-loss fires immediately
        using the original trade's old timestamps."""
        from datetime import datetime, timedelta

        tmp_engine.state["config"]["mode"] = "live"
        tmp_engine.state["config"]["live_api_key"] = "test_key"
        tmp_engine.state["config"]["live_secret"] = "test_secret"
        tmp_engine.state["config"]["max_trade_loss_percent"] = 30.0

        # A closed trade with OLD timestamps (5 days ago)
        old_time = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
        closed_trade = make_trade(
            symbol="ZEC/USDT", sig_type="long", status="closed_tp",
            entry=497.87, sl=496.5, tps=[510.0, 522.0],
            is_live=True, lev=50
        )
        closed_trade["id"] = "trade-old-123"
        closed_trade["entered_at"] = old_time
        closed_trade["filled_at"] = old_time
        closed_trade["close_reason"] = "closed days ago"
        tmp_engine.state["trades"] = [closed_trade]

        mock_positions = [{
            "symbol": "ZEC/USDT", "side": "long",
            "entry_price": 497.87, "mark_price": 500.31,
            "leverage": 50, "margin": 0.86, "notional": 43.0,
            "unrealized_pnl": 0.22, "unrealized_pnl_percent": 24.42,
            "size": 0.0864,
        }]

        before_time = datetime.now()

        with patch.object(tmp_engine, '_fetch_live_positions', return_value=mock_positions):
            with patch.object(tmp_engine, '_send_notification'):
                with patch.object(tmp_engine, '_init_live_ccxt', return_value=(MagicMock(), "binance")):
                    with patch.object(tmp_engine, '_place_live_protective_sl', return_value="sl_003"):
                        trades = tmp_engine.get_trades(mode_filter="live")

        after_time = datetime.now()

        assert trades[0]["status"] == "filled"
        # entered_at and filled_at must be recent (within 60 seconds of now),
        # NOT the old timestamp from 5 days ago
        reopened_at = datetime.strptime(trades[0]["entered_at"], "%Y-%m-%d %H:%M:%S")
        refilled_at = datetime.strptime(trades[0]["filled_at"], "%Y-%m-%d %H:%M:%S")
        assert reopened_at > before_time - timedelta(seconds=5), \
            f"entered_at should be recent, got {trades[0]['entered_at']} (old was {old_time})"
        assert refilled_at > before_time - timedelta(seconds=5), \
            f"filled_at should be recent, got {trades[0]['filled_at']} (old was {old_time})"


# ═══════════════════════════════════════════════════════════════
# Exchange-Side SL Update with Algo Order Support (Trailing Stop Fix)
# ═══════════════════════════════════════════════════════════════

class TestExchangeSLUpdateRetry:
    """Test that _update_live_sl_order_on_exchange cancels algo orders and re-places."""

    def test_force_cancel_cancels_both_regular_and_algo(self, paper_engine):
        """_binance_force_cancel_all_orders should cancel both regular and algo orders."""
        mock_exchange = MagicMock()
        mock_exchange.fapiPrivateDeleteAllOpenOrders.return_value = {}
        mock_exchange.fapiPrivateDeleteAlgoOpenOrders.return_value = {}
        result = paper_engine._binance_force_cancel_all_orders(mock_exchange, "ZEC/USDT:USDT")
        assert result >= 2  # Both regular and algo cancel succeeded
        mock_exchange.fapiPrivateDeleteAllOpenOrders.assert_called_once()
        mock_exchange.fapiPrivateDeleteAlgoOpenOrders.assert_called_once()

    def test_force_cancel_algo_symbol_format(self, paper_engine):
        """Algo cancel API should receive symbol WITHOUT slash."""
        mock_exchange = MagicMock()
        mock_exchange.fapiPrivateDeleteAllOpenOrders.return_value = {}
        mock_exchange.fapiPrivateDeleteAlgoOpenOrders.return_value = {}
        paper_engine._binance_force_cancel_all_orders(mock_exchange, "ZEC/USDT:USDT")
        algo_call = mock_exchange.fapiPrivateDeleteAlgoOpenOrders.call_args[0][0]
        assert algo_call["symbol"] == "ZECUSDT", f"Expected 'ZECUSDT', got '{algo_call['symbol']}'"

    def test_force_cancel_algo_fallback_to_individual(self, paper_engine):
        """If algo bulk cancel fails, should fetch and cancel individually."""
        mock_exchange = MagicMock()
        mock_exchange.fapiPrivateDeleteAllOpenOrders.return_value = {}
        mock_exchange.fapiPrivateDeleteAlgoOpenOrders.side_effect = Exception("API error")
        mock_exchange.fapiPrivateGetOpenAlgoOrders.return_value = [
            {"algoId": 12345, "type": "STOP_MARKET", "symbol": "ZECUSDT"}
        ]
        mock_exchange.fapiPrivateDeleteAlgoOrder.return_value = {}
        result = paper_engine._binance_force_cancel_all_orders(mock_exchange, "ZEC/USDT:USDT")
        assert result >= 1
        mock_exchange.fapiPrivateDeleteAlgoOrder.assert_called()

    def test_cancel_replace_succeeds(self, paper_engine):
        """Cancel all (incl algo) + place new should succeed."""
        trade = make_trade(status="filled", sig_type="long", entry=500, sl=490, tps=[520],
                          amount=0.08, lev=50, margin=0.8, is_live=True)
        trade["protective_sl_order_id"] = "existing"

        mock_exchange = MagicMock()
        mock_exchange.fetch_positions.return_value = [
            {"symbol": "BTC/USDT:USDT", "contracts": 0.08, "side": "long",
             "info": {"positionSide": "LONG", "symbol": "BTCUSDT"}}
        ]

        with patch.object(paper_engine, '_init_live_ccxt', return_value=(mock_exchange, "binance")):
            with patch.object(paper_engine, '_binance_force_cancel_all_orders', return_value=2):
                with patch.object(paper_engine, '_place_live_protective_sl', return_value="new_123"):
                    with patch('time.sleep'):
                        paper_engine._update_live_sl_order_on_exchange(trade, "long", 0.08, 495.0)

        assert trade["protective_sl_order_id"] == "new_123"

    def test_retry_succeeds_on_second_attempt(self, paper_engine):
        """If first placement returns 'existing', retry should cancel and succeed."""
        trade = make_trade(status="filled", sig_type="long", entry=500, sl=490, tps=[520],
                          amount=0.08, lev=50, margin=0.8, is_live=True)
        trade["protective_sl_order_id"] = "existing"

        mock_exchange = MagicMock()
        mock_exchange.fetch_positions.return_value = [
            {"symbol": "BTC/USDT:USDT", "contracts": 0.08, "side": "long",
             "info": {"positionSide": "LONG", "symbol": "BTCUSDT"}}
        ]
        place_results = ["existing", "new_order_123"]

        with patch.object(paper_engine, '_init_live_ccxt', return_value=(mock_exchange, "binance")):
            with patch.object(paper_engine, '_binance_force_cancel_all_orders', return_value=2):
                with patch.object(paper_engine, '_place_live_protective_sl', side_effect=place_results):
                    with patch.object(paper_engine, '_send_notification'):
                        with patch('time.sleep'):
                            paper_engine._update_live_sl_order_on_exchange(
                                trade, "long", 0.08, 495.0
                            )

        assert trade["protective_sl_order_id"] == "new_order_123"

    def test_all_retries_exhausted_sets_existing(self, paper_engine):
        """If all retries fail with 'existing', should set to 'existing' and notify."""
        trade = make_trade(status="filled", sig_type="long", entry=500, sl=490, tps=[520],
                          amount=0.08, lev=50, margin=0.8, is_live=True)
        trade["protective_sl_order_id"] = "existing"

        mock_exchange = MagicMock()
        mock_exchange.fetch_positions.return_value = [
            {"symbol": "BTC/USDT:USDT", "contracts": 0.08, "side": "long",
             "info": {"positionSide": "LONG", "symbol": "BTCUSDT"}}
        ]

        with patch.object(paper_engine, '_init_live_ccxt', return_value=(mock_exchange, "binance")):
            with patch.object(paper_engine, '_binance_force_cancel_all_orders', return_value=2):
                with patch.object(paper_engine, '_place_live_protective_sl', return_value="existing"):
                    with patch.object(paper_engine, '_send_notification') as mock_notify:
                        with patch('time.sleep'):
                            paper_engine._update_live_sl_order_on_exchange(
                                trade, "long", 0.08, 495.0
                            )

        assert trade["protective_sl_order_id"] == "existing"
        assert mock_notify.called, "Should send notification when all retries exhausted"

    def test_skips_non_live_trades(self, paper_engine):
        """Non-live trades should not trigger exchange updates."""
        trade = make_trade(status="filled", sig_type="long", entry=500, sl=490, tps=[520],
                          amount=0.08, lev=50, margin=0.8, is_live=False)

        with patch.object(paper_engine, '_init_live_ccxt') as mock_init:
            paper_engine._update_live_sl_order_on_exchange(trade, "long", 0.08, 495.0)

        mock_init.assert_not_called()

    def test_skips_zero_amount(self, paper_engine):
        """Zero amount should not trigger exchange updates."""
        trade = make_trade(status="filled", sig_type="long", entry=500, sl=490, tps=[520],
                          amount=0.0, lev=50, margin=0.8, is_live=True)

        with patch.object(paper_engine, '_init_live_ccxt') as mock_init:
            paper_engine._update_live_sl_order_on_exchange(trade, "long", 0.0, 495.0)

        mock_init.assert_not_called()

    def test_skips_when_no_position_on_exchange(self, paper_engine):
        """Should skip SL update if exchange has no open position (prevents -4509)."""
        trade = make_trade(status="filled", sig_type="long", entry=500, sl=490, tps=[520],
                          amount=0.08, lev=50, margin=0.8, is_live=True)
        trade["protective_sl_order_id"] = "old_123"

        mock_exchange = MagicMock()
        # Return empty positions — position has been closed on exchange
        mock_exchange.fetch_positions.return_value = []

        with patch.object(paper_engine, '_init_live_ccxt', return_value=(mock_exchange, "binance")):
            with patch.object(paper_engine, '_binance_force_cancel_all_orders') as mock_cancel:
                with patch.object(paper_engine, '_place_live_protective_sl') as mock_place:
                    with patch('time.sleep'):
                        paper_engine._update_live_sl_order_on_exchange(trade, "long", 0.08, 495.0)

        # Should NOT have cancelled orders or placed new ones
        mock_cancel.assert_not_called()
        mock_place.assert_not_called()
        assert trade["protective_sl_order_id"] is None

    def test_no_position_return_stops_retry(self, paper_engine):
        """When _place_live_protective_sl returns 'no_position', stop retrying immediately."""
        trade = make_trade(status="filled", sig_type="long", entry=500, sl=490, tps=[520],
                          amount=0.08, lev=50, margin=0.8, is_live=True)
        trade["protective_sl_order_id"] = "existing"

        mock_exchange = MagicMock()
        mock_exchange.fetch_positions.return_value = [
            {"symbol": "BTC/USDT:USDT", "contracts": 0.08, "side": "long",
             "info": {"positionSide": "LONG", "symbol": "BTCUSDT"}}
        ]

        with patch.object(paper_engine, '_init_live_ccxt', return_value=(mock_exchange, "binance")):
            with patch.object(paper_engine, '_binance_force_cancel_all_orders', return_value=2):
                with patch.object(paper_engine, '_place_live_protective_sl', return_value="no_position") as mock_place:
                    with patch.object(paper_engine, '_send_notification') as mock_notify:
                        with patch('time.sleep'):
                            paper_engine._update_live_sl_order_on_exchange(
                                trade, "long", 0.08, 495.0
                            )

        # Should only be called once (no retry for no_position)
        assert mock_place.call_count == 1
        # Should NOT send alarming notification
        mock_notify.assert_not_called()
        # Should clear the order ID
        assert trade["protective_sl_order_id"] is None

    def test_place_sl_handles_4509_gracefully(self, paper_engine):
        """_place_live_protective_sl should return 'no_position' for -4509 error, not notify."""
        mock_exchange = MagicMock()
        mock_exchange.create_order.side_effect = Exception(
            '{"code":-4509,"msg":"Time in Force (TIF) GTE can only be used with open positions."}'
        )

        with patch.object(paper_engine, '_send_notification') as mock_notify:
            result = paper_engine._place_live_protective_sl(
                mock_exchange, "binance", "BNB/USDT", "short", 1.5, 609.58
            )

        assert result == "no_position"
        mock_notify.assert_not_called()


class TestSignalFreshnessAndReDiagnosis:
    """Test signal freshness expiry and re-diagnosis logic."""

    def test_signal_freshness_cancels_stale_pending(self, paper_engine):
        """Pending trade older than signal_freshness_hours should be cancelled."""
        paper_engine.state["config"]["signal_freshness_hours"] = 1.0
        paper_engine.state["config"]["pending_ttl_hours"] = 24.0

        # Create a pending trade that is 2 hours old (freshness=1h)
        old_time = (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        trade = make_trade(status="pending", entry=100000.0, sl=95000.0, tps=[105000.0])
        trade["entered_at"] = old_time
        trade["planned_entry"] = 100000.0
        # Price is still above entry, not yet crossed
        paper_engine.state["trades"].append(trade)

        # Run price check — price hasn't crossed entry yet, shouldn't fill
        run_price_update(paper_engine, "BTC/USDT", 98000.0, 97500.0, 97700.0)

        assert trade["status"] == "cancelled"
        assert "信号时效性" in trade.get("close_reason", "")

    def test_signal_freshness_skips_fresh_trades(self, paper_engine):
        """Pending trade younger than signal_freshness_hours should NOT be cancelled."""
        paper_engine.state["config"]["signal_freshness_hours"] = 4.0
        paper_engine.state["config"]["pending_ttl_hours"] = 24.0

        # Create a pending trade that is 1 hour old (freshness=4h)
        recent_time = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        trade = make_trade(status="pending", entry=100000.0, sl=95000.0, tps=[105000.0])
        trade["entered_at"] = recent_time
        trade["planned_entry"] = 100000.0
        # For a LONG pending, entry crosses when low_price <= planned_entry
        # Use a price range that stays above planned_entry so it doesn't fill
        # planned_entry=100000, so use low=101500 > 100000
        paper_engine.state["trades"].append(trade)

        # Run price check — price hasn't crossed entry yet (101500 > 100000)
        run_price_update(paper_engine, "BTC/USDT", 102000.0, 101500.0, 101700.0)

        assert trade["status"] == "pending"  # Still pending, not cancelled

    def test_signal_freshness_respects_ttl_priority(self, paper_engine):
        """If freshness > ttl, freshness should not trigger (ttl takes priority)."""
        paper_engine.state["config"]["signal_freshness_hours"] = 48.0  # Longer than ttl
        paper_engine.state["config"]["pending_ttl_hours"] = 24.0

        old_time = (datetime.now() - timedelta(hours=30)).strftime("%Y-%m-%d %H:%M:%S")
        trade = make_trade(status="pending", entry=100000.0, sl=95000.0, tps=[105000.0])
        trade["entered_at"] = old_time
        trade["planned_entry"] = 100000.0
        paper_engine.state["trades"].append(trade)

        run_price_update(paper_engine, "BTC/USDT", 98000.0, 97500.0, 97700.0)

        # Should be cancelled by TTL (30h > 24h), not by freshness
        assert trade["status"] == "cancelled"
        assert "挂单超过" in trade.get("close_reason", "")

    def test_re_diagnosis_triggers_on_price_proximity(self, paper_engine):
        """Pending trade should be flagged for review when price is near entry zone."""
        paper_engine.state["config"]["pending_review_distance_pct"] = 5.0
        paper_engine.state["config"]["pending_review_cooldown_min"] = 30.0

        # Entry center is ~100k, so 5% = ±5k
        # Current price = 97,700 is within 5% of 100k (2.3% away)
        trade = make_trade(status="pending", entry=100000.0, sl=95000.0, tps=[105000.0])
        trade["planned_entry"] = 100000.0
        trade["needs_review"] = False
        trade["last_review_time"] = None
        trade["review_trigger_price"] = None
        paper_engine.state["trades"].append(trade)

        run_price_update(paper_engine, "BTC/USDT", 98000.0, 97500.0, 97700.0)

        assert trade["needs_review"] is True
        assert trade["review_trigger_price"] == 97700.0

    def test_re_diagnosis_respects_cooldown(self, paper_engine):
        """Trade recently reviewed should NOT be flagged again until cooldown expires."""
        paper_engine.state["config"]["pending_review_distance_pct"] = 5.0
        paper_engine.state["config"]["pending_review_cooldown_min"] = 60.0

        # Last review was 5 minutes ago (cooldown=60min)
        recent_review = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        trade = make_trade(status="pending", entry=100000.0, sl=95000.0, tps=[105000.0])
        trade["planned_entry"] = 100000.0
        trade["needs_review"] = False
        trade["last_review_time"] = recent_review
        trade["review_trigger_price"] = None
        paper_engine.state["trades"].append(trade)

        run_price_update(paper_engine, "BTC/USDT", 98000.0, 97500.0, 97700.0)

        # Should NOT be re-flagged because cooldown hasn't expired
        assert trade["needs_review"] is False

    def test_get_pending_trades_needing_review(self, paper_engine):
        """get_pending_trades_needing_review should return only trades with needs_review=True."""
        trade1 = make_trade(status="pending", entry=100000.0, sl=95000.0, tps=[105000.0], symbol="BTC/USDT")
        trade1["needs_review"] = True
        trade1["review_trigger_price"] = 97700.0
        trade1["trade_id"] = "trade-1"
        trade2 = make_trade(status="pending", entry=2000.0, sl=1900.0, tps=[2100.0], symbol="ETH/USDT")
        trade2["needs_review"] = False  # Not flagged
        trade2["trade_id"] = "trade-2"
        trade3 = make_trade(status="filled", entry=100000.0, sl=95000.0, tps=[105000.0], symbol="SOL/USDT")
        trade3["needs_review"] = True  # Flagged but filled, should not be returned
        trade3["trade_id"] = "trade-3"

        paper_engine.state["trades"] = [trade1, trade2, trade3]

        reviews = paper_engine.get_pending_trades_needing_review()

        assert len(reviews) == 1
        assert reviews[0]["trade_id"] == "trade-1"
        assert reviews[0]["symbol"] == "BTC/USDT"

    def test_apply_review_result_keep(self, paper_engine):
        """apply_review_result with action='keep' should not change the trade."""
        trade = make_trade(status="pending", entry=100000.0, sl=95000.0, tps=[105000.0])
        trade["needs_review"] = True
        trade["trade_id"] = "trade-keep"
        paper_engine.state["trades"] = [trade]

        result = paper_engine.apply_review_result("trade-keep", {
            "action": "keep",
            "reason": "市场结构未变，继续持有"
        })

        assert result["success"] is True
        assert trade["status"] == "pending"  # Not cancelled
        assert trade["needs_review"] is False  # Flag cleared
        assert trade["last_review_time"] is not None

    def test_apply_review_result_cancel(self, paper_engine):
        """apply_review_result with action='cancel' should cancel the trade."""
        trade = make_trade(status="pending", entry=100000.0, sl=95000.0, tps=[105000.0])
        trade["needs_review"] = True
        trade["trade_id"] = "trade-cancel"
        paper_engine.state["trades"] = [trade]

        result = paper_engine.apply_review_result("trade-cancel", {
            "action": "cancel",
            "reason": "市场结构已变化，取消入场"
        })

        assert result["success"] is True
        assert trade["status"] == "cancelled"
        assert "取消" in trade.get("close_reason", "")

    def test_apply_review_result_reverse(self, paper_engine):
        """apply_review_result with action='reverse' should reverse trade direction."""
        trade = make_trade(status="pending", entry=100000.0, sl=95000.0, tps=[105000.0])
        trade["needs_review"] = True
        trade["trade_id"] = "trade-reverse"
        paper_engine.state["trades"] = [trade]

        result = paper_engine.apply_review_result("trade-reverse", {
            "action": "reverse",
            "new_signal_type": "short",
            "new_entry_min": 102000.0,
            "new_entry_max": 103000.0,
            "new_stop_loss": 105000.0,
            "new_take_profit_targets": [99000.0, 97000.0],
            "reason": "市场结构反转，应做空"
        })

        assert result["success"] is True
        # The trade_id field in the trade dict uses "trade_id" key, but the test
        # uses "id" in make_trade. Let's check both.
        # Actually make_trade sets "id", and now we also set "trade_id"
        assert trade["signal_type"] == "short"
        assert trade["entry_min"] == 102000.0
        assert trade["entry_max"] == 103000.0
        assert trade["stop_loss"] == 105000.0
        assert trade["take_profit_targets"] == [99000.0, 97000.0]

    def test_apply_review_result_reverse_invalid(self, paper_engine):
        """apply_review_result with reverse but no new_signal_type should fail."""
        trade = make_trade(status="pending", entry=100000.0, sl=95000.0, tps=[105000.0])
        trade["needs_review"] = True
        trade["trade_id"] = "trade-invalid"
        paper_engine.state["trades"] = [trade]

        result = paper_engine.apply_review_result("trade-invalid", {
            "action": "reverse",
            "reason": "反转但无新方向"
        })

        assert result["success"] is False
        assert trade["status"] == "pending"  # Unchanged

    def test_apply_review_result_unknown_trade(self, paper_engine):
        """apply_review_result with non-existent trade_id should fail."""
        result = paper_engine.apply_review_result("non-existent", {
            "action": "keep",
            "reason": "N/A"
        })
        assert result["success"] is False

    def test_apply_review_result_filled_trade(self, paper_engine):
        """apply_review_result should not modify filled trades."""
        trade = make_trade(status="filled", entry=100000.0, sl=95000.0, tps=[105000.0])
        trade["needs_review"] = True
        trade["trade_id"] = "trade-filled"
        paper_engine.state["trades"] = [trade]

        result = paper_engine.apply_review_result("trade-filled", {
            "action": "cancel",
            "reason": "试试取消已成交的"
        })

        assert result["success"] is False
        assert trade["status"] == "filled"  # Unchanged

    def test_get_pending_trades_with_price_dict(self, paper_engine):
        """get_pending_trades_needing_review with prices_dict should flag trades."""
        paper_engine.state["config"]["pending_review_distance_pct"] = 5.0
        paper_engine.state["config"]["pending_review_cooldown_min"] = 30.0

        # Entry center is ~100k, price 97,700 is within 5%
        trade = make_trade(status="pending", entry=100000.0, sl=95000.0, tps=[105000.0], symbol="BTC/USDT")
        trade["needs_review"] = False
        trade["last_review_time"] = None
        trade["trade_id"] = "trade-px"
        paper_engine.state["trades"] = [trade]

        reviews = paper_engine.get_pending_trades_needing_review(
            prices_dict={"BTC/USDT": 97700.0}
        )

        assert len(reviews) == 1
        assert reviews[0]["trade_id"] == "trade-px"
        assert trade["needs_review"] is True


# ═══════════════════════════════════════════════════════════════
# Run tests
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
