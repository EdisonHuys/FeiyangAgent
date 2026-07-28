import React, { useState, useEffect, useRef } from 'react';
import { AreaChart, Settings, Play, ShieldAlert, CheckCircle2, TrendingUp, HelpCircle, Target, History, Brain } from 'lucide-react';
import KLineChart from './components/KLineChart';
import SettingsPanel from './components/SettingsPanel';
import SniperDashboard from './components/SniperDashboard';
import BacktestPanel from './components/BacktestPanel';
import PromptEditorPanel from './components/PromptEditorPanel';
import SentimentPanel from './components/SentimentPanel';

const API_BASE = window.location.origin.includes('5173') ? 'http://127.0.0.1:8000' : window.location.origin;

export default function App() {
  const [activeTab, setActiveTab] = useState('terminal'); // 'terminal', 'sniper', 'backtest', or 'settings'
  const [activeSymbol, setActiveSymbol] = useState('BTC/USDT');
  const [selectedTimeframe, setSelectedTimeframe] = useState('4h');
  
  // Market data & charts
  const [marketData, setMarketData] = useState(null);
  const [chartData, setChartData] = useState([]);
  const [marketLoading, setMarketLoading] = useState(false);
  const [marketError, setMarketError] = useState(null);

  // Diagnostics & predictions
  const [diagLoading, setDiagLoading] = useState(false);
  const [prediction, setPrediction] = useState(null);
  const [diagError, setDiagError] = useState(null);

  const [symbolsList, setSymbolsList] = useState(['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'ZEC/USDT']);

  // Logs terminal
  const [monitorLogs, setMonitorLogs] = useState([]);
  const [hasNewUnreadLogs, setHasNewUnreadLogs] = useState(false);
  const logConsoleRef = React.useRef(null);

  // 0. Splash Screen loading animation
  const [showSplash, setShowSplash] = useState(true);
  const [splashFade, setSplashFade] = useState(false);
  const [splashProgress, setSplashProgress] = useState(0);
  const [splashStatus, setSplashStatus] = useState("Initializing Core...");

  useEffect(() => {
    const statuses = [
      { progress: 15, text: "Initializing Terminal Core..." },
      { progress: 38, text: "Establishing secure WebSocket tunnels..." },
      { progress: 55, text: "Synchronizing exchange telemetry..." },
      { progress: 78, text: "Computing multi-timeframe indicators..." },
      { progress: 92, text: "Optimizing AI consensus engines..." },
      { progress: 100, text: "System fully synchronized. Welcome." }
    ];

    let currentStep = 0;
    const interval = setInterval(() => {
      setSplashProgress(prev => {
        const target = statuses[currentStep].progress;
        if (prev < target) {
          const next = prev + Math.floor(Math.random() * 4) + 1;
          return next >= target ? target : next;
        } else {
          if (currentStep < statuses.length - 1) {
            currentStep++;
            setSplashStatus(statuses[currentStep].text);
          } else {
            clearInterval(interval);
            setTimeout(() => {
              setSplashFade(true);
              setTimeout(() => {
                setShowSplash(false);
              }, 800);
            }, 600);
          }
          return prev;
        }
      });
    }, 30);

    return () => clearInterval(interval);
  }, []);

  // 1. Fetch config and default symbol on mount (and whenever tab changes to pick up settings updates)
  useEffect(() => {
    fetch(`${API_BASE}/api/config`)
      .then(res => res.json())
      .then(data => {
        if (data.symbol) {
          setActiveSymbol(data.symbol);
        }
        if (data.symbols) {
          setSymbolsList(data.symbols);
        }
      })
      .catch(err => console.error("Error loading config:", err));
  }, [activeTab]);

  // 2. Fetch K-line data with 60s auto-refresh (silent updates to prevent loading spinner disruptions)
  useEffect(() => {
    let active = true;
    
    const fetchMarketData = (isSilent = false) => {
      if (!isSilent) {
        setMarketLoading(true);
      }
      setMarketError(null);
      
      fetch(`${API_BASE}/api/market?symbol=${encodeURIComponent(activeSymbol)}`)
        .then(res => {
          if (!res.ok) throw new Error("无法拉取该交易对的市场行情，请检查格式或网络。");
          return res.json();
        })
        .then(data => {
          if (!active) return;
          setMarketData(data);
          const tfKey = mapTimeframeKey(selectedTimeframe);
          setChartData(data.charts[tfKey] || []);
          if (!isSilent) {
            setMarketLoading(false);
          }
        })
        .catch(err => {
          if (!active) return;
          console.error(err);
          if (!isSilent) {
            setMarketError(err.message);
            setMarketLoading(false);
          }
        });
    };

    fetchMarketData(false);

    // Poll every 60s (extremely safe Binance request window)
    const intervalId = setInterval(() => {
      fetchMarketData(true);
    }, 60000);

    return () => {
      active = false;
      clearInterval(intervalId);
    };
  }, [activeSymbol]);

  // Fetch latest background scan report for the active symbol
  useEffect(() => {
    let active = true;
    const fetchLatestReport = () => {
      fetch(`${API_BASE}/api/reports/latest?symbol=${encodeURIComponent(activeSymbol)}`)
        .then(res => res.json())
        .then(data => {
          if (!active) return;
          if (data && data.report) {
            setPrediction({
              signal: data.signal,
              report: data.report
            });
          } else {
            setPrediction(null);
          }
        })
        .catch(err => {
          console.error("Error fetching latest report:", err);
        });
    };

    fetchLatestReport();
    
    // Poll every 15s for background scan updates
    const intervalId = setInterval(fetchLatestReport, 15000);

    return () => {
      active = false;
      clearInterval(intervalId);
    };
  }, [activeSymbol]);

  // 3. Update ChartData whenever selectedTimeframe changes
  useEffect(() => {
    if (marketData) {
      const tfKey = mapTimeframeKey(selectedTimeframe);
      setChartData(marketData.charts[tfKey] || []);
    }
  }, [selectedTimeframe, marketData]);

  // 4. Fetch background monitoring logs with 5s polling loop
  useEffect(() => {
    const fetchLogs = () => {
      fetch(`${API_BASE}/api/monitor-logs`)
        .then(res => res.json())
        .then(data => {
          if (data.logs) {
            setMonitorLogs(data.logs);
          }
        })
        .catch(err => console.error("Error fetching monitor logs:", err));
    };

    fetchLogs();
    const interval = setInterval(fetchLogs, 5000);
    return () => clearInterval(interval);
  }, []);

  const scrollToLogBottom = () => {
    if (logConsoleRef.current) {
      logConsoleRef.current.scrollTop = logConsoleRef.current.scrollHeight;
      setHasNewUnreadLogs(false);
    }
  };

  const handleLogScroll = () => {
    if (logConsoleRef.current) {
      const { scrollTop, scrollHeight, clientHeight } = logConsoleRef.current;
      const distanceFromBottom = scrollHeight - scrollTop - clientHeight;
      if (distanceFromBottom <= 40) {
        setHasNewUnreadLogs(false);
      }
    }
  };

  // 5. Scroll log console to bottom automatically if user is near bottom
  useEffect(() => {
    if (logConsoleRef.current) {
      const { scrollTop, scrollHeight, clientHeight } = logConsoleRef.current;
      const distanceFromBottom = scrollHeight - scrollTop - clientHeight;
      if (distanceFromBottom <= 40) {
        logConsoleRef.current.scrollTop = logConsoleRef.current.scrollHeight;
        setHasNewUnreadLogs(false);
      } else {
        setHasNewUnreadLogs(true);
      }
    }
  }, [monitorLogs]);

  // 6. Cleanup diagnosis polling on unmount
  useEffect(() => {
    return () => {
      if (diagPollRef.current) {
        clearInterval(diagPollRef.current);
      }
    };
  }, []);

  const handleClearLogs = () => {
    setMonitorLogs([]);
    fetch(`${API_BASE}/api/monitor-logs/clear`, { method: 'POST' })
      .then(res => res.json())
      .then(data => {
        if (data.status === 'success') {
          setMonitorLogs([]);
        }
      })
      .catch(err => console.error("Error clearing logs:", err));
  };

  const mapTimeframeKey = (tf) => {
    if (tf === '1h') return '1h';
    if (tf === '4h') return '4h';
    if (tf === '1D') return '1D';
    if (tf === '1W') return '1W';
    if (tf === '1M') return '1M';
    return '4h';
  };

  // Run LLM diagnostics
  const diagPollRef = useRef(null);

  const handleRunDiagnostics = () => {
    setDiagLoading(true);
    setDiagError(null);
    setPrediction(null);

    // Stop any previous polling
    if (diagPollRef.current) {
      clearInterval(diagPollRef.current);
      diagPollRef.current = null;
    }

    fetch(`${API_BASE}/api/analyze`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol: activeSymbol }),
    })
      .then(res => {
        if (!res.ok) {
          return res.json().then(errData => {
            throw new Error(errData.detail || "LLM 诊断请求失败。");
          });
        }
        return res.json();
      })
      .then(data => {
        const taskId = data.task_id;
        if (!taskId) throw new Error("服务器未返回任务 ID");

        // Poll for completion every 2 seconds
        diagPollRef.current = setInterval(() => {
          fetch(`${API_BASE}/api/analyze/status/${taskId}`)
            .then(res => {
              if (!res.ok) {
                return res.json().then(errData => {
                  throw new Error(errData.detail || "LLM 诊断失败。");
                });
              }
              return res.json();
            })
            .then(result => {
              if (result.status === 'processing') return; // still running
              // Done or error (error throws above via !res.ok)
              clearInterval(diagPollRef.current);
              diagPollRef.current = null;
              setPrediction(result);
              setDiagLoading(false);
            })
            .catch(err => {
              clearInterval(diagPollRef.current);
              diagPollRef.current = null;
              console.error(err);
              setDiagError(err.message);
              setDiagLoading(false);
            });
        }, 2000);
      })
      .catch(err => {
        console.error(err);
        setDiagError(err.message);
        setDiagLoading(false);
      });
  };

  // Helper to parse simple markdown to html
  const renderMarkdown = (text) => {
    if (!text) return null;
    
    // Split into lines
    const lines = text.split('\n');
    let inList = false;
    const elements = [];

    lines.forEach((line, idx) => {
      let trimmed = line.trim();
      
      // Handle list tags
      if (trimmed.startsWith('*') || trimmed.startsWith('-')) {
        if (!inList) {
          inList = true;
        }
        const content = trimmed.substring(1).trim();
        elements.push(
          <li key={`li-${idx}`} dangerouslySetInnerHTML={{ __html: parseInlineStyles(content) }} />
        );
        return;
      } else {
        if (inList) {
          inList = false;
          // Wrap previous list items if we wanted strict markup, but standard flex list is fine
        }
      }

      // Handle Headers
      if (trimmed.startsWith('###')) {
        elements.push(
          <h3 key={`h3-${idx}`} dangerouslySetInnerHTML={{ __html: parseInlineStyles(trimmed.substring(3).trim()) }} />
        );
      } else if (trimmed.startsWith('**') && trimmed.endsWith('**')) {
        elements.push(
          <p key={`p-${idx}`} style={{ fontWeight: 'bold', margin: '0.5rem 0' }}>
            {trimmed.replace(/\*\*/g, '')}
          </p>
        );
      } else if (trimmed) {
        elements.push(
          <p key={`p-${idx}`} dangerouslySetInnerHTML={{ __html: parseInlineStyles(trimmed) }} />
        );
      } else {
        elements.push(<div key={`br-${idx}`} style={{ height: '0.5rem' }} />);
      }
    });

    return <div className="report-content">{elements}</div>;
  };

  const parseInlineStyles = (content) => {
    // Bold: **text** -> <strong>text</strong>
    let parsed = content.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    // Highlight codes: $123 -> <span class="highlight">$123</span>
    parsed = parsed.replace(/(\$\d+(\.\d+)?)/g, '<span style="color: var(--text-bright); font-weight: bold;">$1</span>');
    return parsed;
  };

  return (
    <>
      {showSplash && (
        <div className={`splash-overlay ${splashFade ? 'fade-out' : ''}`}>
          {/* Tactical Scoping Grid Background */}
          <div className="splash-grid-bg"></div>
          
          {/* Lock-on Laser Scan Line */}
          <div className="splash-laser-line"></div>

          {/* Matrix Crypto Price Streams (Parallax Tickers) */}
          <div className="splash-ticker-stream stream-1">
            {Array.from({ length: 15 }).map((_, i) => (
              <div key={i} className="stream-item long">BTC/USDT 65,449.60 ▲ +1.24%</div>
            ))}
          </div>
          <div className="splash-ticker-stream stream-2">
            {Array.from({ length: 15 }).map((_, i) => (
              <div key={i} className="stream-item short">ETH/USDT 1,969.44 ▼ -0.85%</div>
            ))}
          </div>
          <div className="splash-ticker-stream stream-3">
            {Array.from({ length: 15 }).map((_, i) => (
              <div key={i} className="stream-item long">SOL/USDT 184.22 ▲ +5.41%</div>
            ))}
          </div>
          <div className="splash-ticker-stream stream-4">
            {Array.from({ length: 15 }).map((_, i) => (
              <div key={i} className="stream-item short">DOGE/USDT 0.07312 ▼ -2.12%</div>
            ))}
          </div>

          {/* Floating Telemetry Ticker Panels */}
          <div className="splash-telemetry left-panel">
            <div className="telemetry-header">📡 TELEMETRY SYSTEM ACTIVE</div>
            <div className="telemetry-row">LOC: 31.2304° N, 121.4737° E</div>
            <div className="telemetry-row">NET: 8ms (EXPRESS VIP)</div>
            <div className="telemetry-row">CCXT: V5.1 (ACTIVE)</div>
            <div className="telemetry-row">COGNITIVE: DUAL-LLM CONSENSUS</div>
            <div className="telemetry-row">BIAS: BULLISH (CONVERGENCE)</div>
          </div>
          <div className="splash-telemetry right-panel">
            <div className="telemetry-header">🔥 REALTIME EXECUTIONS</div>
            <div className="telemetry-row">ZEC/USDT: 504.72 ▲ (PENDING)</div>
            <div className="telemetry-row">HYPE/USDT: 60.176 ▲ (FILLED)</div>
            <div className="telemetry-row">DOGE/USDT: 0.0731 ▼ (PENDING)</div>
            <div className="telemetry-row">BTC/USDT: 65,817.75 ▼ (STANDBY)</div>
            <div className="telemetry-row">ZAMA/USDT: 0.0549 ▲ (MONITORING)</div>
          </div>

          <div className="splash-sniper-scope">
            {/* Scoping crosshair rings */}
            <div className="scope-ring ring-outer"></div>
            <div className="scope-ring ring-middle"></div>
            
            {/* Radar sweep beam */}
            <div className="scope-radar-beam"></div>

            <div className="scope-ring ring-inner">
              {/* Target lock indicators */}
              <div className="target-bracket corner-tl"></div>
              <div className="target-bracket corner-tr"></div>
              <div className="target-bracket corner-bl"></div>
              <div className="target-bracket corner-br"></div>
              
              {/* Core lock data */}
              <div className="scope-core">
                <Target className="scope-icon" size={32} />
                <div className="scope-price">{splashProgress === 100 ? "ACQUIRED" : "TARGETING..."}</div>
                <div className="scope-ticker">{splashStatus}</div>
              </div>
            </div>
            
            {/* Crosshair lines */}
            <div className="scope-line axis-h"></div>
            <div className="scope-line axis-v"></div>
            
            {/* Scoping ticks */}
            <div className="scope-tick tick-top">90°</div>
            <div className="scope-tick tick-right">180°</div>
            <div className="scope-tick tick-bottom">270°</div>
            <div className="scope-tick tick-left">0°</div>
          </div>

          {/* Locked-on flash impact wave */}
          {splashProgress === 100 && <div className="splash-lock-shockwave"></div>}

          {/* Bottom status indicator */}
          <div className="splash-bottom-loader">
            <div className="progress-label">SNIPER SYSTEM SYNCHRONIZING: {splashProgress}%</div>
            <div className="splash-progress-track">
              <div className="splash-progress-fill" style={{ width: `${splashProgress}%` }}></div>
            </div>
          </div>
        </div>
      )}

      <div className="app-container">
      {/* Liquid animated glass blobs background */}
      <div className="liquid-bg">
        <div className="blob blob-gold"></div>
        <div className="blob blob-orange"></div>
        <div className="blob blob-indigo"></div>
        <div className="blob blob-purple"></div>
      </div>

      {/* Navbar */}
      <header className="navbar">
        <div className="brand-section">
          <TrendingUp size={24} style={{ color: 'var(--color-long)' }} />
          <h1 className="brand-logo">Feiyang.AI</h1>
          <span className="badge-tag">防御型右侧交易智能体</span>
        </div>
        
        <div className="nav-links">
          <button 
            className={`btn btn-secondary ${activeTab === 'terminal' ? 'active' : ''}`}
            onClick={() => setActiveTab('terminal')}
          >
            <AreaChart size={16} />
            <span>交易诊断终端</span>
          </button>
          <button
            className={`btn btn-secondary ${activeTab === 'sniper' ? 'active' : ''}`}
            onClick={() => setActiveTab('sniper')}
          >
            <Target size={16} className="text-cyan-400" />
            <span>🎯 智能狙击控制台</span>
          </button>
          <button
            className={`btn btn-secondary ${activeTab === 'backtest' ? 'active' : ''}`}
            onClick={() => setActiveTab('backtest')}
          >
            <History size={16} />
            <span>📈 历史回测</span>
          </button>
          <button
            className={`btn btn-secondary ${activeTab === 'prompt' ? 'active' : ''}`}
            onClick={() => setActiveTab('prompt')}
          >
            <Brain size={16} style={{ color: '#b25000' }} />
            <span>🧠 策略 Prompt</span>
          </button>
          <button
            className={`btn btn-secondary ${activeTab === 'settings' ? 'active' : ''}`}
            onClick={() => setActiveTab('settings')}
          >
            <Settings size={16} />
            <span>核心配置参数</span>
          </button>
        </div>
      </header>

      {/* Main Container */}
      <main style={{ flex: 1, minHeight: 0 }}>
        {activeTab === 'prompt' ? (
          <div style={{ padding: '1.5rem', height: '100%', overflowY: 'auto' }}>
            <PromptEditorPanel apiBase={API_BASE} standalone={true} />
          </div>
        ) : activeTab === 'settings' ? (
          <div style={{ padding: '1.5rem', height: '100%' }}>
            <SettingsPanel apiBase={API_BASE} />
          </div>
        ) : activeTab === 'sniper' ? (
          <div style={{ padding: '1.5rem', height: '100%', overflowY: 'auto' }}>
            <SniperDashboard apiBase={API_BASE} />
          </div>
        ) : activeTab === 'backtest' ? (
          <div style={{ padding: '1.5rem', height: '100%', overflowY: 'auto' }}>
            <BacktestPanel apiBase={API_BASE} symbols={symbolsList} />
          </div>
        ) : (
          <div className="dashboard-grid">
            
            {/* Left Side: Chart and Timeframe */}
            <section className="column-left">
              <div className="panel chart-panel">
                <div className="panel-header">
                  <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
                    <div className="panel-title">
                      <span className="pulse-indicator"></span>
                      <span>K线量化技术视图</span>
                    </div>
                    {/* Active pair select */}
                    <select 
                      value={activeSymbol} 
                      onChange={(e) => setActiveSymbol(e.target.value)}
                      className="form-control"
                      style={{ padding: '0.3rem 0.6rem', width: '140px', margin: 0, height: 'auto' }}
                    >
                      {symbolsList.map(sym => (
                        <option key={sym} value={sym}>{sym}</option>
                      ))}
                    </select>
                  </div>

                  <div className="timeframe-selector">
                    {['1h', '4h', '1D', '1W', '1M'].map(tf => (
                      <button 
                        key={tf} 
                        className={`btn btn-secondary ${selectedTimeframe === tf ? 'active' : ''}`}
                        style={{ padding: '0.3rem 0.75rem', fontSize: '0.8rem' }}
                        onClick={() => setSelectedTimeframe(tf)}
                      >
                        {tf}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="chart-container-div">
                  {marketLoading ? (
                    <div className="loader-wrapper">
                      <div className="spinner"></div>
                      <p>正在获取 Binance 实时K线数据并计算指标...</p>
                    </div>
                  ) : marketError ? (
                    <div className="loader-wrapper" style={{ color: 'var(--color-short)' }}>
                      <ShieldAlert size={40} />
                      <p>{marketError}</p>
                    </div>
                  ) : (
                    <KLineChart key={`${activeSymbol}_${selectedTimeframe}`} data={chartData} />
                  )}
                </div>

                {/* 24H Monitor logs panel */}
                <div 
                  className="monitor-logs-panel"
                  style={{
                    borderTop: '1px solid rgba(0,0,0,0.06)',
                    paddingTop: '0.75rem',
                    marginTop: '0.75rem',
                    height: '160px',
                    display: 'flex',
                    flexDirection: 'column',
                    minHeight: '160px',
                    position: 'relative'
                  }}
                >
                  <div 
                    style={{ 
                      display: 'flex', 
                      justifyContent: 'space-between', 
                      alignItems: 'center', 
                      marginBottom: '0.4rem' 
                    }}
                  >
                    <div 
                      style={{ 
                        fontSize: '0.85rem', 
                        fontWeight: 'bold', 
                        color: 'var(--text-bright)', 
                        display: 'flex', 
                        alignItems: 'center', 
                        gap: '0.4rem' 
                      }}
                    >
                      <span 
                        style={{ 
                          width: '6px', 
                          height: '6px', 
                          background: 'var(--color-long)', 
                          borderRadius: '50%', 
                          animation: '1.6s infinite pulse' 
                        }}
                      ></span>
                      <span>24H 盯盘运行日志</span>
                    </div>
                    <button 
                      onClick={handleClearLogs}
                      className="btn btn-secondary"
                      style={{ 
                        padding: '0.2rem 0.5rem', 
                        fontSize: '0.75rem', 
                        borderRadius: '4px',
                        height: 'auto',
                        minHeight: 'unset',
                        border: '1px solid rgba(0,0,0,0.08)'
                      }}
                    >
                      清空日志
                    </button>
                  </div>
                  <div 
                    ref={logConsoleRef}
                    onScroll={handleLogScroll}
                    style={{
                      flex: 1,
                      background: 'rgba(0,0,0,0.03)',
                      border: '1px solid rgba(0,0,0,0.05)',
                      borderRadius: '6px',
                      padding: '0.5rem 0.75rem',
                      overflowY: 'auto',
                      fontFamily: 'Consolas, Monaco, monospace',
                      fontSize: '0.8rem',
                      lineHeight: '1.4',
                      color: '#248a3d',
                    }}
                  >
                    {monitorLogs.map((log, idx) => {
                      let color = '#248a3d';
                      if (log.includes('❌') || log.includes('失败')) color = 'var(--color-short)';
                      if (log.includes('⚡') || log.includes('手动')) color = '#007aff';
                      if (log.includes('🔄') || log.includes('启动')) color = '#b25000';
                      if (log.includes('✅') || log.includes('成功')) color = 'var(--color-long)';
                      if (log.includes('😴') || log.includes('休眠')) color = 'var(--text-muted)';
                      
                      return (
                        <div key={idx} style={{ color, marginBottom: '0.2rem', wordBreak: 'break-all' }}>
                          {log}
                        </div>
                      );
                    })}
                    {monitorLogs.length === 0 && (
                      <div style={{ color: 'var(--text-muted)', fontStyle: 'italic' }}>
                        暂无盯盘运行日志，后台盯盘任务启动中...
                      </div>
                    )}
                  </div>
                  {hasNewUnreadLogs && (
                    <button
                      type="button"
                      onClick={scrollToLogBottom}
                      style={{
                        position: 'absolute',
                        bottom: '12px',
                        right: '16px',
                        background: '#007aff',
                        color: '#ffffff',
                        border: 'none',
                        borderRadius: '16px',
                        padding: '4px 12px',
                        fontSize: '0.72rem',
                        fontWeight: 'bold',
                        cursor: 'pointer',
                        boxShadow: '0 2px 8px rgba(0,0,0,0.25)',
                        zIndex: 10,
                        display: 'flex',
                        alignItems: 'center',
                        gap: '4px',
                        transition: 'all 0.2s ease'
                      }}
                    >
                      👇 收到新日志 (点击置底)
                    </button>
                  )}
                </div>
              </div>
            </section>

            {/* Right Side: Prediction & Feiyang Console */}
            <section className="column-right">
              {/* Diagnose Trigger Panel */}
              <div className="panel" style={{ flexShrink: 0 }}>
                <div className="panel-header" style={{ marginBottom: '0.5rem', borderBottom: 'none', paddingBottom: 0, display: 'flex', justifyContent: 'space-between', alignItems: 'center', lineHeight: 'normal' }}>
                  <div className="panel-title" style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', lineHeight: 'normal' }}>
                    <span style={{ fontSize: '1.1rem', display: 'inline-flex', alignItems: 'center' }}>💡</span>
                    <span style={{ display: 'inline-flex', alignItems: 'center' }}>飞扬交易决策台</span>
                  </div>
                  <button
                    type="button"
                    className="btn btn-secondary"
                    style={{ fontSize: '0.75rem', padding: '0.25rem 0.6rem', display: 'inline-flex', alignItems: 'center', gap: '0.3rem', lineHeight: 'normal' }}
                    onClick={() => setActiveTab('prompt')}
                    title="自定义与调校 Prompt 人设规则"
                  >
                    <Brain size={13} style={{ color: '#b25000' }} />
                    <span>策略 Prompt</span>
                  </button>
                </div>
                <p style={{ fontSize: '0.82rem', color: 'var(--text-secondary)', marginBottom: '1rem', lineHeight: '1.5' }}>
                  模拟大局观（多周期共振、阻力验证、拒绝追涨），提取精炼指标由 LLM 分析产生右侧防守买卖信号。
                </p>
                <button 
                  onClick={handleRunDiagnostics} 
                  disabled={marketLoading || diagLoading}
                  className="btn btn-primary"
                  style={{ width: '100%', padding: '0.8rem' }}
                >
                  {diagLoading ? (
                    <>
                      <LoaderIcon />
                      <span>飞扬正在分析盘面结构...</span>
                    </>
                  ) : (
                    <>
                      <Play size={16} />
                      <span>开启飞扬流派行情诊断</span>
                    </>
                  )}
                </button>
              </div>

              {/* Market Sentiment Panel */}
              <SentimentPanel apiBase={API_BASE} />

              {/* Diagnostic outputs */}
              <div className="panel" style={{ flex: 1, minHeight: '350px', overflow: 'hidden' }}>
                <div className="panel-header">
                  <div className="panel-title">
                    <span>📊 诊断结果与警报</span>
                  </div>
                </div>

                {diagLoading ? (
                  <div className="loader-wrapper">
                    <div className="spinner" style={{ borderTopColor: 'var(--color-long)' }}></div>
                    <p style={{ fontSize: '0.9rem', color: 'var(--text-bright)' }}>🧠 飞扬正在审查多周期共振点位...</p>
                    <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>正在对EMA55与斐波那契进行缺口诊断与风控过滤</span>
                  </div>
                ) : diagError ? (
                  <div className="loader-wrapper" style={{ color: 'var(--color-short)' }}>
                    <ShieldAlert size={36} />
                    <p>{diagError}</p>
                    <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>请前往“核心配置参数”页面检查您的 API Key 与 Endpoint 设置</span>
                  </div>
                ) : prediction ? (
                  <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflowY: 'auto' }}>
                    {/* Signal Block */}
                    <div className={`signal-box ${prediction.signal.signal_type}`} style={{ flexShrink: 0 }}>
                      <div className="signal-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', lineHeight: 'normal' }}>
                        <div className={`signal-badge ${prediction.signal.signal_type}`} style={{ display: 'inline-flex', alignItems: 'center', gap: '0.4rem', lineHeight: 'normal' }}>
                          <span style={{ fontSize: '1.25rem', display: 'inline-flex', alignItems: 'center' }}>
                            {prediction.signal.signal_type === 'long' && '📈'}
                            {prediction.signal.signal_type === 'short' && '📉'}
                            {prediction.signal.signal_type === 'wait' && '⏳'}
                          </span>
                          <span style={{ display: 'inline-flex', alignItems: 'center' }}>
                            {prediction.signal.signal_type === 'long' && '建议买入 (LONG)'}
                            {prediction.signal.signal_type === 'short' && '建议做空 (SHORT)'}
                            {prediction.signal.signal_type === 'wait' && '建议观望 (WAIT)'}
                          </span>
                          {prediction.signal.signal_class && prediction.signal.signal_class !== 'wait' && (
                            <span style={{ fontSize: '0.75rem', marginLeft: '0.5rem', opacity: 0.8, display: 'inline-flex', alignItems: 'center' }}>
                              {prediction.signal.signal_class === 'pullback_long' && '埋伏低多'}
                              {prediction.signal.signal_class === 'pullback_short' && '埋伏高空'}
                              {prediction.signal.signal_class === 'breakout_long' && '合理追多'}
                              {prediction.signal.signal_class === 'breakout_short' && '合理追空'}
                            </span>
                          )}
                        </div>
                        <div style={{ fontSize: '0.82rem', color: 'var(--text-secondary)', display: 'inline-flex', alignItems: 'center', gap: '0.2rem', lineHeight: 'normal' }}>
                          <span>置信度评分:</span>
                          <span style={{ color: '#b25000', fontWeight: 'bold', fontSize: '1.05rem', display: 'inline-flex', alignItems: 'center' }}>{prediction.signal.confidence_score}</span>
                          <span>/ 12</span>
                        </div>
                      </div>
                      
                      {prediction.signal.signal_type !== 'wait' ? (
                        <div className="parameters-grid">
                          <div className="parameter-card">
                            <div className="param-label">合理吃单区间</div>
                            <div className="param-value" style={{ fontSize: '0.95rem' }}>
                              ${prediction.signal.entry_zone.min} - ${prediction.signal.entry_zone.max}
                            </div>
                          </div>
                          <div className="parameter-card">
                            <div className="param-label">防守线 (止损)</div>
                            <div className="param-value" style={{ color: 'var(--color-short)', fontSize: '0.95rem' }}>
                              ${prediction.signal.stop_loss}
                            </div>
                          </div>
                          <div className="parameter-card">
                            <div className="param-label">阶梯止盈目标</div>
                            <div className="param-value" style={{ color: 'var(--color-long)', fontSize: '0.85rem', display: 'flex', flexDirection: 'column', gap: '0.15rem', marginTop: '0.1rem' }}>
                              {prediction.signal.take_profit_targets.map((tp, idx) => (
                                <div key={idx}>目标 {idx + 1}: ${tp}</div>
                              ))}
                            </div>
                          </div>
                        </div>
                      ) : (
                        <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
                          {prediction.signal.core_reason || '当前盘面不具备右侧安全盈亏比，别急着追，老实等待回踩，君子不立危墙之下！'}
                        </p>
                      )}
                    </div>

                    {/* Markdown Report Render */}
                    <div style={{ flex: 1, borderTop: '1px solid rgba(0,0,0,0.05)', paddingTop: '1rem' }}>
                      {renderMarkdown(prediction.report)}
                    </div>
                  </div>
                ) : (
                  <div className="loader-wrapper" style={{ justifyContent: 'center' }}>
                    <HelpCircle size={40} style={{ color: 'var(--text-muted)', opacity: 0.5 }} />
                    <p>等待开启诊断分析...</p>
                    <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                      点击上方【开启飞扬流派行情诊断】按钮触发智能体分析。
                    </span>
                  </div>
                )}
              </div>
            </section>

          </div>
        )}
      </main>
    </div>
    </>
  );
}

function LoaderIcon() {
  return (
    <svg className="spinner" style={{ width: '16px', height: '16px', margin: 0 }} viewBox="0 0 24 24">
      <circle className="path" cx="12" cy="12" r="10" fill="none" strokeWidth="3"></circle>
    </svg>
  );
}
