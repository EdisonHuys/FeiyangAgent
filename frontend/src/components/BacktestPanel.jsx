import React, { useState, useEffect, useRef, useCallback } from 'react';
import { History, Play, Square, TrendingUp, Activity, ShieldAlert, Award, AlertCircle, Coins, Layers, ArrowUpRight, ArrowDownRight, RefreshCw, BarChart2, CheckCircle2, Clock, DollarSign, Filter } from 'lucide-react';

export default function BacktestPanel({ apiBase, symbols = [] }) {
  const [form, setForm] = useState(() => {
    try {
      const saved = localStorage.getItem('feiyang_backtest_form');
      if (saved) {
        const parsed = JSON.parse(saved);
        return {
          symbol: parsed.symbol || (symbols[0] || 'BTC/USDT'),
          days: parsed.days !== undefined ? Number(parsed.days) : 14,
          step_hours: parsed.step_hours !== undefined ? Number(parsed.step_hours) : 4,
          max_llm_calls: parsed.max_llm_calls !== undefined ? Number(parsed.max_llm_calls) : 60,
          initial_balance: parsed.initial_balance !== undefined ? Number(parsed.initial_balance) : 10000,
        };
      }
    } catch (e) {}
    return {
      symbol: symbols[0] || 'BTC/USDT',
      days: 14,
      step_hours: 4,
      max_llm_calls: 60,
      initial_balance: 10000,
    };
  });

  useEffect(() => {
    try {
      localStorage.setItem('feiyang_backtest_form', JSON.stringify(form));
    } catch (e) {}
  }, [form]);

  const [status, setStatus] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const canvasRef = useRef(null);
  const pollRef = useRef(null);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/backtest/status`);
      const st = await res.json();
      setStatus(st);
      if (!st.running) {
        if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
        if (st.error) {
          setError(st.error);
        } else if (st.finished_at) {
          const rres = await fetch(`${apiBase}/api/backtest/result`);
          const rj = await rres.json();
          if (rj.status === 'success') setResult(rj.result);
        }
      }
    } catch (e) { /* backend may be restarting */ }
  }, [apiBase]);

  // On mount: pick up any already-running job
  useEffect(() => {
    fetchStatus().then(() => {});
    fetch(`${apiBase}/api/backtest/result`)
      .then(r => r.json())
      .then(rj => {
        if (rj.status === 'success') setResult(rj.result);
      })
      .catch(() => {});
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [apiBase, fetchStatus]);

  // Keep polling while running
  useEffect(() => {
    if (status?.running && !pollRef.current) {
      pollRef.current = setInterval(fetchStatus, 3000);
    }
  }, [status?.running, fetchStatus]);

  const startBacktest = async () => {
    setError(null);
    setResult(null);
    try {
      const res = await fetch(`${apiBase}/api/backtest/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      });
      const j = await res.json();
      if (j.status !== 'success') {
        setError(j.message || j.detail || '回测启动失败');
      } else {
        setStatus(s => ({ ...(s || {}), running: true, progress_pct: 0, message: '正在初始化历史数据与大模型诊断...' }));
      }
    } catch (e) {
      setError(`回测启动失败：${e.message}`);
    }
  };

  const stopBacktest = async () => {
    await fetch(`${apiBase}/api/backtest/stop`, { method: 'POST' }).catch(() => {});
  };

  // High-Definition Equity curve canvas rendering
  useEffect(() => {
    if (!result || !canvasRef.current) return;
    const curve = result.equity_curve || [];
    const canvas = canvasRef.current;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    
    canvas.width = rect.width * dpr;
    canvas.height = 180 * dpr;
    ctx.scale(dpr, dpr);

    const W = rect.width;
    const H = 180;
    ctx.clearRect(0, 0, W, H);

    if (curve.length < 2) {
      ctx.fillStyle = '#64748B';
      ctx.font = '13px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('暂无多笔成交数据，平仓后将自动绘制权益曲线', W / 2, H / 2);
      return;
    }

    const values = curve.map(p => p.equity);
    const min = Math.min(...values, result.initial_balance);
    const max = Math.max(...values, result.initial_balance);
    const range = (max - min) || 1;
    const pad = range * 0.1;

    const x = i => (i / (curve.length - 1)) * (W - 60) + 30;
    const y = v => H - 25 - ((v - (min - pad)) / (range + 2 * pad)) * (H - 45);

    // Draw grid background lines
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.05)';
    ctx.lineWidth = 1;
    for (let i = 1; i <= 3; i++) {
      const lineY = (H / 4) * i;
      ctx.beginPath();
      ctx.moveTo(20, lineY);
      ctx.lineTo(W - 20, lineY);
      ctx.stroke();
    }

    // Baseline (initial balance)
    const baseLineY = y(result.initial_balance);
    ctx.strokeStyle = 'rgba(245, 158, 11, 0.4)';
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(20, baseLineY);
    ctx.lineTo(W - 20, baseLineY);
    ctx.stroke();
    ctx.setLineDash([]);

    // Gradient fill under equity curve
    const finalUp = values[values.length - 1] >= result.initial_balance;
    const gradient = ctx.createLinearGradient(0, 0, 0, H);
    if (finalUp) {
      gradient.addColorStop(0, 'rgba(16, 185, 129, 0.25)');
      gradient.addColorStop(1, 'rgba(16, 185, 129, 0.0)');
    } else {
      gradient.addColorStop(0, 'rgba(239, 68, 68, 0.25)');
      gradient.addColorStop(1, 'rgba(239, 68, 68, 0.0)');
    }

    ctx.beginPath();
    ctx.moveTo(x(0), y(curve[0].equity));
    curve.forEach((p, i) => { ctx.lineTo(x(i), y(p.equity)); });
    ctx.lineTo(x(curve.length - 1), H - 10);
    ctx.lineTo(x(0), H - 10);
    ctx.closePath();
    ctx.fillStyle = gradient;
    ctx.fill();

    // Equity Line
    ctx.strokeStyle = finalUp ? '#10B981' : '#EF4444';
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    curve.forEach((p, i) => {
      if (i === 0) ctx.moveTo(x(i), y(p.equity));
      else ctx.lineTo(x(i), y(p.equity));
    });
    ctx.stroke();

    // Points
    curve.forEach((p, i) => {
      ctx.beginPath();
      ctx.arc(x(i), y(p.equity), 3.5, 0, Math.PI * 2);
      ctx.fillStyle = finalUp ? '#10B981' : '#EF4444';
      ctx.fill();
      ctx.strokeStyle = '#0F172A';
      ctx.lineWidth = 1.5;
      ctx.stroke();
    });

    // Min & Max Labels
    ctx.fillStyle = '#94A3B8';
    ctx.font = '11px monospace';
    ctx.textAlign = 'left';
    ctx.fillText(`最高点: $${max.toFixed(2)}`, 30, 20);
    ctx.textAlign = 'right';
    ctx.fillText(`初始资金: $${result.initial_balance.toFixed(2)}`, W - 30, 20);
  }, [result]);

  const running = !!status?.running;
  const estCost = Math.ceil((form.days * 24) / form.step_hours);

  return (
    <div className="sniper-container" style={{ gap: '1.25rem' }}>
      {/* 1. Header Banner */}
      <div className="sniper-header" style={{
        background: 'linear-gradient(135deg, rgba(15, 23, 42, 0.9), rgba(30, 41, 59, 0.7))',
        border: '1px solid rgba(6, 182, 212, 0.25)',
        borderRadius: '12px',
        padding: '1.25rem 1.5rem',
        boxShadow: '0 8px 32px rgba(0, 0, 0, 0.3)',
        backdropFilter: 'blur(12px)'
      }}>
        <div className="sniper-header-left">
          <div className="sniper-icon-badge" style={{
            background: 'rgba(6, 182, 212, 0.12)',
            border: '1px solid rgba(6, 182, 212, 0.3)',
            color: '#06B6D4'
          }}>
            <History size={26} />
          </div>
          <div className="sniper-title-box">
            <div className="sniper-title-row" style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
              <h2 className="sniper-title" style={{ fontSize: '1.25rem', fontWeight: 800 }}>📈 历史回测实验室</h2>
              <span className="badge-tag" style={{ background: 'rgba(6, 182, 212, 0.12)', color: '#06B6D4', borderColor: 'rgba(6, 182, 212, 0.3)' }}>
                WALK-FORWARD 逐根回放
              </span>
            </div>
            <p className="sniper-subtext" style={{ marginTop: '0.2rem', color: 'var(--text-muted)', fontSize: '0.82rem' }}>
              用真实历史行情驱动完整生产链路（指标 → LLM 诊断 → 狙击引擎模拟成交），在不花一分钱本金的前提下验证策略收益率与胜率
            </p>
          </div>
        </div>
      </div>

      {/* 2. Notice Callout */}
      <div style={{
        padding: '0.85rem 1.25rem',
        borderRadius: '10px',
        background: 'rgba(245, 158, 11, 0.06)',
        border: '1px solid rgba(245, 158, 11, 0.25)',
        color: '#F59E0B',
        fontSize: '0.82rem',
        display: 'flex',
        alignItems: 'center',
        gap: '0.6rem',
        lineHeight: '1.5'
      }}>
        <AlertCircle size={18} style={{ flexShrink: 0 }} />
        <div>
          回测使用<strong>真实 LLM API 调用</strong>：按当前参数预计消耗约 <strong>{estCost}</strong> 次诊断（受上限 {form.max_llm_calls} 约束）。模拟盘成交滑点、杠杆安全帽与生产环境完全一致。
        </div>
      </div>

      {/* 3. Parameter Controls Form Panel */}
      <div className="panel" style={{ padding: '1.25rem' }}>
        <div style={{ fontSize: '0.92rem', fontWeight: 700, color: 'var(--text-bright)', marginBottom: '1rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <Filter size={16} style={{ color: '#06B6D4' }} />
          <span>回测实验室参数配置</span>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: '1rem', alignItems: 'end' }}>
          {/* Symbol */}
          <div className="form-group">
            <label className="form-label" style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>回测币对</label>
            <select
              value={form.symbol}
              onChange={e => setForm({ ...form, symbol: e.target.value })}
              className="form-control"
              disabled={running}
              style={{ background: 'rgba(15, 23, 42, 0.8)', borderColor: 'var(--border-color)' }}
            >
              {(symbols.length ? symbols : [form.symbol]).map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>

          {/* Days */}
          <div className="form-group">
            <label className="form-label" style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>回测天数 (≤90 天)</label>
            <input
              type="number"
              min="1"
              max="90"
              value={form.days}
              disabled={running}
              onChange={e => setForm({ ...form, days: Math.max(1, parseInt(e.target.value) || 1) })}
              className="form-control"
              style={{ background: 'rgba(15, 23, 42, 0.8)', borderColor: 'var(--border-color)' }}
            />
          </div>

          {/* Step Hours */}
          <div className="form-group">
            <label className="form-label" style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>诊断步长 (小时)</label>
            <input
              type="number"
              min="1"
              max="24"
              value={form.step_hours}
              disabled={running}
              onChange={e => setForm({ ...form, step_hours: Math.max(1, parseInt(e.target.value) || 1) })}
              className="form-control"
              style={{ background: 'rgba(15, 23, 42, 0.8)', borderColor: 'var(--border-color)' }}
            />
          </div>

          {/* Max LLM Calls */}
          <div className="form-group">
            <label className="form-label" style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>LLM 调用上限 (次)</label>
            <input
              type="number"
              min="1"
              max="500"
              value={form.max_llm_calls}
              disabled={running}
              onChange={e => setForm({ ...form, max_llm_calls: Math.max(1, parseInt(e.target.value) || 1) })}
              className="form-control"
              style={{ background: 'rgba(15, 23, 42, 0.8)', borderColor: 'var(--border-color)' }}
            />
          </div>

          {/* Initial Balance */}
          <div className="form-group">
            <label className="form-label" style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>初始模拟本金 (USD)</label>
            <input
              type="number"
              min="10"
              value={form.initial_balance}
              disabled={running}
              onChange={e => setForm({ ...form, initial_balance: Math.max(10, parseFloat(e.target.value) || 10000) })}
              className="form-control"
              style={{ background: 'rgba(15, 23, 42, 0.8)', borderColor: 'var(--border-color)' }}
            />
          </div>

          {/* Action Trigger Button */}
          <div className="form-group">
            {!running ? (
              <button
                onClick={startBacktest}
                className="btn btn-primary"
                style={{
                  width: '100%',
                  padding: '0.65rem 1.25rem',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: '0.5rem',
                  background: 'linear-gradient(135deg, #06B6D4, #2563EB)',
                  fontWeight: 700,
                  fontSize: '0.88rem'
                }}
              >
                <Play size={16} />
                <span>🚀 启动回测实验室</span>
              </button>
            ) : (
              <button
                onClick={stopBacktest}
                className="btn btn-secondary"
                style={{
                  width: '100%',
                  padding: '0.65rem 1.25rem',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: '0.5rem',
                  color: '#EF4444',
                  borderColor: 'rgba(239, 68, 68, 0.4)',
                  fontWeight: 700,
                  fontSize: '0.88rem'
                }}
              >
                <Square size={15} />
                <span>终止回测</span>
              </button>
            )}
          </div>
        </div>

        {/* Live Progress Bar when Running */}
        {running && status && (
          <div style={{ marginTop: '1.25rem', paddingTop: '1rem', borderTop: '1px solid var(--border-color)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.82rem', color: 'var(--text-bright)', marginBottom: '0.4rem' }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                <RefreshCw size={14} className="spinner" style={{ color: '#06B6D4' }} />
                <span>{status.message || '正在逐根回放历史 K 线...'}</span>
              </span>
              <strong style={{ color: '#06B6D4' }}>{status.progress_pct}%</strong>
            </div>
            <div style={{ height: '8px', borderRadius: '4px', background: 'rgba(15, 23, 42, 0.8)', overflow: 'hidden', border: '1px solid var(--border-color)' }}>
              <div style={{
                height: '100%',
                width: `${status.progress_pct}%`,
                background: 'linear-gradient(90deg, #06B6D4, #10B981)',
                transition: 'width 0.4s ease',
                boxShadow: '0 0 12px rgba(6, 182, 212, 0.5)'
              }}></div>
            </div>
          </div>
        )}

        {/* Error message */}
        {error && (
          <div style={{ marginTop: '1rem', padding: '0.75rem', borderRadius: '6px', background: 'rgba(239, 68, 68, 0.1)', border: '1px solid rgba(239, 68, 68, 0.3)', color: '#F87171', fontSize: '0.82rem' }}>
            ❌ {error}
          </div>
        )}
      </div>

      {/* 4. Results Dashboard Card */}
      {result && (
        <>
          <div className="sniper-grid">
            {/* Net Profit */}
            <div className="sniper-card" style={{
              background: 'linear-gradient(135deg, rgba(15, 23, 42, 0.8), rgba(30, 41, 59, 0.6))',
              border: `1px solid ${result.net_profit_usd >= 0 ? 'rgba(16, 185, 129, 0.3)' : 'rgba(239, 68, 68, 0.3)'}`
            }}>
              <div className="sniper-card-header">
                <span>回测净收益</span>
                <TrendingUp size={18} style={{ color: result.net_profit_usd >= 0 ? '#10B981' : '#EF4444' }} />
              </div>
              <div className="sniper-card-val" style={{ color: result.net_profit_usd >= 0 ? '#10B981' : '#EF4444', fontSize: '1.4rem' }}>
                {result.net_profit_usd >= 0 ? '+' : ''}${result.net_profit_usd}
                <span style={{ fontSize: '0.9rem', marginLeft: '0.4rem', opacity: 0.85 }}>({result.net_profit_percent}%)</span>
              </div>
              <div className="sniper-card-sub">
                期末账户总余额: <strong style={{ color: 'var(--text-bright)' }}>${result.final_balance} USD</strong>
              </div>
            </div>

            {/* Win Rate & Profit Factor */}
            <div className="sniper-card" style={{
              background: 'linear-gradient(135deg, rgba(15, 23, 42, 0.8), rgba(30, 41, 59, 0.6))',
              border: '1px solid rgba(245, 158, 11, 0.3)'
            }}>
              <div className="sniper-card-header">
                <span>交易胜率 / 盈亏比 (PF)</span>
                <Award size={18} style={{ color: '#F59E0B' }} />
              </div>
              <div className="sniper-card-val" style={{ color: '#F59E0B', fontSize: '1.4rem' }}>
                {result.win_rate}%
                <span style={{ fontSize: '0.9rem', marginLeft: '0.4rem', color: 'var(--text-bright)' }}>/ PF {result.profit_factor}</span>
              </div>
              <div className="sniper-card-sub">
                {result.winning_trades_count} 胜 / {result.losing_trades_count} 负 (共计 {result.total_trades_count} 单)
              </div>
            </div>

            {/* Max Drawdown */}
            <div className="sniper-card" style={{
              background: 'linear-gradient(135deg, rgba(15, 23, 42, 0.8), rgba(30, 41, 59, 0.6))',
              border: '1px solid rgba(239, 68, 68, 0.3)'
            }}>
              <div className="sniper-card-header">
                <span>最大回撤 (Max DD)</span>
                <Activity size={18} style={{ color: '#EF4444' }} />
              </div>
              <div className="sniper-card-val" style={{ color: result.max_drawdown_percent > 10 ? '#EF4444' : '#2979FF', fontSize: '1.4rem' }}>
                -{result.max_drawdown_percent}%
              </div>
              <div className="sniper-card-sub">
                历史最大回撤金额: <strong style={{ color: 'var(--text-bright)' }}>-${result.max_drawdown_usd} USD</strong>
              </div>
            </div>

            {/* Cost & LLM Usage */}
            <div className="sniper-card" style={{
              background: 'linear-gradient(135deg, rgba(15, 23, 42, 0.8), rgba(30, 41, 59, 0.6))',
              border: '1px solid rgba(168, 85, 247, 0.3)'
            }}>
              <div className="sniper-card-header">
                <span>交易手续费与 LLM 消耗</span>
                <Coins size={18} style={{ color: '#A855F7' }} />
              </div>
              <div className="sniper-card-val" style={{ color: '#A855F7', fontSize: '1.4rem' }}>
                ${result.total_fees_usd}
              </div>
              <div className="sniper-card-sub">
                LLM 诊察点: <strong style={{ color: 'var(--text-bright)' }}>{result.llm_calls_used} 次</strong>
                {result.llm_budget_exhausted ? ' (上限拦截已触及)' : ''}
              </div>
            </div>
          </div>

          {/* Equity curve chart card */}
          <div className="panel" style={{ padding: '1.25rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.8rem' }}>
              <div style={{ fontSize: '0.92rem', fontWeight: 700, color: 'var(--text-bright)', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <BarChart2 size={16} style={{ color: '#10B981' }} />
                <span>回测净权益增长曲线（含手续费与滑点）</span>
              </div>
              <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>虚线为初始本金水位 ($10,000)</span>
            </div>
            <div style={{ background: 'rgba(15, 23, 42, 0.6)', borderRadius: '8px', padding: '0.5rem', border: '1px solid var(--border-color)' }}>
              <canvas ref={canvasRef} style={{ width: '100%', height: '180px' }}></canvas>
            </div>
          </div>

          {/* Trade Executions Table */}
          <div className="panel" style={{ padding: 0, overflow: 'hidden' }}>
            <div style={{ padding: '1rem 1.25rem', borderBottom: '1px solid var(--border-color)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: 'rgba(15, 23, 42, 0.6)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <Layers size={16} style={{ color: '#06B6D4' }} />
                <h3 style={{ fontSize: '0.95rem', fontWeight: 700, color: 'var(--text-bright)', margin: 0 }}>
                  模拟成交记录明细 ({result.trades.length} 笔)
                </h3>
              </div>
            </div>

            <div className="sniper-table-wrapper" style={{ maxHeight: '420px', overflowY: 'auto' }}>
              <table className="sniper-table">
                <thead>
                  <tr>
                    <th>成交时间</th>
                    <th>方向</th>
                    <th>状态</th>
                    <th>杠杆</th>
                    <th>建仓均价</th>
                    <th>防守止损</th>
                    <th>净盈亏 (USD)</th>
                    <th>预估手续费</th>
                    <th>离场触发原因</th>
                  </tr>
                </thead>
                <tbody>
                  {result.trades.length === 0 ? (
                    <tr>
                      <td colSpan="9" style={{ textAlign: 'center', color: 'var(--text-muted)', padding: '2rem' }}>
                        回测期间内大模型未触发开单条件或全部诊察处于观望状态
                      </td>
                    </tr>
                  ) : result.trades.map(t => {
                    const isLong = t.signal_type === 'long';
                    const isWin = (t.pnl_usd || 0) >= 0;
                    return (
                      <tr key={t.id} style={{ background: isWin ? 'rgba(16, 185, 129, 0.02)' : 'rgba(239, 68, 68, 0.02)' }}>
                        <td style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{t.entered_at}</td>
                        <td>
                          <span style={{
                            fontSize: '0.75rem',
                            fontWeight: 700,
                            padding: '0.15rem 0.5rem',
                            borderRadius: '4px',
                            color: isLong ? '#10B981' : '#EF4444',
                            background: isLong ? 'rgba(16, 185, 129, 0.12)' : 'rgba(239, 68, 68, 0.12)',
                            border: `1px solid ${isLong ? 'rgba(16, 185, 129, 0.3)' : 'rgba(239, 68, 68, 0.3)'}`
                          }}>
                            {isLong ? '📈 做多 (LONG)' : '📉 做空 (SHORT)'}
                          </span>
                        </td>
                        <td style={{ fontSize: '0.78rem' }}>{t.status}</td>
                        <td style={{ fontWeight: 600 }}>{t.leverage}x</td>
                        <td style={{ fontFamily: 'monospace' }}>${t.actual_entry || t.planned_entry}</td>
                        <td style={{ color: '#EF4444', fontFamily: 'monospace' }}>${t.initial_stop_loss || t.stop_loss}</td>
                        <td>
                          <strong style={{ color: isWin ? '#10B981' : '#EF4444', fontSize: '0.92rem' }}>
                            {isWin ? '+' : ''}${t.pnl_usd}
                          </strong>
                        </td>
                        <td style={{ color: 'var(--text-muted)', fontSize: '0.78rem' }}>${t.fees_usd || 0}</td>
                        <td style={{ fontSize: '0.75rem', color: 'var(--text-muted)', maxWidth: '240px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={t.close_reason}>
                          {t.close_reason || '—'}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* Simulation Notes */}
          <div style={{ padding: '0.85rem 1.25rem', borderRadius: '10px', background: 'rgba(15, 23, 42, 0.6)', border: '1px solid var(--border-color)', fontSize: '0.78rem', color: 'var(--text-muted)', lineHeight: '1.6' }}>
            <div style={{ color: 'var(--text-bright)', fontWeight: 600, marginBottom: '0.3rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
              <ShieldAlert size={15} style={{ color: '#06B6D4' }} />
              <span>回测风控与撮合口径依据</span>
            </div>
            {(result.simulation_notes || []).map((n, i) => (
              <div key={i}>• {n}</div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
