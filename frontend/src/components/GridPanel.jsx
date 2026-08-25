import React, { useState, useEffect, useRef } from 'react';
import { Play, Square, RefreshCw, Zap, Shield, TrendingUp, AlertTriangle, CheckCircle2, Layers, DollarSign, Activity, Key, ExternalLink, HelpCircle, RotateCcw, Flame, Target, Percent, BarChart3, ChevronRight } from 'lucide-react';
import LiquidGlass from './LiquidGlass';

export default function GridPanel({ apiBase }) {
  const [gridState, setGridState] = useState(null);
  const [exchangeInfo, setExchangeInfo] = useState(null);
  const [currentMarketPrice, setCurrentMarketPrice] = useState(null);
  const [loading, setLoading] = useState(false);
  const [recommendLoading, setRecommendLoading] = useState(false);
  const [testingExchange, setTestingExchange] = useState(false);
  
  // Screener state
  const [screenerData, setScreenerData] = useState([]);
  const [screenerLoading, setScreenerLoading] = useState(false);
  const [showScreenerModal, setShowScreenerModal] = useState(false);

  // Selected symbol
  const [selectedSymbol, setSelectedSymbol] = useState('BTC/USDT');

  // Form parameters
  const [lower, setLower] = useState('60000');
  const [upper, setUpper] = useState('70000');
  const [gridCount, setGridCount] = useState('12');
  const [sizeBase, setSizeBase] = useState('0.001');
  const [leverage, setLeverage] = useState('20');
  const [gridMode, setGridMode] = useState('long');
  const [sizingMode, setSizingMode] = useState('pyramid');
  const [outOfRangeAction, setOutOfRangeAction] = useState('auto_shift');
  const [isLive, setIsLive] = useState(false);
  const [aiRegimeNotice, setAiRegimeNotice] = useState(null);

  // Track if initial state has been loaded to avoid resetting user edits
  const initialLoadedRef = useRef(false);

  // Format precision decimal string to avoid scientific notation like 3.14e-6
  const formatPrecisionStr = (val, maxDecimals = 8) => {
    if (val === null || val === undefined || isNaN(Number(val))) return '';
    const num = Number(val);
    if (num === 0) return '0';
    return num.toLocaleString('en-US', {
      useGrouping: false,
      maximumFractionDigits: maxDecimals
    });
  };

  // Fetch screener data
  const fetchScreener = async (force = false) => {
    setScreenerLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/grid/screener?force_refresh=${force}`);
      const data = await res.json();
      if (data && data.screener) {
        setScreenerData(data.screener);
      }
    } catch (err) {
      console.error('Failed to fetch screener:', err);
    } finally {
      setScreenerLoading(false);
    }
  };

  // 1-Click apply & start coin directly from screener
  const handleApplyScreenerCoin = async (coin) => {
    setSelectedSymbol(coin.symbol);
    fetchMarketPrice(coin.symbol);
    setGridMode(coin.recommended_mode || 'long');
    
    if (isRunning) {
      if (!window.confirm(`⚠️ 当前已有正在运行的做市任务，是否停止当前任务并切换为【${coin.symbol}】一键做市？`)) {
        return;
      }
    }

    setScreenerLoading(true);
    try {
      const availBal = exchangeInfo?.balance_info?.free_usdt !== undefined
        ? Number(exchangeInfo.balance_info.free_usdt)
        : (typeof exchangeInfo?.balance_info === 'number' ? exchangeInfo.balance_info : 8.91);

      const res = await fetch(`${apiBase}/api/grid/recommend`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: coin.symbol,
          available_balance: availBal
        })
      });
      const data = await res.json();
      if (res.ok && data.recommended_lower !== undefined && data.recommended_upper !== undefined) {
        setLower(formatPrecisionStr(data.recommended_lower, 8));
        setUpper(formatPrecisionStr(data.recommended_upper, 8));
        setGridCount(String(data.recommended_grid_count || 10));
        if (data.recommended_size_base !== undefined) setSizeBase(formatPrecisionStr(data.recommended_size_base, 6));
        if (data.recommended_leverage !== undefined) setLeverage(String(data.recommended_leverage));
        if (data.mode) setGridMode(data.mode);
        if (data.recommended_action) setOutOfRangeAction(data.recommended_action);
        if (data.regime_desc) setAiRegimeNotice(data.regime_desc);
        if (data.current_price !== undefined) setCurrentMarketPrice(formatPrecisionStr(data.current_price, 8));

        const modeMap = { long: '逢低做多 (自动开立多头底仓 + 双向做市)', short: '逢高做空 (自动开立空头底仓 + 双向做市)', neutral: '中性双向 (双向做市)' };
        const modeTitle = modeMap[data.mode] || data.mode;

        const confirmMsg = `🤖 【⚡ AI 一键智能做市启动】\n\n` +
          `• 做市币种：${coin.symbol}\n` +
          `• 研判形态：${data.regime_desc || '多头上行'}\n` +
          `• 做市模式：${modeTitle}\n` +
          `• 做市区间：$${formatPrecisionStr(data.recommended_lower, 8)} ~ $${formatPrecisionStr(data.recommended_upper, 8)} (${data.recommended_grid_count} 格)\n` +
          `• 单格数量：${formatPrecisionStr(data.recommended_size_base, 6)} (${data.recommended_leverage}x 杠杆)\n` +
          `• 破位自适应：🤖 智能动态移网追踪 (Auto-Shift)\n\n` +
          `是否立即以【${isLive ? '🔴 实盘真实下单' : '🟢 模拟盘'}】启动该网格做市？`;

        if (window.confirm(confirmMsg)) {
          setShowScreenerModal(false);
          setLoading(true);

          // If currently running, stop previous grid first
          if (isRunning) {
            await fetch(`${apiBase}/api/grid/stop`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ close_position: false })
            });
          }

          const startRes = await fetch(`${apiBase}/api/grid/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              symbol: coin.symbol,
              lower: data.recommended_lower,
              upper: data.recommended_upper,
              grid_count: data.recommended_grid_count,
              size_base: data.recommended_size_base,
              leverage: data.recommended_leverage,
              mode: data.mode,
              sizing_mode: sizingMode,
              out_of_range_action: 'auto_shift',
              is_live: isLive,
              auto_open_initial_position: true
            })
          });
          const startData = await startRes.json();
          if (startRes.ok) {
            alert(`🚀 ${coin.symbol} AI 智能做市已成功启动！\n已自动建立底仓并双向挂单，开始全自动套利！`);
            fetchGridState();
          } else {
            alert(`启动失败: ${startData.detail || startData.message || '未知错误'}`);
          }
        }
      } else {
        alert(`获取推荐失败: ${data.detail || '请确认行情正常'}`);
      }
    } catch (err) {
      alert(`一键做市异常: ${err.message}`);
    } finally {
      setScreenerLoading(false);
      setLoading(false);
    }
  };

  // Fetch exchange info (with caching)
  const fetchExchangeInfo = async (force = false) => {
    try {
      const res = await fetch(`${apiBase}/api/grid/exchange-info?force_refresh=${force}`);
      const data = await res.json();
      setExchangeInfo(data);
    } catch (err) {
      console.error('Failed to fetch exchange info:', err);
    }
  };

  // Fetch current market price
  const fetchMarketPrice = async (symbol) => {
    try {
      const res = await fetch(`${apiBase}/api/market?symbol=${encodeURIComponent(symbol)}`);
      const data = await res.json();
      if (data && data.price) {
        setCurrentMarketPrice(data.price);
      }
    } catch (err) {
      console.debug('Failed to fetch market price:', err);
    }
  };

  // Fetch current grid state without overwriting user form inputs
  const fetchGridState = async () => {
    try {
      const res = await fetch(`${apiBase}/api/grid/state`);
      const data = await res.json();
      setGridState(data);

      // Only populate form from backend on very FIRST load if running
      if (!initialLoadedRef.current) {
        initialLoadedRef.current = true;
        if (data.config) {
          setSelectedSymbol(data.config.symbol || 'BTC/USDT');
          setLower(String(data.config.lower || '60000'));
          setUpper(String(data.config.upper || '70000'));
          setGridCount(String(data.config.grid_count || '12'));
          setSizeBase(String(data.config.size_base || '0.001'));
          setLeverage(String(data.config.leverage || '20'));
          setGridMode(data.config.mode || 'long');
          setSizingMode(data.config.sizing_mode || 'pyramid');
          setOutOfRangeAction(data.config.out_of_range_action || 'auto_shift');
          setIsLive(Boolean(data.config.is_live));
        }
      }
    } catch (err) {
      console.error('Failed to fetch grid state:', err);
    }
  };

  useEffect(() => {
    fetchGridState();
    fetchExchangeInfo(false);
    fetchMarketPrice(selectedSymbol);
    fetchScreener(false);

    // Fast loop (3s) for price ticks & local simulation
    const fastInterval = setInterval(() => {
      fetchGridState();
      fetchMarketPrice(selectedSymbol);
    }, 3000);

    // Ultra-conservative loop (120s) for exchange balance to strictly obey Binance rate limits
    const balanceInterval = setInterval(() => {
      fetchExchangeInfo(false);
    }, 120000);

    return () => {
      clearInterval(fastInterval);
      clearInterval(balanceInterval);
    };
  }, [selectedSymbol]);

  // One-click AI ATR & Balance Recommendation
  const handleRecommend = async (customSymbol = null) => {
    const sym = customSymbol || selectedSymbol;
    setRecommendLoading(true);
    try {
      const availBal = exchangeInfo?.balance_info?.free_usdt !== undefined
        ? Number(exchangeInfo.balance_info.free_usdt)
        : (typeof exchangeInfo?.balance_info === 'number' ? exchangeInfo.balance_info : 8.91);

      const res = await fetch(`${apiBase}/api/grid/recommend`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: sym,
          available_balance: availBal
        })
      });
      const data = await res.json();
      if (res.ok && data.recommended_lower !== undefined && data.recommended_upper !== undefined) {
        setSelectedSymbol(sym);
        setLower(formatPrecisionStr(data.recommended_lower, 8));
        setUpper(formatPrecisionStr(data.recommended_upper, 8));
        setGridCount(String(data.recommended_grid_count || 10));
        if (data.recommended_size_base !== undefined) {
          setSizeBase(formatPrecisionStr(data.recommended_size_base, 6));
        }
        if (data.recommended_leverage !== undefined) {
          setLeverage(String(data.recommended_leverage));
        }
        if (data.mode) {
          setGridMode(data.mode);
        }
        if (data.recommended_action) {
          setOutOfRangeAction(data.recommended_action);
        }
        if (data.regime_desc) {
          setAiRegimeNotice(data.regime_desc);
        }
        if (data.current_price !== undefined) {
          setCurrentMarketPrice(formatPrecisionStr(data.current_price, 8));
        }
        
        const singleNotional = (data.recommended_size_base * data.current_price).toFixed(2);
        const marginPct = ((data.estimated_margin_usd / (data.available_balance_used || 8.91)) * 100).toFixed(0);
        const modeMap = { long: '逢低做多 (Long Only)', short: '逢高做空 (Short Only)', neutral: '中性双向 (Neutral)' };

        alert(`🤖 AI 全自动智能推荐完成！\n\n【做市币种】：${sym}\n【研判结论】：${data.regime_desc || '已匹配'}\n• 自动匹配模式：${modeMap[data.mode] || data.mode}\n• 自动安全区间：$${formatPrecisionStr(data.recommended_lower, 8)} ~ $${formatPrecisionStr(data.recommended_upper, 8)} (${data.recommended_grid_count} 格)\n• 智能单格数量：${formatPrecisionStr(data.recommended_size_base, 6)} (单笔约 $${singleNotional} USDT)\n• 自动匹配杠杆：${data.recommended_leverage}x (占用保证金 $${data.estimated_margin_usd} USD，仅占可用资金 ${marginPct}%)\n• 破位自适应动作：🤖 智能动态移网追踪 (Auto-Shift)`);
      } else {
        alert(`获取推荐参数失败: ${data.detail || '请确认该币种行情正常'}`);
      }
    } catch (err) {
      alert(`推荐失败: ${err.message}`);
    } finally {
      setRecommendLoading(false);
    }
  };

  // Test Exchange Connectivity
  const handleTestExchange = async () => {
    setTestingExchange(true);
    try {
      await fetchExchangeInfo(true);
      alert('已向交易所发起实盘连通性与可用余额同步！');
    } catch (err) {
      alert(`测试失败: ${err.message}`);
    } finally {
      setTestingExchange(false);
    }
  };

  // Start Grid
  const handleStartGrid = async () => {
    const l = parseFloat(lower);
    const u = parseFloat(upper);
    const c = parseInt(gridCount);
    const s = parseFloat(sizeBase);
    const lev = parseInt(leverage);

    if (isNaN(l) || isNaN(u) || l <= 0 || u <= 0 || l >= u) {
      alert('请输入有效的区间上下界，且上边界必须大于下边界！');
      return;
    }
    if (isNaN(c) || c < 2 || c > 100) {
      alert('网格格数必须在 2 ~ 100 之间！');
      return;
    }
    if (isNaN(s) || s <= 0) {
      alert('请输入有效的单格数量！');
      return;
    }

    if (isLive && (!exchangeInfo || !exchangeInfo.is_configured)) {
      alert('⚠️ 你开启了实盘模式，但尚未在「核心配置参数」中配置实盘 API Key / Secret！请先前往设置配置。');
      return;
    }

    if (isLive) {
      if (!window.confirm(`⚠️ 【实盘真实下单确认】\n你即将使用真实交易所账户 (${exchangeInfo?.exchange_id?.toUpperCase()}) 启动 ${selectedSymbol} 实盘网格！\n\n• 区间：$${l} ~ $${u}\n• 格数：${c} 格\n• 单格数量：${s} (${lev}x 杠杆)\n\n确定要立即向交易所提交 Maker 挂单吗？`)) {
        return;
      }
    }

    setLoading(true);
    try {
      const payload = {
        symbol: selectedSymbol,
        lower: l,
        upper: u,
        grid_count: c,
        size_base: s,
        leverage: lev,
        mode: gridMode,
        sizing_mode: sizingMode,
        out_of_range_action: outOfRangeAction,
        is_live: isLive,
        auto_open_initial_position: true
      };
      const res = await fetch(`${apiBase}/api/grid/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (res.ok) {
        alert(`🚀 机构级网格量化做市机器人启动成功！`);
        fetchGridState();
      } else {
        alert(`启动失败: ${data.detail || data.message || '未知错误'}`);
      }
    } catch (err) {
      alert(`请求错误: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  // Stop Grid
  const handleStopGrid = async (closePos = false) => {
    if (!window.confirm(`确定要停止网格机器人吗？${closePos ? '（将同时市价平仓当前仓位）' : '（将仅撤销挂单，保留已有持仓）'}`)) return;
    setLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/grid/stop`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ close_position: closePos })
      });
      const data = await res.json();
      alert(`🛑 网格已成功停止！`);
      fetchGridState();
    } catch (err) {
      alert(`停止失败: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  // Reset Stats
  const handleResetStats = async () => {
    if (!window.confirm('确定要清空累计网格利润、成交额与历史成交记录吗？（不影响正在运行的实盘/挂单）')) return;
    setLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/grid/reset-stats`, { method: 'POST' });
      if (res.ok) {
        alert('🧹 统计数据与历史成交记录已成功清零！');
        fetchGridState();
      } else {
        alert('重置失败');
      }
    } catch (err) {
      alert(`重置错误: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const isRunning = gridState?.running || false;
  const isRecovery = gridState?.recovery || false;
  const stats = gridState?.stats || {};
  const risk = gridState?.risk || {};
  const activeOrders = gridState?.active_orders || [];
  const fills = gridState?.recent_fills || [];

  // Calculate live preview metrics
  const previewLower = parseFloat(lower) || 0;
  const previewUpper = parseFloat(upper) || 0;
  const previewCount = parseInt(gridCount) || 12;
  const previewSpacing = (previewUpper > previewLower && previewCount > 0) ? ((previewUpper - previewLower) / previewCount) : 0;
  const previewMid = (previewUpper + previewLower) / 2 || 1;
  const previewSpacingPct = previewMid > 0 ? ((previewSpacing / previewMid) * 100).toFixed(2) : '0.00';
  const previewAvgMult = sizingMode === 'pyramid' ? 1.15 : 1.0;
  const previewNotional = (previewCount * (parseFloat(sizeBase) || 0) * previewAvgMult * previewMid).toFixed(2);
  const previewMargin = (previewNotional / (parseInt(leverage) || 20)).toFixed(2);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.2rem', paddingBottom: '2rem' }}>
      
      {/* Header Banner */}
      <LiquidGlass
        cornerRadius={20}
        padding="1.25rem 1.5rem"
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: '1rem',
          border: '1px solid rgba(0, 122, 255, 0.2)',
          background: 'linear-gradient(135deg, rgba(0, 122, 255, 0.05) 0%, rgba(52, 199, 89, 0.03) 100%)'
        }}
      >
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '0.25rem' }}>
            <Layers size={22} style={{ color: 'var(--color-long)' }} />
            <h2 style={{ fontSize: '1.25rem', fontWeight: '700', margin: 0 }}>
              💎 机构级 AI 网格量化做市系统 (Institutional AI Grid MM)
            </h2>
            <span style={{
              fontSize: '0.75rem',
              padding: '2px 8px',
              borderRadius: '12px',
              background: isRecovery ? 'rgba(255, 149, 0, 0.15)' : isRunning ? 'rgba(52, 199, 89, 0.15)' : 'rgba(142, 142, 147, 0.15)',
              color: isRecovery ? '#ff9500' : isRunning ? '#34c759' : '#8e8e93',
              fontWeight: '700',
              border: `1px solid ${isRecovery ? 'rgba(255, 149, 0, 0.3)' : isRunning ? 'rgba(52, 199, 89, 0.3)' : 'rgba(142, 142, 147, 0.3)'}`
            }}>
              {isRecovery ? '🛡️ 只减仓回收中' : isRunning ? '● 正在做市' : '○ 待机中'}
            </span>
          </div>
          <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', margin: 0 }}>
            搭载黄金金字塔非对称加权、ATR 波动率自适应与全天候对账引擎（100% Maker Post-Only 稳健套利）
          </p>
        </div>

        {/* Screener Button */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
          <button
            onClick={() => {
              setShowScreenerModal(!showScreenerModal);
              if (!showScreenerModal) fetchScreener(true);
            }}
            style={{
              background: showScreenerModal ? 'rgba(255, 149, 0, 0.15)' : 'rgba(255, 149, 0, 0.08)',
              color: '#f59e0b',
              border: '1px solid rgba(255, 149, 0, 0.3)',
              borderRadius: '10px',
              padding: '8px 14px',
              fontSize: '0.82rem',
              fontWeight: '700',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              boxShadow: '0 2px 8px rgba(245, 158, 11, 0.15)',
              transition: 'all 0.2s ease'
            }}
          >
            <Flame size={16} />
            🔥 全市场吸金雷达 (Screener)
          </button>
        </div>
      </LiquidGlass>

      {/* Grid Alpha Screener Collapsible Panel */}
      {showScreenerModal && (
        <LiquidGlass
          cornerRadius={18}
          padding="1.3rem"
          style={{
            border: '1px solid rgba(245, 158, 11, 0.3)',
            background: 'linear-gradient(135deg, rgba(245, 158, 11, 0.04) 0%, rgba(0, 113, 227, 0.03) 100%)',
            display: 'flex',
            flexDirection: 'column',
            gap: '0.8rem'
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid rgba(0,0,0,0.06)', paddingBottom: '0.6rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <Flame size={18} style={{ color: '#f59e0b' }} />
              <h3 style={{ fontSize: '1rem', fontWeight: '800', margin: 0 }}>
                🔥 全市场网格套利吸金雷达 (Grid Alpha Screener)
              </h3>
              <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                根据 4H ATR 真实波动率、多空动能与盘口流动性实时智能评分
              </span>
            </div>
            <button
              onClick={() => fetchScreener(true)}
              disabled={screenerLoading}
              style={{
                background: 'rgba(255, 255, 255, 0.08)',
                border: '1px solid var(--border-color)',
                borderRadius: '8px',
                padding: '4px 10px',
                fontSize: '0.75rem',
                cursor: 'pointer',
                fontWeight: '600',
                display: 'flex',
                alignItems: 'center',
                gap: '4px'
              }}
            >
              <RefreshCw size={13} className={screenerLoading ? 'spin' : ''} />
              {screenerLoading ? '扫描全网中...' : '刷新雷达榜单'}
            </button>
          </div>

          {screenerLoading && screenerData.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
              ⚡ 正在扫描币安合约全市场行情与波动率，请稍候...
            </div>
          ) : (
            <div style={{ maxHeight: '280px', overflowY: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid rgba(0,0,0,0.08)', color: 'var(--text-muted)' }}>
                    <th style={{ textAlign: 'left', padding: '8px' }}>交易对</th>
                    <th style={{ textAlign: 'center', padding: '8px' }}>网格吸金指数</th>
                    <th style={{ textAlign: 'right', padding: '8px' }}>最新市价</th>
                    <th style={{ textAlign: 'center', padding: '8px' }}>24H 波动率 (ATR)</th>
                    <th style={{ textAlign: 'center', padding: '8px' }}>4H 趋势形态</th>
                    <th style={{ textAlign: 'center', padding: '8px' }}>推荐模式</th>
                    <th style={{ textAlign: 'center', padding: '8px' }}>预估日化收益</th>
                    <th style={{ textAlign: 'center', padding: '8px' }}>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {screenerData.map((coin, idx) => (
                    <tr key={idx} style={{ borderBottom: '1px solid rgba(0,0,0,0.03)', transition: 'background 0.2s' }}>
                      <td style={{ padding: '8px', fontWeight: '800' }}>
                        <span style={{ color: idx < 3 ? '#f59e0b' : 'inherit' }}>
                          {idx === 0 ? '🥇 ' : (idx === 1 ? '🥈 ' : (idx === 2 ? '🥉 ' : ''))}
                          {coin.symbol}
                        </span>
                      </td>
                      <td style={{ padding: '8px', textAlign: 'center' }}>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px' }}>
                          <div style={{ width: '50px', height: '6px', background: 'rgba(0,0,0,0.1)', borderRadius: '3px', overflow: 'hidden' }}>
                            <div style={{ width: `${coin.alpha_score}%`, height: '100%', background: coin.alpha_score >= 80 ? '#34c759' : (coin.alpha_score >= 65 ? '#0071e3' : '#f59e0b') }} />
                          </div>
                          <strong style={{ color: coin.alpha_score >= 80 ? '#34c759' : '#0071e3' }}>{coin.alpha_score}</strong>
                        </div>
                      </td>
                      <td style={{ padding: '8px', textAlign: 'right', fontWeight: '600' }}>${coin.current_price}</td>
                      <td style={{ padding: '8px', textAlign: 'center' }}>
                        <span style={{ fontSize: '0.75rem', fontWeight: '700', color: coin.atr_pct > 2.5 ? '#ff3b30' : '#0071e3' }}>
                          {coin.atr_pct}% ({coin.volatility_level})
                        </span>
                      </td>
                      <td style={{ padding: '8px', textAlign: 'center' }}>
                        <span style={{
                          fontSize: '0.72rem',
                          padding: '2px 7px',
                          borderRadius: '6px',
                          fontWeight: '700',
                          background: coin.regime === 'trending_up' ? 'rgba(52, 199, 89, 0.1)' : (coin.regime === 'trending_down' ? 'rgba(255, 59, 48, 0.1)' : 'rgba(0, 113, 227, 0.1)'),
                          color: coin.regime === 'trending_up' ? '#34c759' : (coin.regime === 'trending_down' ? '#ff3b30' : '#0071e3')
                        }}>
                          {coin.regime_label}
                        </span>
                      </td>
                      <td style={{ padding: '8px', textAlign: 'center', fontWeight: '700' }}>
                        {coin.recommended_mode === 'long' ? '🟢 做多' : (coin.recommended_mode === 'short' ? '🔴 做空' : '⚖️ 双向')}
                      </td>
                      <td style={{ padding: '8px', textAlign: 'center', color: '#34c759', fontWeight: '700' }}>
                        {coin.est_daily_yield}
                      </td>
                      <td style={{ padding: '8px', textAlign: 'center' }}>
                        <button
                          onClick={() => handleApplyScreenerCoin(coin)}
                          style={{
                            background: isRunning ? 'linear-gradient(135deg, #f59e0b 0%, #ff9500 100%)' : 'linear-gradient(135deg, #0071e3 0%, #34c759 100%)',
                            color: '#fff',
                            border: 'none',
                            borderRadius: '6px',
                            padding: '3px 10px',
                            fontSize: '0.75rem',
                            fontWeight: '700',
                            cursor: 'pointer',
                            boxShadow: '0 2px 6px rgba(0, 113, 227, 0.25)'
                          }}
                        >
                          {isRunning ? '⚡ 切换以此币做市' : '⚡ 一键以此币做市'}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </LiquidGlass>
      )}

      {/* Exchange Connection Status Banner */}
      <LiquidGlass
        cornerRadius={16}
        padding="0.85rem 1.25rem"
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: '0.8rem',
          background: exchangeInfo?.is_configured ? 'rgba(52, 199, 89, 0.05)' : 'rgba(255, 149, 0, 0.05)',
          border: `1px solid ${exchangeInfo?.is_configured ? 'rgba(52, 199, 89, 0.2)' : 'rgba(255, 149, 0, 0.2)'}`
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
          <Key size={18} style={{ color: exchangeInfo?.is_configured ? '#34c759' : '#ff9500' }} />
          <div>
            <div style={{ fontSize: '0.9rem', fontWeight: '700', display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span>实盘绑定交易所：<strong style={{ color: 'var(--color-long)' }}>{(exchangeInfo?.exchange_id || 'BINANCE').toUpperCase()} 永续合约</strong></span>
              {exchangeInfo?.is_configured ? (
                <span style={{ color: '#34c759', fontSize: '0.75rem', fontWeight: '600', padding: '2px 6px', background: 'rgba(52, 199, 89, 0.1)', borderRadius: '6px' }}>
                  ✅ API 凭证已配置
                </span>
              ) : (
                <span style={{ color: '#ff9500', fontSize: '0.75rem', fontWeight: '600', padding: '2px 6px', background: 'rgba(255, 149, 0, 0.1)', borderRadius: '6px' }}>
                  ⚠️ 未配置实盘 Key
                </span>
              )}
            </div>

            <div style={{ fontSize: '0.82rem', marginTop: '4px', color: 'var(--text-main)' }}>
              实盘账户可用余额：
              <strong style={{ color: '#34c759', fontSize: '1.05rem', marginLeft: '4px' }}>
                ${exchangeInfo?.balance_info?.free_usdt !== undefined ? exchangeInfo.balance_info.free_usdt : (typeof exchangeInfo?.balance_info === 'number' ? exchangeInfo.balance_info : '0.00')} USDT
              </strong>
              {exchangeInfo?.balance_info?.total_usdt !== undefined && (
                <span style={{ color: 'var(--text-muted)', marginLeft: '8px', fontSize: '0.75rem' }}>
                  (账户总资产: ${exchangeInfo.balance_info.total_usdt} USDT)
                </span>
              )}
              {exchangeInfo?.balance_info?.error && (
                <span style={{ color: '#ff9500', marginLeft: '8px', fontSize: '0.75rem' }}>
                  ({exchangeInfo.balance_info.error})
                </span>
              )}
            </div>
          </div>
        </div>

        <button
          onClick={handleTestExchange}
          disabled={testingExchange}
          style={{
            background: 'rgba(0, 122, 255, 0.08)',
            color: '#0071e3',
            border: '1px solid rgba(0, 122, 255, 0.2)',
            borderRadius: '8px',
            padding: '5px 12px',
            fontSize: '0.8rem',
            cursor: 'pointer',
            fontWeight: '600',
            display: 'flex',
            alignItems: 'center',
            gap: '6px'
          }}
        >
          <RefreshCw size={14} className={testingExchange ? 'spin' : ''} />
          {testingExchange ? '正在同步...' : '⚡ 刷新实盘连通性与余额'}
        </button>
      </LiquidGlass>

      {/* Metrics Row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '1rem' }}>
        
        {/* Metric 1 */}
        <LiquidGlass cornerRadius={16} padding="1.1rem">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.3rem' }}>
            <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
              <DollarSign size={15} style={{ color: '#34c759' }} /> 累计已实现网格利润
            </div>
            <button
              onClick={handleResetStats}
              title="清空累计利润与统计数据"
              style={{
                background: 'rgba(255, 255, 255, 0.06)',
                border: '1px solid rgba(255, 255, 255, 0.15)',
                color: 'var(--text-muted)',
                borderRadius: '6px',
                padding: '2px 7px',
                fontSize: '0.7rem',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '3px',
                transition: 'all 0.2s ease'
              }}
              onMouseEnter={e => {
                e.currentTarget.style.color = '#ff9500';
                e.currentTarget.style.borderColor = 'rgba(255, 149, 0, 0.4)';
              }}
              onMouseLeave={e => {
                e.currentTarget.style.color = 'var(--text-muted)';
                e.currentTarget.style.borderColor = 'rgba(255, 255, 255, 0.15)';
              }}
            >
              <RotateCcw size={11} /> 清空统计
            </button>
          </div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px' }}>
            <span style={{ fontSize: '1.4rem', fontWeight: '800', color: (stats.grid_profit_usd || 0) >= 0 ? '#34c759' : '#ff3b30' }}>
              +${(stats.grid_profit_usd || 0).toFixed(2)} USD
            </span>
            <span style={{ fontSize: '0.75rem', fontWeight: '700', color: '#0071e3', background: 'rgba(0, 113, 227, 0.1)', padding: '2px 6px', borderRadius: '6px' }}>
              {gridState?.apy_est_pct ? `+${gridState.apy_est_pct}% 年化` : '38%~92% 预估APY'}
            </span>
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
            已完成配对套利：<strong>{stats.completed_rungs || 0} 轮</strong>
          </div>
        </LiquidGlass>

        {/* Metric 2 */}
        <LiquidGlass cornerRadius={16} padding="1.1rem">
          <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: '0.3rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
            <Activity size={15} style={{ color: '#0071e3' }} /> 网格成交总额 & 流速
          </div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px' }}>
            <span style={{ fontSize: '1.4rem', fontWeight: '800', color: 'var(--text-main)' }}>
              ${(stats.volume_usd || 0).toLocaleString()} USD
            </span>
            {gridState?.profit_velocity_usd_hr > 0 && (
              <span style={{ fontSize: '0.72rem', fontWeight: '700', color: '#34c759' }}>
                (${gridState.profit_velocity_usd_hr}/h)
              </span>
            )}
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
            买入 {stats.buys || 0} 笔 / 卖出 {stats.sells || 0} 笔
          </div>
        </LiquidGlass>

        {/* Metric 3 */}
        <LiquidGlass cornerRadius={16} padding="1.1rem">
          <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: '0.3rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
            <Layers size={15} style={{ color: '#ff9500' }} /> 单格参数与仓位模型
          </div>
          <div style={{ fontSize: '1.4rem', fontWeight: '800', color: 'var(--text-main)' }}>
            {isRunning ? (risk.spacing_pct ? `${risk.spacing_pct}% / 格` : '--') : `${previewSpacingPct}% / 格`}
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
            模型：<strong>{(gridState?.risk?.sizing_mode || sizingMode) === 'pyramid' ? '📐 黄金金字塔加权' : '⚖️ 经典等额分布'}</strong>
          </div>
        </LiquidGlass>

        {/* Metric 4 */}
        <LiquidGlass cornerRadius={16} padding="1.1rem">
          <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: '0.3rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
            <Shield size={15} style={{ color: '#a855f7' }} /> 所需保证金与安全垫
          </div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px' }}>
            <span style={{ fontSize: '1.4rem', fontWeight: '800', color: 'var(--text-main)' }}>
              ${isRunning ? (risk.required_margin_usd || '0.00') : previewMargin} USD
            </span>
            <span style={{ fontSize: '0.72rem', fontWeight: '700', color: '#34c759', background: 'rgba(52, 199, 89, 0.12)', padding: '2px 6px', borderRadius: '6px' }}>
              🛡️ {gridState?.safety_score || 95}/100 安全
            </span>
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
            总名义价值：${isRunning ? (risk.notional_usd || '0.00') : previewNotional} ({leverage}x 杠杆)
          </div>
        </LiquidGlass>
      </div>

      {/* Grid Configuration Form & Active Orders */}
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(320px, 390px) 1fr', gap: '1.2rem' }}>
        
        {/* Left: Configuration Form */}
        <LiquidGlass cornerRadius={18} padding="1.3rem" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid rgba(0,0,0,0.06)', paddingBottom: '0.6rem' }}>
            <div>
              <h3 style={{ fontSize: '1rem', fontWeight: '700', margin: 0 }}>⚙️ 网格策略参数配置</h3>
              {currentMarketPrice && (
                <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '2px' }}>
                  当前最新市价：<strong style={{ color: 'var(--color-long)' }}>${currentMarketPrice}</strong>
                </div>
              )}
            </div>
            <button
              onClick={handleRecommend}
              disabled={recommendLoading || isRunning}
              style={{
                background: 'rgba(0, 122, 255, 0.08)',
                color: '#0071e3',
                border: '1px solid rgba(0, 122, 255, 0.2)',
                borderRadius: '8px',
                padding: '4px 10px',
                fontSize: '0.75rem',
                cursor: isRunning ? 'not-allowed' : 'pointer',
                fontWeight: '600',
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
                opacity: isRunning ? 0.5 : 1
              }}
            >
              <Zap size={13} /> {recommendLoading ? '计算中...' : '✨ 智能推荐区间'}
            </button>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.8rem' }}>
            
            {/* AI Auto-Pilot Regime Banner */}
            {aiRegimeNotice && (
              <div style={{
                background: 'linear-gradient(135deg, rgba(0, 113, 227, 0.12), rgba(52, 199, 89, 0.12))',
                border: '1px solid rgba(0, 113, 227, 0.25)',
                borderRadius: '10px',
                padding: '8px 12px',
                fontSize: '0.8rem',
                color: 'var(--text-main)',
                display: 'flex',
                alignItems: 'center',
                gap: '8px'
              }}>
                <span style={{ fontSize: '1.1rem' }}>🤖</span>
                <div style={{ lineHeight: '1.35' }}>
                  <div style={{ fontWeight: '700', color: '#0071e3' }}>AI 智能研判与模式已锁定</div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{aiRegimeNotice}</div>
                </div>
              </div>
            )}

            {/* Symbol */}
            <div>
              <label style={{ fontSize: '0.8rem', fontWeight: '600', color: 'var(--text-muted)', marginBottom: '0.25rem', display: 'block' }}>
                做市交易对 (Symbol)
              </label>
              <select
                value={selectedSymbol}
                onChange={e => {
                  setSelectedSymbol(e.target.value);
                  fetchMarketPrice(e.target.value);
                }}
                disabled={isRunning}
                className="input-select"
                style={{ width: '100%', padding: '0.5rem', borderRadius: '8px', border: '1px solid var(--border-color)', background: 'var(--card-bg)' }}
              >
                <option value="1000PEPE/USDT">1000PEPE/USDT</option>
                <option value="XRP/USDT">XRP/USDT</option>
                <option value="DOGE/USDT">DOGE/USDT</option>
                <option value="SUI/USDT">SUI/USDT</option>
                <option value="NEAR/USDT">NEAR/USDT</option>
                <option value="ADA/USDT">ADA/USDT</option>
                <option value="AVAX/USDT">AVAX/USDT</option>
                <option value="LINK/USDT">LINK/USDT</option>
                <option value="BNB/USDT">BNB/USDT</option>
                <option value="SOL/USDT">SOL/USDT</option>
                <option value="ETH/USDT">ETH/USDT</option>
                <option value="BTC/USDT">BTC/USDT</option>
                <option value="ZEC/USDT">ZEC/USDT</option>
                {!["1000PEPE/USDT", "XRP/USDT", "DOGE/USDT", "SUI/USDT", "NEAR/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT", "BNB/USDT", "SOL/USDT", "ETH/USDT", "BTC/USDT", "ZEC/USDT"].includes(selectedSymbol) && (
                  <option value={selectedSymbol}>{selectedSymbol}</option>
                )}
              </select>
            </div>

            {/* Upper & Lower */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.6rem' }}>
              <div>
                <label style={{ fontSize: '0.8rem', fontWeight: '600', color: 'var(--text-muted)', marginBottom: '0.25rem', display: 'block' }}>
                  区间下界 ($)
                </label>
                <input
                  type="number"
                  step="any"
                  value={lower}
                  onChange={e => setLower(e.target.value)}
                  disabled={isRunning}
                  style={{ width: '100%', padding: '0.5rem', borderRadius: '8px', border: '1px solid var(--border-color)', background: 'var(--card-bg)' }}
                />
              </div>
              <div>
                <label style={{ fontSize: '0.8rem', fontWeight: '600', color: 'var(--text-muted)', marginBottom: '0.25rem', display: 'block' }}>
                  区间上界 ($)
                </label>
                <input
                  type="number"
                  step="any"
                  value={upper}
                  onChange={e => setUpper(e.target.value)}
                  disabled={isRunning}
                  style={{ width: '100%', padding: '0.5rem', borderRadius: '8px', border: '1px solid var(--border-color)', background: 'var(--card-bg)' }}
                />
              </div>
            </div>

            {/* Spacing Info Alert */}
            <div style={{
              fontSize: '0.75rem',
              padding: '6px 10px',
              borderRadius: '8px',
              background: parseFloat(previewSpacingPct) >= 0.2 ? 'rgba(52, 199, 89, 0.08)' : 'rgba(255, 149, 0, 0.08)',
              color: parseFloat(previewSpacingPct) >= 0.2 ? '#248a3d' : '#d97706',
              border: `1px solid ${parseFloat(previewSpacingPct) >= 0.2 ? 'rgba(52, 199, 89, 0.2)' : 'rgba(255, 149, 0, 0.2)'}`,
              display: 'flex',
              justifyContent: 'space-between'
            }}>
              <span>等差单格间距：<strong>${previewSpacing.toFixed(2)}</strong></span>
              <span>单格幅度：<strong>{previewSpacingPct}%</strong></span>
            </div>

            {/* Sizing Distribution Mode */}
            <div>
              <label style={{ fontSize: '0.8rem', fontWeight: '600', color: 'var(--text-muted)', marginBottom: '0.25rem', display: 'flex', justifyContent: 'space-between' }}>
                <span>💎 仓位分布加权模型</span>
                <span style={{ fontSize: '0.72rem', color: '#a855f7', fontWeight: '700' }}>机构金字塔加权</span>
              </label>
              <select
                value={sizingMode}
                onChange={e => setSizingMode(e.target.value)}
                disabled={isRunning}
                className="input-select"
                style={{ width: '100%', padding: '0.5rem', borderRadius: '8px', border: '1px solid var(--border-color)', background: 'var(--card-bg)' }}
              >
                <option value="pyramid">📐 黄金金字塔加权 (Pyramid) [深层重仓抄底拉均价·机构首选]</option>
                <option value="uniform">⚖️ 经典等额模式 (Uniform) [各格位资金严格等分]</option>
              </select>
            </div>

            {/* Grid Count & Base Size */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.6rem' }}>
              <div>
                <label style={{ fontSize: '0.8rem', fontWeight: '600', color: 'var(--text-muted)', marginBottom: '0.25rem', display: 'block' }}>
                  网格格数 (Count)
                </label>
                <input
                  type="number"
                  min="2"
                  max="100"
                  value={gridCount}
                  onChange={e => setGridCount(e.target.value)}
                  disabled={isRunning}
                  style={{ width: '100%', padding: '0.5rem', borderRadius: '8px', border: '1px solid var(--border-color)', background: 'var(--card-bg)' }}
                />
              </div>
              <div>
                <label style={{ fontSize: '0.8rem', fontWeight: '600', color: 'var(--text-muted)', marginBottom: '0.25rem', display: 'block' }}>
                  基础数量 (Base Qty)
                </label>
                <input
                  type="number"
                  step="any"
                  value={sizeBase}
                  onChange={e => setSizeBase(e.target.value)}
                  disabled={isRunning}
                  style={{ width: '100%', padding: '0.5rem', borderRadius: '8px', border: '1px solid var(--border-color)', background: 'var(--card-bg)' }}
                />
              </div>
            </div>

            {/* Leverage & Mode */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.6rem' }}>
              <div>
                <label style={{ fontSize: '0.8rem', fontWeight: '600', color: 'var(--text-muted)', marginBottom: '0.25rem', display: 'block' }}>
                  合约杠杆 (x)
                </label>
                <input
                  type="number"
                  min="1"
                  max="50"
                  value={leverage}
                  onChange={e => setLeverage(e.target.value)}
                  disabled={isRunning}
                  style={{ width: '100%', padding: '0.5rem', borderRadius: '8px', border: '1px solid var(--border-color)', background: 'var(--card-bg)' }}
                />
              </div>
              <div>
                <label style={{ fontSize: '0.8rem', fontWeight: '600', color: 'var(--text-muted)', marginBottom: '0.25rem', display: 'block' }}>
                  网格模式
                </label>
                <select
                  value={gridMode}
                  onChange={e => setGridMode(e.target.value)}
                  disabled={isRunning}
                  style={{ width: '100%', padding: '0.5rem', borderRadius: '8px', border: '1px solid var(--border-color)', background: 'var(--card-bg)' }}
                >
                  <option value="long">🟢 逢低做多 (Long Only)</option>
                  <option value="neutral">⚖️ 中性双向 (Neutral)</option>
                  <option value="short">🔴 逢高做空 (Short Only)</option>
                </select>
              </div>
            </div>

            {/* Out of Range Action */}
            <div>
              <label style={{ fontSize: '0.8rem', fontWeight: '600', color: 'var(--text-muted)', marginBottom: '0.25rem', display: 'block' }}>
                破位出区间保护动作
              </label>
              <select
                value={outOfRangeAction}
                onChange={e => setOutOfRangeAction(e.target.value)}
                disabled={isRunning}
                style={{ width: '100%', padding: '0.5rem', borderRadius: '8px', border: '1px solid var(--border-color)', background: 'var(--card-bg)' }}
              >
                <option value="auto_shift">🤖 智能动态移网追踪 (Auto-Shift) [全托管首选]</option>
                <option value="close">🛑 破位市价止损平仓 (Stop & Close)</option>
                <option value="recover">🛡️ 只减仓回收阶梯 (Recover)</option>
              </select>
            </div>

            {/* Live / Paper Radio Box */}
            <div style={{
              padding: '10px 12px',
              borderRadius: '10px',
              background: isLive ? 'rgba(255, 59, 48, 0.08)' : 'rgba(52, 199, 89, 0.08)',
              border: `1px solid ${isLive ? 'rgba(255, 59, 48, 0.2)' : 'rgba(52, 199, 89, 0.2)'}`,
              marginTop: '0.3rem'
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <input
                  type="checkbox"
                  id="isLiveGridToggle"
                  checked={isLive}
                  onChange={e => setIsLive(e.target.checked)}
                  disabled={isRunning}
                  style={{ width: '16px', height: '16px', cursor: isRunning ? 'not-allowed' : 'pointer' }}
                />
                <label htmlFor="isLiveGridToggle" style={{ fontSize: '0.85rem', fontWeight: '700', color: isLive ? '#d70015' : '#248a3d', cursor: isRunning ? 'not-allowed' : 'pointer' }}>
                  {isLive ? '🔴 实盘真实下单模式 (Live Mode)' : '🟢 模拟盘测算模式 (Paper Mode)'}
                </label>
              </div>
              <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', margin: '4px 0 0 24px' }}>
                {isLive ? '启动后将真实在交易所提交 Post-Only Maker 挂单与对账' : '启动后在本地模拟撮合价差并统计套利利润'}
              </p>
            </div>

            {/* Start / Stop Action Buttons inside Config Form */}
            <div style={{ marginTop: '0.4rem' }}>
              {isRunning ? (
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.6rem' }}>
                  <button
                    onClick={() => handleStopGrid(false)}
                    disabled={loading}
                    className="btn"
                    style={{
                      background: 'rgba(255, 149, 0, 0.12)',
                      color: '#ff9500',
                      border: '1px solid rgba(255, 149, 0, 0.3)',
                      fontWeight: '600',
                      padding: '0.75rem 0.5rem',
                      fontSize: '0.85rem',
                      borderRadius: '10px',
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: '4px'
                    }}
                  >
                    <Square size={15} /> 仅撤单停止
                  </button>
                  <button
                    onClick={() => handleStopGrid(true)}
                    disabled={loading}
                    className="btn"
                    style={{
                      background: 'rgba(255, 59, 48, 0.12)',
                      color: '#ff3b30',
                      border: '1px solid rgba(255, 59, 48, 0.3)',
                      fontWeight: '600',
                      padding: '0.75rem 0.5rem',
                      fontSize: '0.85rem',
                      borderRadius: '10px',
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: '4px'
                    }}
                  >
                    <Square size={15} /> 撤单并市价平仓
                  </button>
                </div>
              ) : (
                <button
                  onClick={handleStartGrid}
                  disabled={loading}
                  className="btn btn-primary"
                  style={{
                    width: '100%',
                    padding: '0.8rem 1rem',
                    background: 'linear-gradient(135deg, #0071e3 0%, #34c759 100%)',
                    fontWeight: '700',
                    fontSize: '0.95rem',
                    borderRadius: '10px',
                    boxShadow: '0 4px 14px rgba(0, 113, 227, 0.3)',
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: '8px'
                  }}
                >
                  <Play size={17} /> 🚀 启动网格做市
                </button>
              )}
            </div>

          </div>
        </LiquidGlass>

        {/* Right: Active Maker Orders & Recent Fills */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          
          {/* Active Maker Orders Table */}
          <LiquidGlass cornerRadius={18} padding="1.2rem">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.8rem' }}>
              <h3 style={{ fontSize: '0.95rem', fontWeight: '700', margin: 0, display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                <Layers size={16} style={{ color: '#0071e3' }} /> 当前盘口活动挂单 ({activeOrders.length} 笔)
              </h3>
              <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                100% Maker Post-Only
              </span>
            </div>

            {activeOrders.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '2rem 1rem', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
                暂无活动挂单，启动网格后将自动在此铺设等差买卖单。
              </div>
            ) : (
              <div style={{ maxHeight: '240px', overflowY: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid rgba(0,0,0,0.06)', color: 'var(--text-muted)' }}>
                      <th style={{ textAlign: 'left', padding: '6px' }}>格位</th>
                      <th style={{ textAlign: 'left', padding: '6px' }}>方向</th>
                      <th style={{ textAlign: 'right', padding: '6px' }}>挂单价格</th>
                      <th style={{ textAlign: 'right', padding: '6px' }}>数量</th>
                      <th style={{ textAlign: 'center', padding: '6px' }}>属性</th>
                      <th style={{ textAlign: 'center', padding: '6px' }}>交易所状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {activeOrders.map((ord, idx) => (
                      <tr key={idx} style={{ borderBottom: '1px solid rgba(0,0,0,0.03)' }}>
                        <td style={{ padding: '6px', fontWeight: '600' }}>#{ord.level_index ?? idx}</td>
                        <td style={{ padding: '6px', color: ord.side === 'buy' ? '#34c759' : '#ff3b30', fontWeight: '700' }}>
                          {ord.side?.toUpperCase()}
                        </td>
                        <td style={{ padding: '6px', textAlign: 'right', fontWeight: '600' }}>${ord.price}</td>
                        <td style={{ padding: '6px', textAlign: 'right' }}>
                          <span style={{ fontWeight: '600' }}>{ord.size_base}</span>
                          {ord.size_multiplier && ord.size_multiplier !== 1.0 && (
                            <span style={{ fontSize: '0.68rem', color: '#a855f7', marginLeft: '4px', fontWeight: '700' }}>
                              ({ord.size_multiplier}x)
                            </span>
                          )}
                        </td>
                        <td style={{ padding: '6px', textAlign: 'center' }}>
                          {ord.reduce_only ? (
                            <span style={{ fontSize: '0.7rem', padding: '1px 5px', borderRadius: '4px', background: 'rgba(0,122,255,0.1)', color: '#0071e3' }}>
                              只减仓
                            </span>
                          ) : (
                            <span style={{ fontSize: '0.7rem', padding: '1px 5px', borderRadius: '4px', background: 'rgba(52,199,89,0.1)', color: '#34c759' }}>
                              双向开平
                            </span>
                          )}
                        </td>
                        <td style={{ padding: '6px', textAlign: 'center' }}>
                          {ord.live_order_id ? (
                            <span style={{ fontSize: '0.7rem', padding: '1px 6px', borderRadius: '4px', background: 'rgba(52,199,89,0.12)', color: '#248a3d', fontWeight: '600' }}>
                              🟢 币安已挂单 (#{ord.live_order_id.slice(-6)})
                            </span>
                          ) : (
                            <span style={{ fontSize: '0.7rem', padding: '1px 6px', borderRadius: '4px', background: 'rgba(142,142,147,0.12)', color: '#8e8e93' }}>
                              🟢 本地模拟
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </LiquidGlass>

          {/* Recent Fills & Captured Profit */}
          <LiquidGlass cornerRadius={18} padding="1.2rem">
            <h3 style={{ fontSize: '0.95rem', fontWeight: '700', margin: '0 0 0.8rem 0', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
              <TrendingUp size={16} style={{ color: '#34c759' }} /> 最近成交记录与单格套利
            </h3>

            {fills.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '1.5rem 1rem', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
                暂无成交记录，价格触碰网格线时将实时记录套利流水。
              </div>
            ) : (
              <div style={{ maxHeight: '180px', overflowY: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid rgba(0,0,0,0.06)', color: 'var(--text-muted)' }}>
                      <th style={{ textAlign: 'left', padding: '6px' }}>时间</th>
                      <th style={{ textAlign: 'left', padding: '6px' }}>方向</th>
                      <th style={{ textAlign: 'right', padding: '6px' }}>成交价</th>
                      <th style={{ textAlign: 'right', padding: '6px' }}>数量</th>
                      <th style={{ textAlign: 'right', padding: '6px' }}>锁定利润</th>
                    </tr>
                  </thead>
                  <tbody>
                    {fills.map((f, idx) => (
                      <tr key={idx} style={{ borderBottom: '1px solid rgba(0,0,0,0.03)' }}>
                        <td style={{ padding: '6px', color: 'var(--text-muted)', fontSize: '0.75rem' }}>{f.timestamp || '--'}</td>
                        <td style={{ padding: '6px', color: f.side === 'buy' ? '#34c759' : '#ff3b30', fontWeight: '700' }}>
                          {f.side?.toUpperCase()}
                        </td>
                        <td style={{ padding: '6px', textAlign: 'right', fontWeight: '600' }}>${f.price}</td>
                        <td style={{ padding: '6px', textAlign: 'right' }}>{f.size}</td>
                        <td style={{ padding: '6px', textAlign: 'right', color: '#34c759', fontWeight: '700' }}>
                          +${gridState?.risk?.per_rung_profit_usd || '0.00'} USD
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </LiquidGlass>

          {/* AI Auto-Shift & Risk Management Events */}
          {(gridState?.events && gridState.events.length > 0) && (
            <LiquidGlass cornerRadius={18} padding="1.2rem">
              <h3 style={{ fontSize: '0.95rem', fontWeight: '700', margin: '0 0 0.8rem 0', display: 'flex', alignItems: 'center', gap: '0.4rem', color: '#0071e3' }}>
                <Zap size={16} /> 🤖 AI 智能动态移网与风控日志
              </h3>
              <div style={{ maxHeight: '140px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '6px' }}>
                {gridState.events.map((evt, idx) => (
                  <div
                    key={idx}
                    style={{
                      fontSize: '0.75rem',
                      padding: '6px 10px',
                      borderRadius: '8px',
                      background: 'rgba(0, 113, 227, 0.08)',
                      borderLeft: '3px solid #0071e3',
                      color: 'var(--text-main)',
                      lineHeight: '1.4'
                    }}
                  >
                    {evt}
                  </div>
                ))}
              </div>
            </LiquidGlass>
          )}

        </div>
      </div>

    </div>
  );
}
