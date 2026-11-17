import React, { useState, useEffect, useRef } from 'react';
import { Play, Square, RefreshCw, Zap, Shield, TrendingUp, AlertTriangle, CheckCircle2, Layers, DollarSign, Activity, Key, ExternalLink, HelpCircle, RotateCcw, Flame, Target, Percent, BarChart3, ChevronRight, Bot, Sparkles, X, FileText } from 'lucide-react';
import LiquidGlass from './LiquidGlass';

export default function GridPanel({ apiBase }) {
  const [gridState, setGridState] = useState(null);
  const [exchangeInfo, setExchangeInfo] = useState(null);
  const [currentMarketPrice, setCurrentMarketPrice] = useState(null);
  const [loading, setLoading] = useState(false);
  const [recommendLoading, setRecommendLoading] = useState(false);
  const [testingExchange, setTestingExchange] = useState(false);

  // Grid running state
  const isRunning = gridState?.running || false;
  const isRecovery = gridState?.recovery || false;
  
  // Screener state
  const [screenerData, setScreenerData] = useState([]);
  const [screenerLoading, setScreenerLoading] = useState(false);
  const [showScreenerModal, setShowScreenerModal] = useState(false);

  // Auto-Pilot state
  const [autoPilotLoading, setAutoPilotLoading] = useState(false);
  const [autoPilotData, setAutoPilotData] = useState(null);
  const [showAutoPilotModal, setShowAutoPilotModal] = useState(false);

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
  const [isLive, setIsLive] = useState(() => {
    const saved = localStorage.getItem('grid_is_live');
    return saved !== null ? saved === 'true' : false;
  });

  const handleSetIsLive = (val) => {
    setIsLive(val);
    try {
      localStorage.setItem('grid_is_live', String(val));
    } catch (e) {
      console.warn('Failed to save grid_is_live to localStorage:', e);
    }
  };
  const [aiRegimeNotice, setAiRegimeNotice] = useState(null);
  const [maxDrawdownLimit, setMaxDrawdownLimit] = useState('15');
  const [autoCompound, setAutoCompound] = useState(true);
  const [mtfInfo, setMtfInfo] = useState(null);

  // Track if initial state has been loaded to avoid resetting user edits
  const initialLoadedRef = useRef(false);
  // Mutex lock to strictly prevent double-click / concurrent start requests
  const isStartingRef = useRef(false);

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
      const balObj = exchangeInfo?.balance_info;
      const availBal = balObj && typeof balObj === 'object'
        ? Number((isRunning && balObj.total_usdt > 0) ? balObj.total_usdt : (balObj.free_usdt ?? balObj.total_usdt ?? 8.91))
        : (typeof balObj === 'number' ? balObj : 8.91);

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
          `• 破位自适应：🤖 智能动态移网追踪 (Auto-Shift)\n` +
          `• 交易环境：${isLive ? '🔴 实盘真实做市 (向 Binance 提交真实挂单)' : '🟢 本地模拟测算 (零资金风险)'}\n\n` +
          `是否立即以【${isLive ? '🔴 实盘真实下单' : '🟢 模拟盘'}】启动该网格做市？\n(提示：若需切换模式，可点击取消后在页面顶部直接切换)`;

        if (window.confirm(confirmMsg)) {
          setShowScreenerModal(false);
          setLoading(true);

          // If currently running, stop previous grid first
          if (isRunning) {
            const prevSym = gridState?.config?.symbol;
            const isDiff = prevSym && prevSym !== coin.symbol;
            let closeOldPos = false;
            if (isDiff) {
              closeOldPos = window.confirm(`⚠️ 检测到你正从【${prevSym}】切换至【${coin.symbol}】。\n是否市价平掉【${prevSym}】原持仓以释放保证金？\n\n• 点击【确定】市价平掉旧币持仓（推荐）\n• 点击【取消】保留旧币持仓`);
            }
            await fetch(`${apiBase}/api/grid/stop`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ close_position: closeOldPos })
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
              auto_open_initial_position: false,
              max_drawdown_limit_pct: parseFloat(maxDrawdownLimit) || 15.0,
              auto_compound: autoCompound
            })
          });
          const startData = await startRes.json();
          if (startRes.ok) {
            alert(`🚀 ${coin.symbol} 宽幅自适应做市已成功启动！\n• 采用 100% Maker 纯挂单做市\n• 零市价滑点消耗，安全捕捉宽幅震荡套利！`);
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

  // Trigger AI Auto-Pick & Directly Launch Grid (True 1-Click Autonomous Start)
  const handleTriggerAutoPick = async () => {
    if (isStartingRef.current || autoPilotLoading) {
      console.warn('Grid launch already in progress, debouncing duplicate call');
      return;
    }
    isStartingRef.current = true;
    setAutoPilotLoading(true);
    try {
      const balObj = exchangeInfo?.balance_info;
      const availBal = balObj && typeof balObj === 'object'
        ? Number((isRunning && balObj.total_usdt > 0) ? balObj.total_usdt : (balObj.free_usdt ?? balObj.total_usdt ?? 8.91))
        : (typeof balObj === 'number' ? balObj : 8.91);

      // 1. Fetch AI Optimal Coin & Parameters based on capital
      const res = await fetch(`${apiBase}/api/grid/auto-pick`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          available_balance: availBal,
          is_live: isLive
        })
      });
      const data = await res.json();
      if (!res.ok || data.status !== 'success') {
        alert(`智能选币失败: ${data.detail || data.message || '请确认行情接口正常'}`);
        return;
      }

      setAutoPilotData(data);
      // Keep modal closed so user is not prompted with duplicate launch button
      setShowAutoPilotModal(false);

      const rec = data.recommended_params;
      const sym = data.selected_symbol;

      // Populate form parameters
      setSelectedSymbol(sym);
      setLower(formatPrecisionStr(rec.recommended_lower, 8));
      setUpper(formatPrecisionStr(rec.recommended_upper, 8));
      setGridCount(String(rec.recommended_grid_count || 10));
      if (rec.recommended_size_base !== undefined) setSizeBase(formatPrecisionStr(rec.recommended_size_base, 6));
      if (rec.recommended_leverage !== undefined) setLeverage(String(rec.recommended_leverage));
      if (rec.mode) setGridMode(rec.mode);
      if (rec.recommended_action) setOutOfRangeAction(rec.recommended_action);
      if (rec.regime_desc) setAiRegimeNotice(rec.regime_desc);
      if (rec.current_price !== undefined) setCurrentMarketPrice(formatPrecisionStr(rec.current_price, 8));

      // 2. If in Live mode, verify API configuration
      if (isLive && (!exchangeInfo || !exchangeInfo.is_configured)) {
        alert('⚠️ 当前处于实盘模式，但尚未配置实盘 API Key / Secret！请先前往设置配置。');
        return;
      }

      // If already running, prompt user to confirm switching
      if (isRunning) {
        if (!window.confirm(`⚠️ 当前已有做市任务正在运行中。\n\n是否立即停止当前任务，并一键切换启动【${sym}】AI 智能做市？`)) {
          return;
        }
        const prevSym = gridState?.config?.symbol;
        const isDiff = prevSym && prevSym !== sym;
        let closeOldPos = false;
        if (isDiff) {
          closeOldPos = window.confirm(`⚠️ 检测到你正从【${prevSym}】切换至【${sym}】。\n是否市价平掉【${prevSym}】原持仓以释放保证金？\n\n• 点击【确定】市价平掉旧币持仓（推荐）\n• 点击【取消】保留旧币持仓`);
        }
        await fetch(`${apiBase}/api/grid/stop`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ close_position: closeOldPos })
        });
      }

      // 3. Directly Launch the Grid Market Making!
      const startRes = await fetch(`${apiBase}/api/grid/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: sym,
          lower: rec.recommended_lower,
          upper: rec.recommended_upper,
          grid_count: rec.recommended_grid_count,
          size_base: rec.recommended_size_base,
          leverage: rec.recommended_leverage,
          mode: rec.mode || 'neutral',
          sizing_mode: sizingMode,
          out_of_range_action: 'auto_shift',
          is_live: isLive,
          auto_open_initial_position: false,
          max_drawdown_limit_pct: parseFloat(maxDrawdownLimit) || 15.0,
          auto_compound: autoCompound
        })
      });
      const startData = await startRes.json();
      if (startRes.ok) {
        const modeText = isLive ? '🔴 实盘真实挂单 (Binance 交易所)' : '🟢 本地模拟测算';
        alert(`🚀 【${sym}】AI 全自动智能做市已直接启动！\n\n• 做市环境：${modeText}\n• 智能研判：${data.reasoning?.[2] || '资金自治最优解'}\n• 宽幅通道：$${rec.recommended_lower} ~ $${rec.recommended_upper} (${rec.recommended_grid_count} 格)\n• 杠杆倍数：${rec.recommended_leverage}x (单格 ${rec.recommended_size_base})\n• 采用 100% Maker Post-Only 纯挂单做市，零市价滑点磨损！`);
        fetchGridState();
      } else {
        alert(`启动做市失败: ${startData.detail || startData.message || '未知错误'}`);
      }
    } catch (err) {
      alert(`AI 一键做市异常: ${err.message}`);
    } finally {
      setAutoPilotLoading(false);
      setTimeout(() => {
        isStartingRef.current = false;
      }, 1500);
    }
  };

  // Launch or Apply Auto-Pilot Decision
  const handleLaunchAutoPilot = async (data, executeNow = true) => {
    if (!data || !data.recommended_params) return;
    if (isStartingRef.current || loading) return;
    isStartingRef.current = true;
    const rec = data.recommended_params;
    const sym = data.selected_symbol;

    setSelectedSymbol(sym);
    setLower(formatPrecisionStr(rec.recommended_lower, 8));
    setUpper(formatPrecisionStr(rec.recommended_upper, 8));
    setGridCount(String(rec.recommended_grid_count || 10));
    if (rec.recommended_size_base !== undefined) setSizeBase(formatPrecisionStr(rec.recommended_size_base, 6));
    if (rec.recommended_leverage !== undefined) setLeverage(String(rec.recommended_leverage));
    if (rec.mode) setGridMode(rec.mode);
    if (rec.recommended_action) setOutOfRangeAction(rec.recommended_action);
    if (rec.regime_desc) setAiRegimeNotice(rec.regime_desc);
    if (rec.current_price !== undefined) setCurrentMarketPrice(formatPrecisionStr(rec.current_price, 8));

    if (!executeNow) {
      setShowAutoPilotModal(false);
      alert(`已成功将【${sym}】的全套做市参数载入左侧配置面板！`);
      return;
    }

    if (isLive && (!exchangeInfo || !exchangeInfo.is_configured)) {
      alert('⚠️ 你开启了实盘模式，但尚未在「核心配置参数」中配置实盘 API Key / Secret！请先前往设置配置。');
      return;
    }

    if (isRunning) {
      if (!window.confirm(`⚠️ 当前已有做市任务正在运行中。\n\n是否立即停止当前任务，并全托管启动【${sym}】做市？`)) {
        return;
      }
    }

    setShowAutoPilotModal(false);
    setLoading(true);
    try {
      if (isRunning) {
        const prevSym = gridState?.config?.symbol;
        const isDiff = prevSym && prevSym !== sym;
        let closeOldPos = false;
        if (isDiff) {
          closeOldPos = window.confirm(`⚠️ 检测到你正从【${prevSym}】切换至【${sym}】。\n是否市价平掉【${prevSym}】原持仓以释放保证金？\n\n• 点击【确定】市价平掉旧币持仓（推荐）\n• 点击【取消】保留旧币持仓`);
        }
        await fetch(`${apiBase}/api/grid/stop`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ close_position: closeOldPos })
        });
      }

      const startRes = await fetch(`${apiBase}/api/grid/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: sym,
          lower: rec.recommended_lower,
          upper: rec.recommended_upper,
          grid_count: rec.recommended_grid_count,
          size_base: rec.recommended_size_base,
          leverage: rec.recommended_leverage,
          mode: rec.mode || 'neutral',
          sizing_mode: sizingMode,
          out_of_range_action: 'auto_shift',
          is_live: isLive,
          auto_open_initial_position: false,
          max_drawdown_limit_pct: parseFloat(maxDrawdownLimit) || 15.0,
          auto_compound: autoCompound
        })
      });
      const startData = await startRes.json();
      if (startRes.ok) {
        alert(`🚀 【${sym}】AI 全托管宽幅做市已成功启动！\n\n• 宽幅通道：$${rec.recommended_lower} ~ $${rec.recommended_upper}\n• 格数：${rec.recommended_grid_count} 格 (${rec.recommended_leverage}x 杠杆)\n• 单格数量：${rec.recommended_size_base}\n• 采用 100% Maker Post-Only 纯挂单做市，安全保全本金！`);
        fetchGridState();
      } else {
        alert(`启动失败: ${startData.detail || startData.message || '未知错误'}`);
      }
    } catch (err) {
      alert(`启动异常: ${err.message}`);
    } finally {
      setLoading(false);
      setTimeout(() => {
        isStartingRef.current = false;
      }, 1500);
    }
  };

  // 1-Click Auto-Deploy user's chosen coin (我只负责选币 其他交给系统)
  const handleOneClickDeploySelectedCoin = async (targetSymbol = null) => {
    if (isStartingRef.current || recommendLoading) return;
    isStartingRef.current = true;
    const sym = targetSymbol || selectedSymbol;
    setRecommendLoading(true);
    try {
      const balObj = exchangeInfo?.balance_info;
      const availBal = balObj && typeof balObj === 'object'
        ? Number((isRunning && balObj.total_usdt > 0) ? balObj.total_usdt : (balObj.free_usdt ?? balObj.total_usdt ?? 8.91))
        : (typeof balObj === 'number' ? balObj : 8.91);

      if (availBal < 25 && sym.includes('BTC')) {
        const proceed = window.confirm(
          `⚠️ 账户资金安全提示：\n当前可用资金为 $${availBal.toFixed(2)} USDT。\n` +
          `BTC 永续合约币安单笔最小名义价值为 ~$65，在此资金下将强制使用高杠杆。\n\n` +
          `建议优先选择 DOGE, 1000PEPE, SUI 或 XRP 等单笔门槛低的币种。\n\n` +
          `是否仍要继续由 AI 计算并配置 BTC？`
        );
        if (!proceed) {
          setRecommendLoading(false);
          return;
        }
      }

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
        if (data.recommended_size_base !== undefined) setSizeBase(formatPrecisionStr(data.recommended_size_base, 6));
        if (data.recommended_leverage !== undefined) setLeverage(String(data.recommended_leverage));
        if (data.mode) setGridMode(data.mode);
        if (data.recommended_action) setOutOfRangeAction(data.recommended_action);
        if (data.regime_desc) setAiRegimeNotice(data.regime_desc);
        if (data.mtf_info) setMtfInfo(data.mtf_info);
        if (data.current_price !== undefined) setCurrentMarketPrice(formatPrecisionStr(data.current_price, 8));

        const modeMap = { long: '逢低做多 (自动开立多头底仓 + 双向做市)', short: '逢高做空 (自动开立空头底仓 + 双向做市)', neutral: '中性双向 (双向挂单)' };
        const modeTitle = modeMap[data.mode] || data.mode;
        if (isLive && data.margin_warning) {
          alert(`${data.margin_warning}\n\n已自动为您将推荐参数填入面板。请充值或选择其他币种（或使用模拟盘测试）。`);
          return;
        }

        const confirmMsg = `🤖 【⚡ AI 全托管做市就绪】\n\n` +
          `• 选定币种：${sym}\n` +
          `• 多空形态：${data.regime_desc || '多周期共振'}\n` +
          `• 匹配模式：${modeTitle}\n` +
          `• 智能区间：$${formatPrecisionStr(data.recommended_lower, 8)} ~ $${formatPrecisionStr(data.recommended_upper, 8)} (${data.recommended_grid_count} 格)\n` +
          `• 单格委托：${formatPrecisionStr(data.recommended_size_base, 6)} (单笔约 $${singleNotional} USDT，${data.recommended_leverage}x 杠杆)\n` +
          `• 保证金占用：约 $${data.estimated_margin_usd} USD (预留充裕防穿仓缓冲)\n` +
          `• 破位策略：🤖 智能动态移网追踪 (Auto-Shift)\n` +
          `• 交易环境：${isLive ? '🔴 实盘真实做市 (向 Binance 提交真实委托)' : '🟢 本地模拟测算 (零资金风险)'}\n\n` +
          (data.margin_warning ? `${data.margin_warning}\n\n` : '') +
          `是否立即以【${isLive ? '🔴 实盘真实下单' : '🟢 模拟盘'}】启动此币做市？\n(提示：若需切换模式，可点击取消后在页面顶部直接切换；或点击取消仅保存参数)`;

        if (window.confirm(confirmMsg)) {
          if (isLive && (!exchangeInfo || !exchangeInfo.is_configured)) {
            alert('⚠️ 你开启了实盘模式，但尚未配置实盘 API Key / Secret！请先前往设置配置。');
            return;
          }

          setLoading(true);
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
              symbol: sym,
              lower: data.recommended_lower,
              upper: data.recommended_upper,
              grid_count: data.recommended_grid_count,
              size_base: data.recommended_size_base,
              leverage: data.recommended_leverage,
              mode: data.mode,
              sizing_mode: sizingMode,
              out_of_range_action: 'auto_shift',
              is_live: isLive,
              auto_open_initial_position: false,
              max_drawdown_limit_pct: parseFloat(maxDrawdownLimit) || 15.0,
              auto_compound: autoCompound
            })
          });
          const startData = await startRes.json();
          if (startRes.ok) {
            alert(`🚀 【${sym}】AI 托管做市已成功启动！\n• 采用 100% Maker 纯挂单安全做市！`);
            fetchGridState();
          } else {
            alert(`启动失败: ${startData.detail || startData.message || '未知错误'}`);
          }
        }
      } else {
        alert(`获取推荐失败: ${data.detail || '请确认行情正常'}`);
      }
    } catch (err) {
      alert(`全托管启动异常: ${err.message}`);
    } finally {
      setRecommendLoading(false);
      setLoading(false);
      setTimeout(() => {
        isStartingRef.current = false;
      }, 1500);
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

      if (data.latest_regime_info) {
        setMtfInfo(data.latest_regime_info);
        setAiRegimeNotice(data.latest_regime_info.regime_title);
      }

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
          // If currently running, sync isLive with running config; otherwise keep user's persisted choice
          if (data.running) {
            const backendLive = Boolean(data.config.is_live);
            setIsLive(backendLive);
            try {
              localStorage.setItem('grid_is_live', String(backendLive));
            } catch (e) {}
          }
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
      const balObj = exchangeInfo?.balance_info;
      const availBal = balObj && typeof balObj === 'object'
        ? Number((isRunning && balObj.total_usdt > 0) ? balObj.total_usdt : (balObj.free_usdt ?? balObj.total_usdt ?? 8.91))
        : (typeof balObj === 'number' ? balObj : 8.91);

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
        if (data.mtf_info) {
          setMtfInfo(data.mtf_info);
        }
        if (data.current_price !== undefined) {
          setCurrentMarketPrice(formatPrecisionStr(data.current_price, 8));
        }
        
        const singleNotional = (data.recommended_size_base * data.current_price).toFixed(2);
        const marginPct = ((data.estimated_margin_usd / (data.available_balance_used || 8.91)) * 100).toFixed(0);
        const warnMsg = data.margin_warning ? `\n\n${data.margin_warning}` : '';
        alert(`🤖 AI 全自动智能推荐完成！\n\n【做市币种】：${sym}\n【研判结论】：${data.regime_desc || '已匹配'}\n• 自动匹配模式：${modeMap[data.mode] || data.mode}\n• 自动安全区间：$${formatPrecisionStr(data.recommended_lower, 8)} ~ $${formatPrecisionStr(data.recommended_upper, 8)} (${data.recommended_grid_count} 格)\n• 智能单格数量：${formatPrecisionStr(data.recommended_size_base, 6)} (单笔约 $${singleNotional} USDT)\n• 自动匹配杠杆：${data.recommended_leverage}x (占用保证金 $${data.estimated_margin_usd} USD，仅占可用资金 ${marginPct}%)\n• 破位自适应动作：🤖 智能动态移网追踪 (Auto-Shift)${warnMsg}`);
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
    if (isStartingRef.current || loading) return;
    isStartingRef.current = true;
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
        auto_open_initial_position: false,
        max_drawdown_limit_pct: parseFloat(maxDrawdownLimit) || 15.0,
        auto_compound: autoCompound
      };
      const res = await fetch(`${apiBase}/api/grid/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (res.ok) {
        alert(`🚀 机构级宽幅网格量化做市机器人启动成功！\n• 采用 100% Maker Post-Only 纯挂单做市，零市价滑点磨损！`);
        fetchGridState();
      } else {
        alert(`启动失败: ${data.detail || data.message || '未知错误'}`);
      }
    } catch (err) {
      alert(`请求错误: ${err.message}`);
    } finally {
      setLoading(false);
      setTimeout(() => {
        isStartingRef.current = false;
      }, 1500);
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

  // Emergency Close All Live Positions
  const handleEmergencyClosePosition = async () => {
    if (!window.confirm('⚠️ 确定要立即强制向交易所提交市价全平指令，清空该币种全部多空持仓并撤销挂单吗？')) return;
    setLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/grid/emergency-close-position`, { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        alert(`🛑 成功全平交易所持仓！已平仓 ${data.closed_count || 0} 笔持仓。`);
        fetchGridState();
      } else {
        alert(`平仓受阻: ${data.detail || '未知错误'}`);
      }
    } catch (err) {
      alert(`网络或执行异常: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  // Manually Reset Grid Circuit Breaker / Clear Cooldown
  const handleResetCircuitBreaker = async () => {
    if (!window.confirm('确定要手动解除网格冷静保护期，立即恢复做市待机状态吗？')) return;
    setLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/grid/reset-breaker`, { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        alert('🔓 网格熔断冷静期已成功解除，您现在可以重新配置或直接一键启动做市！');
        fetchGridState();
      } else {
        alert(`解除熔断失败: ${data.detail || '未知错误'}`);
      }
    } catch (err) {
      alert(`解除熔断异常: ${err.message}`);
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

  const stats = gridState?.stats || {};
  const risk = gridState?.risk || {};
  const drawdownStats = gridState?.drawdown_stats || {};
  const activeOrders = gridState?.active_orders || [];
  const fills = gridState?.recent_fills || [];

  const peakEquity = Number(drawdownStats.peak_equity_usd || 0);
  const currDrawdownUsd = Number(drawdownStats.current_drawdown_usd || 0);
  const currDrawdownPct = Number(drawdownStats.current_drawdown_pct || 0);
  const maxDrawdownUsd = Number(drawdownStats.max_drawdown_usd || 0);
  const maxDrawdownPct = Number(drawdownStats.max_drawdown_pct || 0);
  const unrealizedPnl = Number(drawdownStats.unrealized_pnl_usd || 0);
  const netProfit = drawdownStats.net_profit_usd !== undefined ? Number(drawdownStats.net_profit_usd) : Number(stats.grid_profit_usd || 0);
  const liqDistance = Number(drawdownStats.liq_distance_pct !== undefined ? drawdownStats.liq_distance_pct : 100);
  const isTripped = Boolean(drawdownStats.circuit_breaker_tripped);
  const maxDrawdownLimitPct = Number(gridState?.config?.max_drawdown_limit_pct || maxDrawdownLimit || 15);
  const compoundScaleRatio = Number(gridState?.compound_scale_ratio || 1.0);
  const isAutoCompound = gridState?.config?.auto_compound !== undefined ? Boolean(gridState.config.auto_compound) : autoCompound;
  const fundingRateInfo = gridState?.funding_rate_info || {};
  const frRate8h = Number(fundingRateInfo.rate_8h_pct !== undefined ? fundingRateInfo.rate_8h_pct : 0.01);
  const frEstApr = Number(fundingRateInfo.est_apr_pct !== undefined ? fundingRateInfo.est_apr_pct : 10.95);
  const circuitBreakerRemainingSec = Number(gridState?.circuit_breaker_cooldown_remaining_sec || 0);

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
  const freeUsdtBal = exchangeInfo?.balance_info?.free_usdt !== undefined
    ? Number(exchangeInfo.balance_info.free_usdt).toFixed(2)
    : (typeof exchangeInfo?.balance_info === 'number' ? Number(exchangeInfo.balance_info).toFixed(2) : '0.00');

  const totalUsdtBal = exchangeInfo?.balance_info?.total_usdt !== undefined
    ? Number(exchangeInfo.balance_info.total_usdt).toFixed(2)
    : (drawdownStats?.live_wallet_balance_usd !== undefined ? Number(drawdownStats.live_wallet_balance_usd).toFixed(2) : freeUsdtBal);

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
              padding: '3px 10px',
              borderRadius: '12px',
              background: isRecovery ? 'rgba(255, 149, 0, 0.15)' : isRunning ? (
                gridState?.config?.is_live ? 'rgba(255, 59, 48, 0.15)' : 'rgba(52, 199, 89, 0.15)'
              ) : 'rgba(142, 142, 147, 0.15)',
              color: isRecovery ? '#ff9500' : isRunning ? (
                gridState?.config?.is_live ? '#d70015' : '#248a3d'
              ) : '#8e8e93',
              fontWeight: '800',
              border: `1px solid ${isRecovery ? 'rgba(255, 149, 0, 0.3)' : isRunning ? (
                gridState?.config?.is_live ? 'rgba(255, 59, 48, 0.4)' : 'rgba(52, 199, 89, 0.4)'
              ) : 'rgba(142, 142, 147, 0.3)'}`,
              display: 'flex',
              alignItems: 'center',
              gap: '6px'
            }}>
              {isRecovery ? (
                '🛡️ 只减仓回收中'
              ) : isRunning ? (
                gridState?.config?.is_live ? (
                  <>
                    <span style={{ width: '7px', height: '7px', borderRadius: '50%', background: '#d70015', display: 'inline-block', boxShadow: '0 0 6px #d70015' }} />
                    🔴 实盘做市中 (Binance真实挂单)
                  </>
                ) : (
                  <>
                    <span style={{ width: '7px', height: '7px', borderRadius: '50%', background: '#34c759', display: 'inline-block' }} />
                    🟢 本地模拟测算中 (未在交易所下单)
                  </>
                )
              ) : (
                '○ 待机中'
              )}
            </span>
          </div>
          <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', margin: 0 }}>
            搭载黄金金字塔非对称加权、ATR 波动率自适应与全天候对账引擎（100% Maker Post-Only 稳健套利）
          </p>
        </div>

        {/* Top Action Buttons & Live/Paper Segmented Switcher */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap' }}>
          
          {/* Header Segmented Mode Switcher */}
          <div style={{
            display: 'flex',
            alignItems: 'center',
            background: 'rgba(0,0,0,0.06)',
            borderRadius: '10px',
            padding: '3px',
            border: '1px solid var(--border-color)',
            gap: '2px'
          }}>
            <button
              onClick={() => handleSetIsLive(false)}
              disabled={isRunning}
              style={{
                padding: '6px 12px',
                borderRadius: '8px',
                fontSize: '0.8rem',
                fontWeight: !isLive ? '800' : '600',
                background: !isLive ? 'rgba(52, 199, 89, 0.2)' : 'transparent',
                color: !isLive ? '#248a3d' : 'var(--text-muted)',
                border: !isLive ? '1px solid rgba(52, 199, 89, 0.35)' : '1px solid transparent',
                cursor: isRunning ? 'not-allowed' : 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
                boxShadow: !isLive ? '0 1px 4px rgba(52, 199, 89, 0.15)' : 'none',
                transition: 'all 0.15s ease'
              }}
              title={isRunning ? '做市运行中无法切换，请先停止做市' : '切换为本地模拟盘测算（零风险）'}
            >
              🟢 模拟盘测算
            </button>
            <button
              onClick={() => {
                if (!exchangeInfo?.is_configured) {
                  alert('⚠️ 尚未检测到配置的实盘 API Key，请先前往设置配置 Binance 凭证。');
                  return;
                }
                handleSetIsLive(true);
              }}
              disabled={isRunning}
              style={{
                padding: '6px 12px',
                borderRadius: '8px',
                fontSize: '0.8rem',
                fontWeight: isLive ? '800' : '600',
                background: isLive ? 'rgba(255, 59, 48, 0.2)' : 'transparent',
                color: isLive ? '#d70015' : 'var(--text-muted)',
                border: isLive ? '1px solid rgba(255, 59, 48, 0.4)' : '1px solid transparent',
                cursor: isRunning ? 'not-allowed' : 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
                boxShadow: isLive ? '0 0 10px rgba(255, 59, 48, 0.3)' : 'none',
                transition: 'all 0.15s ease'
              }}
              title={isRunning ? '做市运行中无法切换，请先停止做市' : `切换为币安交易所实盘真实做市 (当前可用: $${freeUsdtBal} USDT)`}
            >
              🔴 实盘真实做市
            </button>
          </div>

          <button
            onClick={handleTriggerAutoPick}
            disabled={autoPilotLoading}
            style={{
              background: isLive
                ? 'linear-gradient(135deg, #ff3b30 0%, #ff9500 100%)'
                : 'linear-gradient(135deg, #0071e3 0%, #34c759 100%)',
              color: '#fff',
              border: 'none',
              borderRadius: '10px',
              padding: '8px 16px',
              fontSize: '0.84rem',
              fontWeight: '800',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              boxShadow: isLive ? '0 3px 14px rgba(255, 59, 48, 0.4)' : '0 3px 12px rgba(52, 199, 89, 0.35)',
              transition: 'all 0.2s ease'
            }}
            title="点击此按钮，AI 将根据您的可用资金自动优选标的，并立即一键启动网格做市！"
          >
            <Bot size={16} />
            {autoPilotLoading
              ? '🤖 AI 正在选币并直接启动做市...'
              : (isLive ? '⚡ 🤖 AI 资金全自动一键实盘做市' : '⚡ 🤖 AI 资金全自动一键模拟做市')
            }
          </button>

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

          {autoPilotData && (
            <button
              onClick={() => setShowAutoPilotModal(!showAutoPilotModal)}
              style={{
                background: showAutoPilotModal ? 'rgba(0, 113, 227, 0.15)' : 'rgba(0, 113, 227, 0.08)',
                color: '#0071e3',
                border: '1px solid rgba(0, 113, 227, 0.3)',
                borderRadius: '10px',
                padding: '8px 14px',
                fontSize: '0.82rem',
                fontWeight: '700',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                boxShadow: '0 2px 8px rgba(0, 113, 227, 0.12)',
                transition: 'all 0.2s ease'
              }}
            >
              <FileText size={15} />
              {showAutoPilotModal ? '收起 AI 报告' : '📋 AI 选币报告'}
            </button>
          )}
        </div>
      </LiquidGlass>

      {/* AI Capital-Adaptive Auto-Pilot Decision Card */}
      {showAutoPilotModal && autoPilotData && (
        <LiquidGlass
          cornerRadius={18}
          padding="1.3rem"
          style={{
            border: '1px solid rgba(52, 199, 89, 0.4)',
            background: 'linear-gradient(135deg, rgba(52, 199, 89, 0.08) 0%, rgba(0, 113, 227, 0.06) 100%)',
            display: 'flex',
            flexDirection: 'column',
            gap: '1rem',
            boxShadow: '0 8px 32px rgba(52, 199, 89, 0.12)'
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid rgba(52, 199, 89, 0.2)', paddingBottom: '0.7rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
              <Bot size={22} style={{ color: '#34c759' }} />
              <div>
                <h3 style={{ fontSize: '1.05rem', fontWeight: '800', margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
                  🤖 AI 智能资金自动选币做市决策报告
                  <span style={{ fontSize: '0.72rem', padding: '2px 8px', borderRadius: '12px', background: 'rgba(52, 199, 89, 0.2)', color: '#34c759', fontWeight: '700' }}>
                    资金自治最优解
                  </span>
                </h3>
                <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                  根据当前可用资金 (${autoPilotData.available_balance} USDT)，经多周期波动率、盘口深度与单笔名义门槛深度测算得出
                </span>
              </div>
            </div>
            <button
              onClick={() => setShowAutoPilotModal(false)}
              style={{
                background: 'rgba(0,0,0,0.06)',
                border: 'none',
                borderRadius: '50%',
                width: '28px',
                height: '28px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                cursor: 'pointer',
                color: 'var(--text-muted)'
              }}
            >
              <X size={16} />
            </button>
          </div>

          {/* Reasoning Chain */}
          <div style={{ background: 'rgba(0,0,0,0.03)', borderRadius: '12px', padding: '0.9rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            <div style={{ fontSize: '0.8rem', fontWeight: '700', color: 'var(--text-main)', marginBottom: '2px' }}>
              🧠 算法决策与风控逻辑链：
            </div>
            {autoPilotData.reasoning?.map((r, idx) => (
              <div key={idx} style={{ fontSize: '0.78rem', lineHeight: '1.45', color: 'var(--text-main)', display: 'flex', alignItems: 'flex-start', gap: '6px' }}>
                <span>{r}</span>
              </div>
            ))}
          </div>

          {/* Parameter Metrics Grid */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '0.8rem' }}>
            <div style={{ background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '10px', padding: '8px 12px' }}>
              <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>推荐标的</div>
              <div style={{ fontSize: '1.1rem', fontWeight: '800', color: '#0071e3', marginTop: '2px' }}>
                {autoPilotData.selected_symbol}
              </div>
            </div>
            <div style={{ background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '10px', padding: '8px 12px' }}>
              <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>运行模式</div>
              <div style={{ fontSize: '0.92rem', fontWeight: '700', color: '#34c759', marginTop: '4px' }}>
                {autoPilotData.recommended_params?.mode === 'neutral' ? '⚖️ 中性双向' : (autoPilotData.recommended_params?.mode === 'long' ? '📈 逢低做多' : '📉 逢高做空')}
              </div>
            </div>
            <div style={{ background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '10px', padding: '8px 12px' }}>
              <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>安全区间</div>
              <div style={{ fontSize: '0.85rem', fontWeight: '700', marginTop: '4px' }}>
                ${autoPilotData.recommended_params?.recommended_lower} ~ ${autoPilotData.recommended_params?.recommended_upper}
              </div>
            </div>
            <div style={{ background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '10px', padding: '8px 12px' }}>
              <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>网格格数 / 杠杆</div>
              <div style={{ fontSize: '0.92rem', fontWeight: '700', marginTop: '4px' }}>
                {autoPilotData.recommended_params?.recommended_grid_count} 格 / {autoPilotData.recommended_params?.recommended_leverage}x
              </div>
            </div>
            <div style={{ background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '10px', padding: '8px 12px' }}>
              <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>预估占用保证金</div>
              <div style={{ fontSize: '0.92rem', fontWeight: '700', color: '#ff9500', marginTop: '4px' }}>
                ${autoPilotData.recommended_params?.estimated_margin_usd} USD
              </div>
            </div>
          </div>

          {/* Mode Selector Card inside Auto-Pilot Modal */}
          <div style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            background: isLive ? 'rgba(255, 59, 48, 0.08)' : 'rgba(52, 199, 89, 0.08)',
            border: `1px solid ${isLive ? 'rgba(255, 59, 48, 0.25)' : 'rgba(52, 199, 89, 0.25)'}`,
            borderRadius: '10px',
            padding: '8px 14px',
            marginTop: '0.2rem'
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ fontSize: '0.82rem', fontWeight: '800', color: isLive ? '#d70015' : '#248a3d' }}>
                {isLive ? '🔴 实盘模式：将向 Binance 交易所提交真实资金订单' : '🟢 模拟模式：本地撮合计算收益，无资金风险'}
              </span>
              {isLive && (
                <span style={{ fontSize: '0.74rem', color: 'var(--text-muted)' }}>
                  (实盘可用: ${freeUsdtBal} USDT)
                </span>
              )}
            </div>
            <div style={{ display: 'flex', gap: '4px' }}>
              <button
                type="button"
                onClick={() => handleSetIsLive(false)}
                style={{
                  padding: '5px 12px',
                  borderRadius: '6px',
                  fontSize: '0.76rem',
                  fontWeight: !isLive ? '800' : '500',
                  background: !isLive ? '#34c759' : 'transparent',
                  color: !isLive ? '#fff' : 'var(--text-muted)',
                  border: !isLive ? 'none' : '1px solid var(--border-color)',
                  cursor: 'pointer'
                }}
              >
                🟢 模拟盘测算
              </button>
              <button
                type="button"
                onClick={() => {
                  if (!exchangeInfo?.is_configured) {
                    alert('⚠️ 尚未检测到配置的实盘 API Key，请先前往设置配置 Binance 凭证。');
                    return;
                  }
                  handleSetIsLive(true);
                }}
                style={{
                  padding: '5px 12px',
                  borderRadius: '6px',
                  fontSize: '0.76rem',
                  fontWeight: isLive ? '800' : '500',
                  background: isLive ? '#ff3b30' : 'transparent',
                  color: isLive ? '#fff' : 'var(--text-muted)',
                  border: isLive ? 'none' : '1px solid var(--border-color)',
                  cursor: 'pointer'
                }}
              >
                🔴 实盘真实做市
              </button>
            </div>
          </div>

          {/* Action Buttons */}
          <div style={{ display: 'flex', gap: '0.8rem', justifyContent: 'flex-end', alignItems: 'center', marginTop: '0.2rem' }}>
            <button
              onClick={() => handleTriggerAutoPick()}
              disabled={autoPilotLoading}
              style={{
                background: 'rgba(0,0,0,0.05)',
                border: '1px solid var(--border-color)',
                borderRadius: '8px',
                padding: '8px 14px',
                fontSize: '0.8rem',
                fontWeight: '600',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '5px'
              }}
            >
              <RefreshCw size={13} className={autoPilotLoading ? 'spin' : ''} />
              重新评估
            </button>
            <button
              onClick={() => handleLaunchAutoPilot(autoPilotData, false)}
              style={{
                background: 'rgba(0, 113, 227, 0.08)',
                color: '#0071e3',
                border: '1px solid rgba(0, 113, 227, 0.25)',
                borderRadius: '8px',
                padding: '8px 14px',
                fontSize: '0.8rem',
                fontWeight: '700',
                cursor: 'pointer'
              }}
            >
              📋 仅填入左侧表单
            </button>
            {isRunning ? (
              <button
                onClick={() => setShowAutoPilotModal(false)}
                style={{
                  background: 'rgba(52, 199, 89, 0.15)',
                  color: '#34c759',
                  border: '1px solid rgba(52, 199, 89, 0.3)',
                  borderRadius: '8px',
                  padding: '8px 20px',
                  fontSize: '0.85rem',
                  fontWeight: '800',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px'
                }}
              >
                <CheckCircle2 size={14} style={{ color: '#34c759' }} />
                做市运行中 (点击关闭)
              </button>
            ) : (
              <button
                onClick={() => handleLaunchAutoPilot(autoPilotData, true)}
                disabled={loading || autoPilotLoading}
                style={{
                  background: isLive
                    ? 'linear-gradient(135deg, #ff3b30 0%, #ff9500 100%)'
                    : 'linear-gradient(135deg, #34c759 0%, #0071e3 100%)',
                  color: '#fff',
                  border: 'none',
                  borderRadius: '8px',
                  padding: '8px 20px',
                  fontSize: '0.85rem',
                  fontWeight: '800',
                  cursor: 'pointer',
                  boxShadow: isLive ? '0 4px 14px rgba(255, 59, 48, 0.35)' : '0 4px 14px rgba(52, 199, 89, 0.35)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px'
                }}
              >
                <Play size={14} fill="#fff" />
                {isLive ? '🔴 实盘一键托管启动 (向币安下单)' : '🟢 模拟盘一键托管启动'}
              </button>
            )}
          </div>
        </LiquidGlass>
      )}

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
          <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.25rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '4px' }}>
            <span>已完成配对套利：<strong>{stats.completed_rungs || 0} 轮</strong></span>
            <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
              账户总净利: <strong style={{ color: netProfit >= 0 ? '#34c759' : '#ff3b30' }}>{netProfit >= 0 ? `+$${netProfit.toFixed(2)}` : `-$${Math.abs(netProfit).toFixed(2)}`}</strong> (手续费: -${Number(stats.total_fees_usd || 0).toFixed(4)})
            </span>
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

      {/* 🛡️ Institutional Risk & Drawdown Monitor Card */}
      <LiquidGlass
        cornerRadius={16}
        padding="1rem 1.25rem"
        style={{
          background: isTripped
            ? 'linear-gradient(135deg, rgba(255, 59, 48, 0.15) 0%, rgba(255, 149, 0, 0.08) 100%)'
            : 'linear-gradient(135deg, rgba(0, 113, 227, 0.04) 0%, rgba(52, 199, 89, 0.03) 100%)',
          border: isTripped
            ? '1px solid rgba(255, 59, 48, 0.4)'
            : '1px solid rgba(0, 113, 227, 0.18)',
          display: 'flex',
          flexDirection: 'column',
          gap: '0.8rem'
        }}
      >
        {isTripped && (
          <div style={{
            background: 'linear-gradient(135deg, rgba(255, 59, 48, 0.15), rgba(255, 149, 0, 0.15))',
            border: '1px solid rgba(255, 59, 48, 0.35)',
            color: '#ff3b30',
            padding: '10px 14px',
            borderRadius: '10px',
            fontSize: '0.85rem',
            display: 'flex',
            flexDirection: 'column',
            gap: '6px'
          }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '8px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontWeight: '700' }}>
                <span style={{ fontSize: '1.2rem' }}>🚨</span>
                <span>【风控硬熔断已触发】：已紧急全平持仓并停机保全本金！</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <button
                  onClick={handleResetCircuitBreaker}
                  disabled={loading}
                  style={{
                    background: 'rgba(52, 199, 89, 0.2)',
                    color: '#34c759',
                    border: '1px solid rgba(52, 199, 89, 0.45)',
                    padding: '4px 10px',
                    borderRadius: '6px',
                    fontWeight: '700',
                    fontSize: '0.78rem',
                    cursor: 'pointer',
                    transition: 'all 0.2s ease'
                  }}
                  title="手动解除当前冷静保护期，可立即重新配置或启动做市"
                >
                  🔓 手动解除冷静期
                </button>
                {isLive && (
                  <button
                    onClick={handleEmergencyClosePosition}
                    disabled={loading}
                    style={{
                      background: 'rgba(255, 59, 48, 0.25)',
                      color: '#ff3b30',
                      border: '1px solid rgba(255, 59, 48, 0.45)',
                      padding: '4px 10px',
                      borderRadius: '6px',
                      fontWeight: '700',
                      fontSize: '0.78rem',
                      cursor: 'pointer',
                      transition: 'all 0.2s ease'
                    }}
                    title="点击强制向交易所发送市价全平指令，清除交易所遗留持仓与挂单"
                  >
                    ⚡ 再次确保全平持仓
                  </button>
                )}
                <div style={{
                  background: 'rgba(255, 59, 48, 0.2)',
                  padding: '4px 10px',
                  borderRadius: '6px',
                  fontWeight: '800',
                  color: '#ff3b30',
                  fontSize: '0.82rem'
                }}>
                  ⏳ 冷静期倒计时: {Math.floor(circuitBreakerRemainingSec / 60)}分 {circuitBreakerRemainingSec % 60}秒
                </div>
              </div>
            </div>
            <div style={{ fontSize: '0.76rem', color: 'var(--text-main)', opacity: 0.9 }}>
              🛡️ <strong>安全保护已锁定</strong>：熔断触发后系统已锁定停机以保全剩余本金，彻底杜绝连续追单亏损。您可以点击上方【🔓 手动解除冷静期】后，重新评估行情或切换币种再次启动做市。
            </div>
          </div>
        )}

        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '8px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Shield size={18} style={{ color: '#0071e3' }} />
            <span style={{ fontSize: '0.92rem', fontWeight: '700' }}>🛡️ 机构级全天候最大回撤监控 & 硬熔断风控 (Max Drawdown & Circuit Breaker)</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
            <span>🌊 历史最高净值水位: <strong style={{ color: 'var(--text-main)' }}>${peakEquity.toFixed(2)} USD</strong></span>
            <span>🚨 熔断警戒红线: <strong style={{ color: '#ff9500' }}>{maxDrawdownLimitPct}%</strong></span>
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '0.8rem' }}>
          {/* Card 1: Current Drawdown */}
          <div style={{ background: 'rgba(255, 255, 255, 0.03)', padding: '8px 12px', borderRadius: '10px', border: '1px solid rgba(255,255,255,0.06)' }}>
            <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginBottom: '2px' }}>📉 实时当前回撤 (Current DD)</div>
            <div style={{ fontSize: '1.15rem', fontWeight: '800', color: currDrawdownPct > 5 ? '#ff3b30' : (currDrawdownPct > 2 ? '#ff9500' : '#34c759') }}>
              -{currDrawdownPct.toFixed(2)}% <span style={{ fontSize: '0.8rem', fontWeight: '500' }}>(-${currDrawdownUsd.toFixed(2)})</span>
            </div>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '2px' }}>
              {currDrawdownPct < 5 ? '🟢 回撤极小，安全运行' : (currDrawdownPct < 10 ? '🟡 正常波动回调' : '🔴 接近风控预警线')}
            </div>
          </div>

          {/* Card 2: Max Historical Drawdown */}
          <div style={{ background: 'rgba(255, 255, 255, 0.03)', padding: '8px 12px', borderRadius: '10px', border: '1px solid rgba(255,255,255,0.06)' }}>
            <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginBottom: '2px' }}>🛡️ 历史最大回撤 (Max DD)</div>
            <div style={{ fontSize: '1.15rem', fontWeight: '800', color: maxDrawdownPct > 8 ? '#ff9500' : '#34c759' }}>
              -{maxDrawdownPct.toFixed(2)}% <span style={{ fontSize: '0.8rem', fontWeight: '500' }}>(-${maxDrawdownUsd.toFixed(2)})</span>
            </div>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '2px' }}>
              峰值回撤保护控制优良
            </div>
          </div>

          {/* Card 3: Net PnL (Realized + Unrealized) */}
          <div style={{ background: 'rgba(255, 255, 255, 0.03)', padding: '8px 12px', borderRadius: '10px', border: '1px solid rgba(255,255,255,0.06)' }}>
            <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginBottom: '2px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span>💎 综合净收益 (Net PnL)</span>
              {drawdownStats?.live_equity_usd && (
                <span style={{ fontSize: '0.68rem', color: '#0071e3', background: 'rgba(0,113,227,0.12)', padding: '1px 5px', borderRadius: '4px' }}>
                  总净值: ${Number(drawdownStats.live_equity_usd).toFixed(2)}
                </span>
              )}
            </div>
            <div style={{ fontSize: '1.15rem', fontWeight: '800', color: netProfit >= 0 ? '#34c759' : '#ff3b30' }}>
              {netProfit >= 0 ? `+$${netProfit.toFixed(2)}` : `-$${Math.abs(netProfit).toFixed(2)}`} USD
            </div>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '2px', lineHeight: '1.3' }}>
              套利 +${(stats.grid_profit_usd || 0).toFixed(2)} | 调仓 {Number(stats.realized_position_pnl_usd || 0) >= 0 ? `+$${Number(stats.realized_position_pnl_usd || 0).toFixed(2)}` : `-$${Math.abs(Number(stats.realized_position_pnl_usd || 0)).toFixed(2)}`} | 浮动 {unrealizedPnl >= 0 ? `+$${unrealizedPnl.toFixed(2)}` : `-$${Math.abs(unrealizedPnl).toFixed(2)}`}
            </div>
          </div>

          {/* Card 4: Liquidation Safety Buffer */}
          <div style={{ background: 'rgba(255, 255, 255, 0.03)', padding: '8px 12px', borderRadius: '10px', border: '1px solid rgba(255,255,255,0.06)' }}>
            <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginBottom: '2px' }}>🧱 强平安全垫距离 (Buffer)</div>
            <div style={{ fontSize: '1.15rem', fontWeight: '800', color: liqDistance > 30 ? '#34c759' : (liqDistance > 15 ? '#ff9500' : '#ff3b30') }}>
              {liqDistance > 90 ? '> 90% 极充裕' : `${liqDistance.toFixed(1)}%`}
            </div>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '2px' }}>
              {liqDistance > 30 ? '🟢 爆仓风险极低' : '⚠️ 杠杆较高，注意仓位'}
            </div>
          </div>

          {/* Card 5: Auto-Compounding Snowball */}
          <div style={{ background: 'rgba(255, 255, 255, 0.03)', padding: '8px 12px', borderRadius: '10px', border: '1px solid rgba(255,255,255,0.06)' }}>
            <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginBottom: '2px' }}>❄️ 自动复利滚雪球 (Compound)</div>
            <div style={{ fontSize: '1.15rem', fontWeight: '800', color: compoundScaleRatio > 1.0 ? '#0071e3' : '#34c759' }}>
              {isAutoCompound ? `${compoundScaleRatio.toFixed(2)}x 放大` : '已停用'}
            </div>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '2px' }}>
              {compoundScaleRatio > 1.0 ? `利润驱动挂单翻 ${compoundScaleRatio.toFixed(2)} 倍` : '盈利后挂单自动等比放大'}
            </div>
          </div>

          {/* Card 6: Funding Rate & Carry Yield */}
          <div style={{ background: 'rgba(255, 255, 255, 0.03)', padding: '8px 12px', borderRadius: '10px', border: '1px solid rgba(255,255,255,0.06)' }}>
            <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginBottom: '2px' }}>🌐 8H 资金费率 & 套利</div>
            <div style={{ fontSize: '1.15rem', fontWeight: '800', color: frRate8h <= 0.02 ? '#34c759' : '#ff9500' }}>
              {frRate8h >= 0 ? `+${frRate8h.toFixed(4)}%` : `${frRate8h.toFixed(4)}%`} <span style={{ fontSize: '0.75rem', fontWeight: '600' }}>/ 8H</span>
            </div>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '2px' }}>
              年化费率: ~{frEstApr >= 0 ? `+${frEstApr.toFixed(1)}%` : `${frEstApr.toFixed(1)}%`}
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', gap: '14px', fontSize: '0.72rem', color: 'var(--text-muted)', flexWrap: 'wrap', borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: '6px' }}>
          <span>⚡ <strong>15M+1H+4H 敏捷动能雷达</strong> 30秒超高频巡检生效中（敏锐捕捉反转防踏空）</span>
          <span>🎯 <strong>宽幅自适应 ATR 通道</strong> 全距≥14%~20% 抗洗盘防假破位</span>
          <span>🛡️ <strong>Anti-Whipsaw</strong> 15s 破位持久度滤波已开启</span>
          <span>⚡ <strong>100% Post-Only (GTX)</strong> 最低费率保护</span>
        </div>
      </LiquidGlass>

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
              <Zap size={13} /> {recommendLoading ? '智能配置中...' : '✨ AI 智能配置全部参数'}
            </button>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.8rem' }}>
            
            {/* AI Multi-Timeframe Resonance Banner */}
            {aiRegimeNotice && (
              <div style={{
                background: mtfInfo?.tactical_reversal
                  ? 'linear-gradient(135deg, rgba(255, 59, 48, 0.15), rgba(255, 149, 0, 0.12))'
                  : 'linear-gradient(135deg, rgba(0, 113, 227, 0.12), rgba(52, 199, 89, 0.12))',
                border: mtfInfo?.tactical_reversal
                  ? '1px solid rgba(255, 59, 48, 0.4)'
                  : '1px solid rgba(0, 113, 227, 0.25)',
                borderRadius: '10px',
                padding: '8px 12px',
                fontSize: '0.8rem',
                color: 'var(--text-main)',
                display: 'flex',
                flexDirection: 'column',
                gap: '6px',
                boxShadow: mtfInfo?.tactical_reversal ? '0 0 12px rgba(255, 59, 48, 0.2)' : 'none'
              }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span style={{ fontSize: '1.1rem' }}>{mtfInfo?.tactical_reversal ? '⚡' : '🤖'}</span>
                    <div style={{ fontWeight: '700', color: mtfInfo?.tactical_reversal ? '#ff3b30' : '#0071e3' }}>
                      1D+4H+1H+15M 多周期多空共振 & 敏捷反转研判
                    </div>
                  </div>
                  {mtfInfo?.tactical_reversal && (
                    <span style={{
                      background: 'rgba(255, 59, 48, 0.2)',
                      color: '#ff3b30',
                      padding: '2px 8px',
                      borderRadius: '6px',
                      fontWeight: '800',
                      fontSize: '0.72rem'
                    }}>
                      🚨 趋势反转响应中
                    </span>
                  )}
                </div>
                <div style={{ fontSize: '0.78rem', color: 'var(--text-main)', fontWeight: '600' }}>{aiRegimeNotice}</div>
                {mtfInfo?.timeframes && (
                  <div style={{ display: 'flex', gap: '6px', fontSize: '0.7rem', flexWrap: 'wrap', marginTop: '2px' }}>
                    {mtfInfo.timeframes['15m']?.label && (
                      <span style={{ padding: '2px 6px', background: 'rgba(255,59,48,0.1)', color: '#ff3b30', borderRadius: '4px', fontWeight: '600' }}>
                        {mtfInfo.timeframes['15m'].label}
                      </span>
                    )}
                    <span style={{ padding: '2px 6px', background: 'rgba(255,149,0,0.1)', borderRadius: '4px' }}>{mtfInfo.timeframes['1h']?.label}</span>
                    <span style={{ padding: '2px 6px', background: 'rgba(52,199,89,0.1)', borderRadius: '4px' }}>{mtfInfo.timeframes['4h']?.label}</span>
                    <span style={{ padding: '2px 6px', background: 'rgba(0,113,227,0.1)', borderRadius: '4px' }}>{mtfInfo.timeframes['1d']?.label}</span>
                  </div>
                )}
              </div>
            )}

            {/* Top-Level Mode Selection Card (Live vs Paper) */}
            <div style={{
              padding: '12px 14px',
              borderRadius: '12px',
              background: isLive
                ? 'linear-gradient(135deg, rgba(255, 59, 48, 0.1) 0%, rgba(255, 149, 0, 0.05) 100%)'
                : 'linear-gradient(135deg, rgba(52, 199, 89, 0.1) 0%, rgba(0, 113, 227, 0.05) 100%)',
              border: `1.5px solid ${isLive ? 'rgba(255, 59, 48, 0.35)' : 'rgba(52, 199, 89, 0.35)'}`,
              display: 'flex',
              flexDirection: 'column',
              gap: '8px',
              boxShadow: isLive ? '0 2px 12px rgba(255, 59, 48, 0.12)' : '0 2px 12px rgba(52, 199, 89, 0.1)'
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <label style={{ fontSize: '0.82rem', fontWeight: '800', color: isLive ? '#d70015' : '#248a3d', margin: 0, display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <span>{isLive ? '🔴 实盘真实做市模式 (Live Mode)' : '🟢 本地模拟测算模式 (Paper Mode)'}</span>
                </label>
                {isRunning && (
                  <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>运行中锁定</span>
                )}
              </div>

              {/* Segmented Selector Buttons */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px' }}>
                <button
                  type="button"
                  onClick={() => handleSetIsLive(false)}
                  disabled={isRunning}
                  style={{
                    padding: '8px 10px',
                    borderRadius: '8px',
                    fontSize: '0.8rem',
                    fontWeight: !isLive ? '800' : '600',
                    background: !isLive ? '#34c759' : 'rgba(0,0,0,0.04)',
                    color: !isLive ? '#fff' : 'var(--text-muted)',
                    border: !isLive ? 'none' : '1px solid var(--border-color)',
                    cursor: isRunning ? 'not-allowed' : 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: '4px',
                    boxShadow: !isLive ? '0 2px 8px rgba(52, 199, 89, 0.3)' : 'none',
                    transition: 'all 0.15s ease'
                  }}
                >
                  🟢 本地模拟盘
                </button>
                <button
                  type="button"
                  onClick={() => {
                    if (!exchangeInfo?.is_configured) {
                      alert('⚠️ 尚未检测到配置的实盘 API Key，请先前往设置配置 Binance 凭证。');
                      return;
                    }
                    handleSetIsLive(true);
                  }}
                  disabled={isRunning}
                  style={{
                    padding: '8px 10px',
                    borderRadius: '8px',
                    fontSize: '0.8rem',
                    fontWeight: isLive ? '800' : '600',
                    background: isLive ? '#ff3b30' : 'rgba(0,0,0,0.04)',
                    color: isLive ? '#fff' : 'var(--text-muted)',
                    border: isLive ? 'none' : '1px solid var(--border-color)',
                    cursor: isRunning ? 'not-allowed' : 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: '4px',
                    boxShadow: isLive ? '0 2px 8px rgba(255, 59, 48, 0.35)' : 'none',
                    transition: 'all 0.15s ease'
                  }}
                >
                  🔴 币安真实实盘
                </button>
              </div>

              {/* Explanatory description & live status */}
              <div style={{ fontSize: '0.74rem', color: isLive ? '#b91c1c' : 'var(--text-muted)', lineHeight: '1.4' }}>
                {isLive ? (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
                    <div>⚠️ <strong>将向 Binance 交易所提交真实委托</strong>（100% Post-Only Maker 稳健挂单）</div>
                    <div style={{ color: 'var(--text-main)', fontSize: '0.72rem' }}>
                      实盘可用余额: <strong style={{ color: '#34c759' }}>${freeUsdtBal} USDT</strong>
                      <span style={{ color: 'var(--text-muted)', marginLeft: '6px' }}>(合约总资产: <strong>${totalUsdtBal} USDT</strong>)</span>
                      {!exchangeInfo?.is_configured && <span style={{ color: '#ff3b30', marginLeft: '6px' }}>⚠️ 未配置Key</span>}
                    </div>
                  </div>
                ) : (
                  <div>🧪 <strong>本地撮合模拟模式</strong>：不消耗真实资金，适合参数调优与策略验证。</div>
                )}
              </div>
            </div>

            {/* Symbol */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.35rem' }}>
                <label style={{ fontSize: '0.8rem', fontWeight: '600', color: 'var(--text-muted)', margin: 0 }}>
                  做市交易对 (Symbol)
                </label>
                <button
                  type="button"
                  onClick={() => handleOneClickDeploySelectedCoin(selectedSymbol)}
                  disabled={recommendLoading || isRunning}
                  style={{
                    background: 'linear-gradient(135deg, rgba(0, 113, 227, 0.12) 0%, rgba(52, 199, 89, 0.12) 100%)',
                    color: '#0071e3',
                    border: '1px solid rgba(0, 113, 227, 0.3)',
                    borderRadius: '6px',
                    padding: '3px 8px',
                    fontSize: '0.74rem',
                    fontWeight: '700',
                    cursor: isRunning ? 'not-allowed' : 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '4px',
                    opacity: isRunning ? 0.5 : 1
                  }}
                  title="您只负责选币，点击此按钮由 AI 自动配置全部参数并一键启动做市"
                >
                  <Sparkles size={12} style={{ color: '#34c759' }} />
                  ⚡ 仅选此币·AI 一键托管启动
                </button>
              </div>
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
              <span>等差单格间距：<strong>${formatPrecisionStr(previewSpacing, 6)}</strong></span>
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

            {/* Out of Range Action & Max Drawdown Circuit Breaker */}
            <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: '0.6rem' }}>
              <div>
                <label style={{ fontSize: '0.8rem', fontWeight: '600', color: 'var(--text-muted)', marginBottom: '0.25rem', display: 'block' }}>
                  破位出区间保护动作
                </label>
                <select
                  value={outOfRangeAction}
                  onChange={e => setOutOfRangeAction(e.target.value)}
                  disabled={isRunning}
                  style={{ width: '100%', padding: '0.5rem', borderRadius: '8px', border: '1px solid var(--border-color)', background: 'var(--card-bg)', fontSize: '0.8rem' }}
                >
                  <option value="auto_shift">🤖 动态移网追踪 (Auto-Shift)</option>
                  <option value="close">🛑 破位止损平仓 (Stop & Close)</option>
                  <option value="recover">🛡️ 只减仓阶梯 (Recover)</option>
                </select>
              </div>
              <div>
                <label style={{ fontSize: '0.8rem', fontWeight: '600', color: 'var(--text-muted)', marginBottom: '0.25rem', display: 'block' }}>
                  🚨 熔断回撤阈值 (%)
                </label>
                <input
                  type="number"
                  min="3"
                  max="50"
                  step="1"
                  value={maxDrawdownLimit}
                  onChange={e => setMaxDrawdownLimit(e.target.value)}
                  disabled={isRunning}
                  placeholder="15"
                  style={{ width: '100%', padding: '0.5rem', borderRadius: '8px', border: '1px solid var(--border-color)', background: 'var(--card-bg)', fontSize: '0.8rem' }}
                />
              </div>
            </div>

            {/* Auto-Compounding Snowball Toggle */}
            <div style={{
              padding: '8px 12px',
              borderRadius: '10px',
              background: autoCompound ? 'rgba(0, 113, 227, 0.08)' : 'rgba(255, 255, 255, 0.03)',
              border: `1px solid ${autoCompound ? 'rgba(0, 113, 227, 0.25)' : 'rgba(255, 255, 255, 0.08)'}`,
              marginTop: '0.2rem'
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <input
                  type="checkbox"
                  id="autoCompoundToggle"
                  checked={autoCompound}
                  onChange={e => setAutoCompound(e.target.checked)}
                  disabled={isRunning}
                  style={{ width: '15px', height: '15px', cursor: isRunning ? 'not-allowed' : 'pointer' }}
                />
                <label htmlFor="autoCompoundToggle" style={{ fontSize: '0.82rem', fontWeight: '700', color: autoCompound ? '#0071e3' : 'var(--text-muted)', cursor: isRunning ? 'not-allowed' : 'pointer' }}>
                  ❄️ 开启自动复利滚雪球模式 (Auto-Compounding)
                </label>
              </div>
              <p style={{ fontSize: '0.72rem', color: 'var(--text-muted)', margin: '3px 0 0 22px' }}>
                已赚网格利润将自动滚入本金，自适应等比放大后续挂单量，实现指数级加速收益。
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
                    padding: '0.85rem 1rem',
                    background: isLive
                      ? 'linear-gradient(135deg, #ff3b30 0%, #ff9500 100%)'
                      : 'linear-gradient(135deg, #0071e3 0%, #34c759 100%)',
                    fontWeight: '800',
                    fontSize: '0.95rem',
                    borderRadius: '10px',
                    boxShadow: isLive ? '0 4px 14px rgba(255, 59, 48, 0.35)' : '0 4px 14px rgba(0, 113, 227, 0.3)',
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: '8px'
                  }}
                >
                  <Play size={17} fill="#fff" />
                  {isLive ? '🚀 启动币安实盘做市 (提交真实挂单)' : '🧪 启动本地模拟做市 (Paper)'}
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
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <h3 style={{ fontSize: '0.95rem', fontWeight: '700', margin: 0, display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                  <Layers size={16} style={{ color: '#0071e3' }} /> 当前盘口活动挂单 ({activeOrders.length} 笔)
                </h3>
                {isRunning && (
                  gridState?.config?.is_live ? (
                    <span style={{ fontSize: '0.72rem', padding: '2px 8px', borderRadius: '10px', background: 'rgba(255, 59, 48, 0.15)', color: '#d70015', fontWeight: '700', border: '1px solid rgba(255, 59, 48, 0.3)' }}>
                      🔴 币安实盘真实挂单
                    </span>
                  ) : (
                    <span style={{ fontSize: '0.72rem', padding: '2px 8px', borderRadius: '10px', background: 'rgba(52, 199, 89, 0.15)', color: '#248a3d', fontWeight: '700', border: '1px solid rgba(52, 199, 89, 0.3)' }}>
                      🟢 本地模拟测算
                    </span>
                  )
                )}
              </div>
              <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                100% Maker Post-Only
              </span>
            </div>

            {/* Mode Explanatory Banner when running */}
            {isRunning && !gridState?.config?.is_live && (
              <div style={{
                fontSize: '0.75rem',
                padding: '6px 12px',
                borderRadius: '8px',
                background: 'rgba(0, 113, 227, 0.06)',
                border: '1px solid rgba(0, 113, 227, 0.18)',
                color: '#0071e3',
                marginBottom: '0.6rem',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between'
              }}>
                <span>💡 当前处于<strong>【本地模拟测算模式】</strong>，未向交易所提交真实资金挂单。如需在币安真实做市，请先停止当前网格，在顶部切换为【🔴 实盘真实做市】即可！</span>
              </div>
            )}
            {isRunning && gridState?.config?.is_live && (
              <div style={{
                fontSize: '0.75rem',
                padding: '6px 12px',
                borderRadius: '8px',
                background: 'rgba(255, 59, 48, 0.06)',
                border: '1px solid rgba(255, 59, 48, 0.2)',
                color: '#d70015',
                marginBottom: '0.6rem',
                display: 'flex',
                alignItems: 'center',
                gap: '6px'
              }}>
                <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: '#ff3b30' }} />
                <span>🔴 实盘挂单中：所有挂单已由 CCXT 实时同步至 Binance 永续合约深度盘口，享受 Maker 负费率/极低手续费。</span>
              </div>
            )}

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
                            <span style={{ fontSize: '0.7rem', padding: '2px 7px', borderRadius: '4px', background: 'rgba(255, 59, 48, 0.12)', color: '#d70015', fontWeight: '700', border: '1px solid rgba(255, 59, 48, 0.25)' }}>
                              🔴 币安已挂单 (#{ord.live_order_id.slice(-6)})
                            </span>
                          ) : isLive ? (
                            <span style={{ fontSize: '0.7rem', padding: '2px 7px', borderRadius: '4px', background: 'rgba(255, 149, 0, 0.12)', color: '#ff9500', fontWeight: '600', border: '1px solid rgba(255, 149, 0, 0.25)' }}>
                              {ord.status === 'pending_retry' ? '⏳ 补单重试中' : '⚠️ 实盘待报单'}
                            </span>
                          ) : (
                            <span style={{ fontSize: '0.7rem', padding: '2px 7px', borderRadius: '4px', background: 'rgba(142,142,147,0.12)', color: '#8e8e93', fontWeight: '600' }}>
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
                      <th style={{ textAlign: 'right', padding: '6px' }}>手续费</th>
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
                        <td style={{ padding: '6px', textAlign: 'right', color: 'var(--text-muted)', fontSize: '0.75rem' }}>
                          {f.fee_usd !== undefined ? `-$${Number(f.fee_usd).toFixed(4)}` : '--'}
                        </td>
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
