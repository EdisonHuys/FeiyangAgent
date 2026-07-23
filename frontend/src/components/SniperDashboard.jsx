import React, { useState, useEffect, useRef } from 'react';
import { Target, TrendingUp, ShieldAlert, Award, Activity, Settings, RefreshCw, Layers, CheckCircle2, AlertCircle, Key, Lock, Server, RotateCcw } from 'lucide-react';

export default function SniperDashboard({ apiBase }) {
  const [dashboardData, setDashboardData] = useState(null);
  const [trades, setTrades] = useState([]);
  const [loading, setLoading] = useState(true);
  const [savingConfig, setSavingConfig] = useState(false);
  const [showConfigModal, setShowConfigModal] = useState(false);
  const [activeSubTab, setActiveSubTab] = useState('general'); // 'general' or 'exchange'

  // Saved Config from backend
  const [formConfig, setFormConfig] = useState({
    mode: 'paper',
    account_balance: 10000,
    risk_per_trade_percent: 2.0,
    max_active_trades: 3,
    min_confidence: 7,
    max_leverage: 15,
    live_exchange: 'binance',
    live_api_key: '',
    live_secret: '',
    live_passphrase: '',
    live_trading_mode: 'swap'
  });

  // Modal Draft Config (completely isolated from background polling)
  const [modalConfig, setModalConfig] = useState({ ...formConfig });

  const [testingExchange, setTestingExchange] = useState(false);
  const [exchangeTestResult, setExchangeTestResult] = useState(null);

  const canvasRef = useRef(null);

  const fetchData = async () => {
    try {
      setLoading(true);
      const [dashRes, tradesRes] = await Promise.all([
        fetch(`${apiBase}/api/sniper/dashboard`),
        fetch(`${apiBase}/api/sniper/trades`)
      ]);
      const dashJson = await dashRes.json();
      const tradesJson = await tradesRes.json();

      setDashboardData(dashJson);
      if (dashJson.config) {
        setFormConfig(prev => ({ ...prev, ...dashJson.config }));
      }
      setTrades(tradesJson.trades || []);
    } catch (err) {
      console.error("Failed to fetch sniper data:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 10000);
    return () => clearInterval(interval);
  }, [apiBase]);

  // Open modal with fresh copy of formConfig
  const handleOpenModal = () => {
    setModalConfig({ ...formConfig });
    setExchangeTestResult(null);
    setShowConfigModal(true);
  };

  // Draw PnL Cumulative Trend Chart on Canvas
  useEffect(() => {
    if (!canvasRef.current || !trades) return;
    const canvas = canvasRef.current;
    const ctx = canvas.getContext('2d');
    const width = canvas.width;
    const height = canvas.height;

    ctx.clearRect(0, 0, width, height);

    const closed = trades.filter(t => ['closed_tp', 'closed_sl'].includes(t.status)).reverse();
    if (closed.length === 0) {
      ctx.fillStyle = '#8892b0';
      ctx.font = '13px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('暂无平仓样本数据，收益曲线待生成...', width / 2, height / 2);
      return;
    }

    let cum = 0;
    const points = [0];
    closed.forEach(t => {
      cum += (t.pnl_usd || 0);
      points.push(cum);
    });

    const maxVal = Math.max(0, ...points);
    const minVal = Math.min(0, ...points);
    const range = (maxVal - minVal) || 100;

    const padding = 30;
    const chartW = width - padding * 2;
    const chartH = height - padding * 2;

    ctx.strokeStyle = 'rgba(255, 255, 255, 0.05)';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = padding + (chartH / 4) * i;
      ctx.beginPath();
      ctx.moveTo(padding, y);
      ctx.lineTo(width - padding, y);
      ctx.stroke();
    }

    const pts = points.map((val, idx) => {
      const x = padding + (chartW / (points.length - 1 || 1)) * idx;
      const y = height - padding - ((val - minVal) / range) * chartH;
      return { x, y, val };
    });

    const gradient = ctx.createLinearGradient(0, padding, 0, height - padding);
    const isProfitable = points[points.length - 1] >= 0;
    if (isProfitable) {
      gradient.addColorStop(0, 'rgba(0, 230, 118, 0.3)');
      gradient.addColorStop(1, 'rgba(0, 230, 118, 0.0)');
    } else {
      gradient.addColorStop(0, 'rgba(255, 23, 68, 0.3)');
      gradient.addColorStop(1, 'rgba(255, 23, 68, 0.0)');
    }

    ctx.beginPath();
    ctx.moveTo(pts[0].x, pts[0].y);
    pts.forEach(p => ctx.lineTo(p.x, p.y));
    ctx.lineTo(pts[pts.length - 1].x, height - padding);
    ctx.lineTo(pts[0].x, height - padding);
    ctx.closePath();
    ctx.fillStyle = gradient;
    ctx.fill();

    ctx.beginPath();
    ctx.moveTo(pts[0].x, pts[0].y);
    pts.forEach(p => ctx.lineTo(p.x, p.y));
    ctx.strokeStyle = isProfitable ? '#00e676' : '#ff1744';
    ctx.lineWidth = 2.5;
    ctx.stroke();

    pts.forEach((p, idx) => {
      ctx.beginPath();
      ctx.arc(p.x, p.y, 4, 0, Math.PI * 2);
      ctx.fillStyle = isProfitable ? '#00e676' : '#ff1744';
      ctx.fill();
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1.5;
      ctx.stroke();
    });

  }, [trades]);

  const handleSaveConfig = async (updatedFields) => {
    try {
      setSavingConfig(true);
      const sanitized = { ...updatedFields };
      if ('risk_per_trade_percent' in sanitized) sanitized.risk_per_trade_percent = parseFloat(sanitized.risk_per_trade_percent) || 2.0;
      if ('max_leverage' in sanitized) sanitized.max_leverage = parseInt(sanitized.max_leverage) || 15;
      if ('max_active_trades' in sanitized) sanitized.max_active_trades = parseInt(sanitized.max_active_trades) || 3;
      if ('min_confidence' in sanitized) sanitized.min_confidence = parseInt(sanitized.min_confidence) || 7;

      const res = await fetch(`${apiBase}/api/sniper/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(sanitized)
      });
      const resJson = await res.json();
      if (resJson.status === 'success') {
        setFormConfig(prev => ({ ...prev, ...resJson.config }));
        fetchData();
      }
    } catch (err) {
      console.error("Failed to update sniper config:", err);
    } finally {
      setSavingConfig(false);
    }
  };

  const handleModeChange = (newMode) => {
    setFormConfig(prev => ({ ...prev, mode: newMode }));
    handleSaveConfig({ mode: newMode });
  };

  const handleTestExchange = async () => {
    if (!modalConfig.live_api_key || !modalConfig.live_secret) {
      setExchangeTestResult({ status: 'error', message: '请先填写 API Key 和 API Secret 之后再测试连通性。' });
      return;
    }
    try {
      setTestingExchange(true);
      setExchangeTestResult(null);
      const res = await fetch(`${apiBase}/api/sniper/test-exchange-api`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          exchange_id: modalConfig.live_exchange,
          api_key: modalConfig.live_api_key,
          secret: modalConfig.live_secret,
          passphrase: modalConfig.live_passphrase
        })
      });
      const data = await res.json();
      setExchangeTestResult(data);
    } catch (err) {
      setExchangeTestResult({ status: 'error', message: `测试请求异常：${err.message}` });
    } finally {
      setTestingExchange(false);
    }
  };

  const handleCloseTrade = async (tradeId, symbol) => {
    if (!window.confirm(`确定要手动市价平仓/撤销 ${symbol} 的单据吗？`)) return;
    try {
      const res = await fetch('/api/sniper/close-trade', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ trade_id: tradeId })
      });
      const data = await res.json();
      if (data.status === 'success') {
        fetchData();
      } else {
        alert(`操作失败：${data.message}`);
      }
    } catch (err) {
      alert(`网络请求失败: ${err.message}`);
    }
  };

  const handleResetPaper = async () => {
    const inputVal = window.prompt("请输入重新开局的模拟初始资金金额 (USD)：", "10000");
    if (inputVal === null) return;
    const initialBal = parseFloat(inputVal) || 10000.0;
    
    if (!window.confirm(`确认要清空所有模拟盘记录，并将初始模拟资金重置为 $${initialBal} USD 吗？`)) return;
    try {
      const res = await fetch('/api/sniper/reset-paper', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ initial_balance: initialBal })
      });
      const data = await res.json();
      if (data.status === 'success') {
        alert(data.message);
        fetchData();
      } else {
        alert(`重置失败：${data.message}`);
      }
    } catch (err) {
      alert(`重置请求失败: ${err.message}`);
    }
  };

  if (loading && !dashboardData) {
    return (
      <div className="loader-wrapper py-20">
        <div className="spinner"></div>
        <p>正在加载飞扬精准狙击系统仪表盘...</p>
      </div>
    );
  }

  const mode = dashboardData?.mode || formConfig.mode || 'paper';
  const netProfit = dashboardData?.net_profit_usd || 0;
  const winRate = dashboardData?.win_rate || 0;

  return (
    <div className="sniper-container">
      {/* 1. Top Header & Control Center */}
      <div className="sniper-header">
        <div className="sniper-header-left">
          <div className="sniper-icon-badge">
            <Target size={28} />
          </div>
          <div className="sniper-title-box">
            <div className="sniper-title-row">
              <h2 className="sniper-title">🎯 飞扬精准狙击系统</h2>
              <span className="badge-tag">防御型右侧建仓埋伏</span>
            </div>
            <p className="sniper-subtext">
              多周期共振共识 | 动态保本推损 (Break-Even) | 风险平价杠杆风控
            </p>
          </div>
        </div>

        {/* Master Switcher */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'nowrap' }}>
          <div className="sniper-mode-bar">
            <button
              onClick={() => handleModeChange('off')}
              className={`sniper-mode-btn ${mode === 'off' ? 'active-off' : ''}`}
            >
              🔴 总控关闭
            </button>
            <button
              onClick={() => handleModeChange('paper')}
              className={`sniper-mode-btn ${mode === 'paper' ? 'active-paper' : ''}`}
            >
              🟡 模拟盘交易
            </button>
            <button
              onClick={() => handleModeChange('live')}
              className={`sniper-mode-btn ${mode === 'live' ? 'active-live' : ''}`}
            >
              🟢 实盘 API 交易
            </button>
          </div>

          {mode === 'paper' && (
            <button
              onClick={handleResetPaper}
              className="btn btn-secondary"
              style={{ padding: '0.4rem 0.65rem', fontSize: '0.78rem', display: 'flex', alignItems: 'center', gap: '0.3rem', color: '#EF4444', borderColor: 'rgba(239,68,68,0.3)', whiteSpace: 'nowrap' }}
              title="清空模拟盘履约与盈亏曲线，重新设置初始资金"
            >
              <RotateCcw size={14} />
              <span>重置模拟本金</span>
            </button>
          )}

          <button
            onClick={handleOpenModal}
            className="btn btn-secondary"
            style={{ padding: '0.4rem 0.65rem', fontSize: '0.78rem', display: 'flex', alignItems: 'center', gap: '0.35rem', whiteSpace: 'nowrap' }}
          >
            <Settings size={15} />
            <span>狙击与平台配置</span>
          </button>

          <button
            onClick={fetchData}
            className="btn btn-secondary"
            style={{ padding: '0.4rem', display: 'flex', alignItems: 'center' }}
            title="刷新数据"
          >
            <RefreshCw size={15} className={loading ? 'spinner' : ''} />
          </button>
        </div>
      </div>

      {/* 2. Key Telemetry Metric Cards */}
      <div className="sniper-grid">
        {/* Win Rate */}
        <div className="sniper-card">
          <div className="sniper-card-header">
            <span>交易胜率 (Win Rate)</span>
            <Award size={18} style={{ color: 'var(--color-warning)' }} />
          </div>
          <div className="sniper-card-val" style={{ color: 'var(--color-warning)' }}>
            {winRate}%
          </div>
          <div className="sniper-card-sub">
            样本数据: {dashboardData?.winning_trades_count || 0} 胜 / {dashboardData?.total_trades_count || 0} 单
          </div>
        </div>

        {/* Net Profit */}
        <div className="sniper-card">
          <div className="sniper-card-header">
            <span>已实现累计净收益</span>
            <TrendingUp size={18} style={{ color: netProfit >= 0 ? 'var(--color-long)' : 'var(--color-short)' }} />
          </div>
          <div className="sniper-card-val" style={{ color: netProfit >= 0 ? 'var(--color-long)' : 'var(--color-short)' }}>
            {netProfit >= 0 ? '+' : ''}${netProfit.toFixed(2)} USD
          </div>
          <div className="sniper-card-sub">
            可用账户余额: <strong style={{ color: 'var(--text-bright)' }}>${dashboardData?.account_balance}</strong>
          </div>
        </div>

        {/* Leverage & Risk */}
        <div className="sniper-card">
          <div className="sniper-card-header">
            <span>风控偏好与杠杆模式</span>
            <ShieldAlert size={18} style={{ color: 'var(--color-long)' }} />
          </div>
          <div className="sniper-card-val" style={{ color: 'var(--color-long)' }}>
            {formConfig.leverage_mode === 'fixed' ? `${formConfig.fixed_leverage || 50}x (固定)` : `${formConfig.min_leverage || 35}-${formConfig.max_leverage || 70}x (智能)`}
          </div>
          <div className="sniper-card-sub">
            单笔硬性风控上限: <strong style={{ color: 'var(--text-bright)' }}>{formConfig.risk_per_trade_percent}%</strong>
          </div>
        </div>

        {/* Active Positions */}
        <div className="sniper-card">
          <div className="sniper-card-header">
            <span>实时埋伏/活跃仓位</span>
            <Layers size={18} style={{ color: '#e040fb' }} />
          </div>
          <div className="sniper-card-val" style={{ color: '#e040fb' }}>
            {dashboardData?.active_positions_count || 0} <span style={{ fontSize: '0.9rem', color: 'var(--text-muted)' }}>/ 最多 {formConfig.max_active_trades} 单</span>
          </div>
          <div className="sniper-card-sub">
            平台及引擎: <strong style={{ color: 'var(--text-bright)' }}>{(formConfig.live_exchange || 'binance').toUpperCase()} ({mode.toUpperCase()})</strong>
          </div>
        </div>
      </div>

      {/* 3. Real-Time Active Open Positions Section (Moved to Top) */}
      {(() => {
        const activePositions = trades.filter(t => t.status === 'filled' || t.status === 'tp1_hit');
        return (
          <div className="sniper-panel" style={{ padding: 0, overflow: 'hidden', border: '1px solid rgba(16, 185, 129, 0.3)', boxShadow: '0 4px 20px rgba(0, 230, 118, 0.05)' }}>
            <div style={{ padding: '1rem 1.25rem', borderBottom: '1px solid var(--border-color)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: 'rgba(0, 230, 118, 0.04)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
                <div style={{ width: '10px', height: '10px', borderRadius: '50%', background: activePositions.length > 0 ? '#10B981' : '#64748B', boxShadow: activePositions.length > 0 ? '0 0 12px #10B981' : 'none' }}></div>
                <h3 style={{ fontSize: '1rem', fontWeight: '700', color: 'var(--text-bright)', margin: 0 }}>
                  🔥 实时活跃持仓看板 ({activePositions.length})
                </h3>
              </div>
              <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>10秒毫秒级实时价格监控与浮盈测算</span>
            </div>

            {activePositions.length === 0 ? (
              <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
                当前无持仓中的单子。大模型下发策略且行情价格回踩/冲高进入埋伏区间后，将自动开单并在此呈现。
              </div>
            ) : (
              <div className="sniper-table-wrapper">
                <table className="sniper-table">
                  <thead>
                    <tr>
                      <th>建仓时间</th>
                      <th>币对 / 方向</th>
                      <th>状态</th>
                      <th>杠杆 / 保证金</th>
                      <th>建仓均价</th>
                      <th style={{ color: 'var(--color-wait)' }}>实时标记价格</th>
                      <th>防守线 (止损)</th>
                      <th>阶梯止盈 (TP)</th>
                      <th>未实现浮动盈亏 (PnL)</th>
                      <th style={{ textAlign: 'right' }}>操作管理</th>
                    </tr>
                  </thead>
                  <tbody>
                    {activePositions.map(t => {
                      const isLong = t.signal_type.toLowerCase() === 'long';
                      return (
                        <tr key={t.id} style={{ background: 'rgba(16, 185, 129, 0.02)' }}>
                          <td style={{ color: 'var(--text-muted)' }}>{t.entered_at}</td>
                          <td>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                              <strong style={{ color: 'var(--text-bright)' }}>{t.symbol}</strong>
                              <span className={`badge-tag ${isLong ? '' : 'badge-short'}`} style={{
                                background: isLong ? 'rgba(0, 230, 118, 0.12)' : 'rgba(255, 23, 68, 0.12)',
                                color: isLong ? 'var(--color-long)' : 'var(--color-short)',
                                borderColor: isLong ? 'rgba(0, 230, 118, 0.3)' : 'rgba(255, 23, 68, 0.3)'
                              }}>
                                {isLong ? '做多 (LONG)' : '做空 (SHORT)'}
                              </span>
                            </div>
                          </td>

                          <td>
                            {t.status === 'filled' && <span className="badge-status filled">📈 持仓中 (埋伏成功)</span>}
                            {t.status === 'tp1_hit' && <span className="badge-status tp1_hit">🎯 触及 TP1 (保本防御)</span>}
                          </td>

                          <td>
                            <strong style={{ color: 'var(--color-wait)' }}>{t.leverage}x</strong>
                            <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginLeft: '4px' }}>(${t.margin_usd})</span>
                          </td>

                          <td style={{ fontWeight: '600' }}>
                            ${t.actual_entry || t.planned_entry}
                          </td>

                          {/* Explicit Real-Time Price Column */}
                          <td>
                            <strong style={{ color: '#F59E0B', fontFamily: 'monospace', fontSize: '0.95rem' }}>
                              ${t.current_price || '-'}
                            </strong>
                          </td>

                          <td style={{ color: 'var(--color-short)', fontWeight: '600' }}>
                            ${t.stop_loss}
                            {t.tp1_partial_closed && (
                              <span style={{ fontSize: '0.65rem', background: 'rgba(0,230,118,0.15)', color: 'var(--color-long)', padding: '1px 4px', borderRadius: '3px', marginLeft: '4px' }}>
                                已锁保本位
                              </span>
                            )}
                          </td>

                          <td style={{ color: 'var(--color-long)' }}>
                            {t.take_profit_targets?.map(tp => `$${tp}`).join(', ')}
                          </td>

                          <td style={{ fontWeight: 'bold' }}>
                            {t.unrealized_pnl_usd !== undefined ? (
                              <span style={{
                                color: t.unrealized_pnl_usd >= 0 ? '#10B981' : '#EF4444',
                                fontSize: '0.95rem'
                              }}>
                                {t.unrealized_pnl_usd >= 0 ? '+' : ''}${t.unrealized_pnl_usd} ({t.unrealized_pnl_percent >= 0 ? '+' : ''}{t.unrealized_pnl_percent}%)
                              </span>
                            ) : (
                              <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>计算中...</span>
                            )}
                          </td>

                          <td style={{ textAlign: 'right' }}>
                            <button
                              onClick={() => handleCloseTrade(t.id, t.symbol)}
                              style={{
                                background: 'rgba(239, 68, 68, 0.15)',
                                color: '#EF4444',
                                border: '1px solid rgba(239, 68, 68, 0.4)',
                                borderRadius: '6px',
                                padding: '3px 8px',
                                fontSize: '0.75rem',
                                cursor: 'pointer',
                                fontWeight: '600'
                              }}
                              title="手动市价平仓离场"
                            >
                              ✋ 手动平仓
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        );
      })()}

      {/* 4. Visual Charts Section */}
      <div className="sniper-chart-section">
        {/* Cumulative Profit Trend Line Chart */}
        <div className="sniper-panel">
          <div className="sniper-panel-title">
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <TrendingUp size={18} style={{ color: 'var(--color-long)' }} />
              <span>累计收益轨迹曲线 (Cumulative PnL Curve)</span>
            </div>
            <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>实时演算点位履约曲线</span>
          </div>
          <canvas
            ref={canvasRef}
            width={700}
            height={200}
            style={{ width: '100%', height: '200px', background: 'rgba(0,0,0,0.2)', borderRadius: '8px' }}
          />
        </div>

        {/* Win/Loss Analytics Bar */}
        <div className="sniper-panel">
          <div className="sniper-panel-title">
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <Award size={18} style={{ color: 'var(--color-warning)' }} />
              <span>胜负概率比例</span>
            </div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem', justifyContent: 'center', height: '100%' }}>
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem', marginBottom: '0.4rem' }}>
                <span style={{ color: 'var(--color-long)' }}>盈利单 (Win)</span>
                <span style={{ color: 'var(--color-long)' }}>{dashboardData?.winning_trades_count || 0} 笔</span>
              </div>
              <div style={{ height: '8px', background: 'rgba(255,255,255,0.05)', borderRadius: '4px', overflow: 'hidden' }}>
                <div style={{ height: '100%', width: `${winRate}%`, background: 'var(--color-long)', borderRadius: '4px' }} />
              </div>
            </div>

            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem', marginBottom: '0.4rem' }}>
                <span style={{ color: 'var(--color-short)' }}>止损单 (Loss)</span>
                <span style={{ color: 'var(--color-short)' }}>{dashboardData?.losing_trades_count || 0} 笔</span>
              </div>
              <div style={{ height: '8px', background: 'rgba(255,255,255,0.05)', borderRadius: '4px', overflow: 'hidden' }}>
                <div style={{ height: '100%', width: `${100 - winRate}%`, background: 'var(--color-short)', borderRadius: '4px' }} />
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* 5. Pending Orders & History Trades Table */}
      <div className="sniper-panel" style={{ padding: 0, overflow: 'hidden' }}>
        <div style={{ padding: '1rem 1.25rem', borderBottom: '1px solid var(--border-color)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontWeight: '600' }}>
            <Target size={18} style={{ color: 'var(--color-wait)' }} />
            <span>📋 狙击挂单埋伏与履约历史记录</span>
          </div>
          <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>已平仓单与埋伏中挂单统一管理</span>
        </div>

        {(() => {
          const pendingAndHistoryTrades = trades.filter(t => t.status !== 'filled' && t.status !== 'tp1_hit');

          if (pendingAndHistoryTrades.length === 0) {
            return (
              <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-muted)', fontSize: '0.82rem' }}>
                暂无挂单中或已结平的历史数据。
              </div>
            );
          }

          return (
            <div className="sniper-table-wrapper">
              <table className="sniper-table">
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>币对 / 方向</th>
                    <th>履约状态</th>
                    <th>杠杆 / 保证金</th>
                    <th>预设吃单点 / 区间</th>
                    <th style={{ color: 'var(--color-wait)' }}>实时币价</th>
                    <th>防守线 (止损)</th>
                    <th>阶梯止盈 (TP)</th>
                    <th style={{ textAlign: 'right' }}>已结算盈亏 (PnL)</th>
                  </tr>
                </thead>
                <tbody>
                  {pendingAndHistoryTrades.map(t => {
                    const isLong = t.signal_type.toLowerCase() === 'long';
                    return (
                      <tr key={t.id}>
                        <td style={{ color: 'var(--text-muted)' }}>{t.entered_at}</td>
                        <td>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                            <strong style={{ color: 'var(--text-bright)' }}>{t.symbol}</strong>
                            <span className={`badge-tag ${isLong ? '' : 'badge-short'}`} style={{
                              background: isLong ? 'rgba(0, 230, 118, 0.12)' : 'rgba(255, 23, 68, 0.12)',
                              color: isLong ? 'var(--color-long)' : 'var(--color-short)',
                              borderColor: isLong ? 'rgba(0, 230, 118, 0.3)' : 'rgba(255, 23, 68, 0.3)'
                            }}>
                              {isLong ? '做多 (LONG)' : '做空 (SHORT)'}
                            </span>
                          </div>
                        </td>

                        <td>
                          {t.status === 'pending' && <span className="badge-status pending">⏳ 等待挂单回踩</span>}
                          {t.status === 'closed_tp' && <span className="badge-status closed_tp">🎉 止盈平仓</span>}
                          {t.status === 'closed_sl' && <span className="badge-status closed_sl">🛡️ 止损平仓</span>}
                          {t.status === 'cancelled' && <span className="badge-status" style={{ background: '#334155', color: '#94a3b8' }}>⚪ 已撤单</span>}
                        </td>

                        <td>
                          <strong style={{ color: 'var(--color-wait)' }}>{t.leverage}x</strong>
                          <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginLeft: '4px' }}>(${t.margin_usd})</span>
                        </td>

                        <td>
                          ${t.planned_entry}
                          <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>区间: ${t.entry_min} - ${t.entry_max}</div>
                        </td>

                        <td>
                          <span style={{ color: 'var(--text-muted)', fontFamily: 'monospace' }}>
                            ${t.current_price || '-'}
                          </span>
                        </td>

                        <td style={{ color: 'var(--color-short)', fontWeight: '600' }}>
                          ${t.stop_loss}
                        </td>

                        <td style={{ color: 'var(--color-long)' }}>
                          {t.take_profit_targets?.map(tp => `$${tp}`).join(', ')}
                        </td>

                        <td style={{ textAlign: 'right', fontWeight: 'bold' }}>
                          {t.pnl_usd !== 0 ? (
                            <span style={{ color: t.pnl_usd > 0 ? '#10B981' : '#EF4444' }}>
                              {t.pnl_usd > 0 ? '+' : ''}${t.pnl_usd} ({t.pnl_percent >= 0 ? '+' : ''}{t.pnl_percent}%)
                            </span>
                          ) : (
                            <span style={{ color: 'var(--text-muted)' }}>-</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          );
        })()}
      </div>

      {/* 5. Exchange & Sniper Settings Modal */}
      {showConfigModal && (
        <div className="sniper-modal-backdrop">
          <div className="sniper-modal-box">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid var(--border-color)', paddingBottom: '0.75rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <Settings size={20} style={{ color: 'var(--color-wait)' }} />
                <h3 style={{ fontSize: '1.05rem', fontWeight: '700', color: 'var(--text-bright)' }}>交易所 Key 配置与狙击参数</h3>
              </div>
              <button onClick={() => setShowConfigModal(false)} style={{ background: 'none', border: 'none', color: 'var(--text-muted)', fontSize: '1.2rem', cursor: 'pointer' }}>
                ✕
              </button>
            </div>

            {/* Sub-tabs inside modal */}
            <div style={{ display: 'flex', gap: '0.5rem', background: 'rgba(0,0,0,0.3)', padding: '4px', borderRadius: '8px' }}>
              <button
                type="button"
                className={`btn btn-secondary ${activeSubTab === 'general' ? 'active' : ''}`}
                style={{ flex: 1, padding: '0.4rem', fontSize: '0.8rem', justifyContent: 'center' }}
                onClick={() => setActiveSubTab('general')}
              >
                风控与杠杆参数
              </button>
              <button
                type="button"
                className={`btn btn-secondary ${activeSubTab === 'exchange' ? 'active' : ''}`}
                style={{ flex: 1, padding: '0.4rem', fontSize: '0.8rem', justifyContent: 'center' }}
                onClick={() => setActiveSubTab('exchange')}
              >
                实盘 API Key (交易所选型)
              </button>
            </div>

            {/* Modal Input Forms with isolated modalConfig */}
            <div style={{ display: activeSubTab === 'general' ? 'flex' : 'none', flexDirection: 'column', gap: '1rem', fontSize: '0.82rem' }}>
              <div className="form-group">
                <label className="form-label">单笔本金最高接纳风险 (%)</label>
                <input
                  type="number"
                  step="0.5"
                  value={modalConfig.risk_per_trade_percent ?? ''}
                  onChange={e => setModalConfig({ ...modalConfig, risk_per_trade_percent: e.target.value === '' ? '' : parseFloat(e.target.value) })}
                  className="form-control"
                />
                <span className="form-help">系统自动根据止损空间倒推建仓仓位，保证单笔亏损不超过此预算。</span>
              </div>

              <div className="form-group">
                <label className="form-label">杠杆管理模式</label>
                <div style={{ display: 'flex', gap: '1rem', marginTop: '0.2rem' }}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', cursor: 'pointer' }}>
                    <input
                      type="radio"
                      name="leverage_mode"
                      value="smart"
                      checked={(modalConfig.leverage_mode || 'smart') === 'smart'}
                      onChange={() => setModalConfig({ ...modalConfig, leverage_mode: 'smart' })}
                    />
                    <span>⚡ 智能调频模式</span>
                  </label>
                  <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', cursor: 'pointer' }}>
                    <input
                      type="radio"
                      name="leverage_mode"
                      value="fixed"
                      checked={modalConfig.leverage_mode === 'fixed'}
                      onChange={() => setModalConfig({ ...modalConfig, leverage_mode: 'fixed' })}
                    />
                    <span>🔒 固定杠杆模式</span>
                  </label>
                </div>
              </div>

              {(modalConfig.leverage_mode || 'smart') === 'smart' ? (
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem' }}>
                  <div className="form-group">
                    <label className="form-label">杠杆下限 (x)</label>
                    <input
                      type="number"
                      min="1"
                      max="100"
                      value={modalConfig.min_leverage ?? 35}
                      onChange={e => setModalConfig({ ...modalConfig, min_leverage: e.target.value === '' ? '' : parseInt(e.target.value) })}
                      className="form-control"
                    />
                  </div>
                  <div className="form-group">
                    <label className="form-label">杠杆上限 (x)</label>
                    <input
                      type="number"
                      min="1"
                      max="100"
                      value={modalConfig.max_leverage ?? 70}
                      onChange={e => setModalConfig({ ...modalConfig, max_leverage: e.target.value === '' ? '' : parseInt(e.target.value) })}
                      className="form-control"
                    />
                  </div>
                </div>
              ) : (
                <div className="form-group">
                  <label className="form-label">锁定固定杠杆倍数 (x)</label>
                  <input
                    type="number"
                    min="1"
                    max="100"
                    value={modalConfig.fixed_leverage ?? 50}
                    onChange={e => setModalConfig({ ...modalConfig, fixed_leverage: e.target.value === '' ? '' : parseInt(e.target.value) })}
                    className="form-control"
                  />
                  <span className="form-help">固定杠杆模式下，所有开单计划将统一锁死在此固定倍数。</span>
                </div>
              )}

              <div className="form-group">
                <label className="form-label">同时挂单/持仓最大数量</label>
                <input
                  type="number"
                  value={modalConfig.max_active_trades ?? ''}
                  onChange={e => setModalConfig({ ...modalConfig, max_active_trades: e.target.value === '' ? '' : parseInt(e.target.value) })}
                  className="form-control"
                />
              </div>

              <div className="form-group">
                <label className="form-label">触发狙击最低 AI 置信度 (1-10)</label>
                <input
                  type="number"
                  min="5"
                  max="10"
                  value={modalConfig.min_confidence ?? ''}
                  onChange={e => setModalConfig({ ...modalConfig, min_confidence: e.target.value === '' ? '' : parseInt(e.target.value) })}
                  className="form-control"
                />
              </div>
            </div>

            <div style={{ display: activeSubTab === 'exchange' ? 'flex' : 'none', flexDirection: 'column', gap: '1rem', fontSize: '0.82rem' }}>
              <div className="form-group">
                <label className="form-label">实盘对接交易所平台</label>
                <select
                  value={modalConfig.live_exchange}
                  onChange={e => setModalConfig({ ...modalConfig, live_exchange: e.target.value })}
                  className="form-control"
                >
                  <option value="binance">Binance (币安)</option>
                  <option value="okx">OKX (欧易)</option>
                  <option value="bybit">Bybit</option>
                </select>
              </div>

              <div className="form-group">
                <label className="form-label">API Key (公钥)</label>
                <input
                  type="text"
                  value={modalConfig.live_api_key}
                  onChange={e => setModalConfig({ ...modalConfig, live_api_key: e.target.value })}
                  placeholder="例如: vmPU... (由交易所 API 界面生成)"
                  className="form-control"
                />
              </div>

              <div className="form-group">
                <label className="form-label">API Secret (私钥)</label>
                <input
                  type="password"
                  value={modalConfig.live_secret}
                  onChange={e => setModalConfig({ ...modalConfig, live_secret: e.target.value })}
                  placeholder="必须包含交易/合约权限"
                  className="form-control"
                />
              </div>

              {modalConfig.live_exchange === 'okx' && (
                <div className="form-group">
                  <label className="form-label">Passphrase (OKX 专属 Passphrase 密码)</label>
                  <input
                    type="password"
                    value={modalConfig.live_passphrase}
                    onChange={e => setModalConfig({ ...modalConfig, live_passphrase: e.target.value })}
                    placeholder="创建 OKX API Key 时自设的 Password"
                    className="form-control"
                  />
                </div>
              )}

              <button
                type="button"
                onClick={handleTestExchange}
                disabled={testingExchange}
                className="btn btn-secondary"
                style={{ width: '100%', justifyContent: 'center', padding: '0.6rem', marginTop: '0.4rem' }}
              >
                <Server size={16} />
                <span>{testingExchange ? '正在校验 API 密钥及账户余额...' : '🔑 测试实盘 API 连通性'}</span>
              </button>

              {exchangeTestResult && (
                <div style={{
                  padding: '0.75rem',
                  borderRadius: '8px',
                  fontSize: '0.8rem',
                  border: '1px solid',
                  background: exchangeTestResult.status === 'success' ? 'rgba(0,230,118,0.1)' : 'rgba(255,23,68,0.1)',
                  borderColor: exchangeTestResult.status === 'success' ? 'rgba(0,230,118,0.3)' : 'rgba(255,23,68,0.3)',
                  color: exchangeTestResult.status === 'success' ? 'var(--color-long)' : 'var(--color-short)',
                  lineHeight: '1.4'
                }}>
                  {exchangeTestResult.message}
                </div>
              )}
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.75rem', paddingTop: '0.75rem', borderTop: '1px solid var(--border-color)' }}>
              <button
                type="button"
                onClick={() => setShowConfigModal(false)}
                className="btn btn-secondary"
                style={{ padding: '0.5rem 1rem', fontSize: '0.8rem' }}
              >
                取消
              </button>
              <button
                type="button"
                disabled={savingConfig}
                onClick={async () => {
                  await handleSaveConfig(modalConfig);
                  setShowConfigModal(false);
                }}
                className="btn btn-primary"
                style={{ padding: '0.5rem 1.25rem', fontSize: '0.8rem' }}
              >
                {savingConfig ? '保存中...' : '确认保存配置'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
