import React, { useState, useEffect } from 'react';
import { AreaChart, Settings, Play, ShieldAlert, CheckCircle2, TrendingUp, HelpCircle, Target, History, Brain } from 'lucide-react';
import KLineChart from './components/KLineChart';
import SettingsPanel from './components/SettingsPanel';
import SniperDashboard from './components/SniperDashboard';
import BacktestPanel from './components/BacktestPanel';
import PromptEditorPanel from './components/PromptEditorPanel';

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
  const logConsoleRef = React.useRef(null);

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

  // 5. Scroll log console to bottom automatically
  useEffect(() => {
    if (logConsoleRef.current) {
      logConsoleRef.current.scrollTop = logConsoleRef.current.scrollHeight;
    }
  }, [monitorLogs]);

  const handleClearLogs = () => {
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
  const handleRunDiagnostics = () => {
    setDiagLoading(true);
    setDiagError(null);
    setPrediction(null);

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
        setPrediction(data);
        setDiagLoading(false);
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
    <div className="app-container">
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
            <Brain size={16} className="text-amber-400" style={{ color: '#F59E0B' }} />
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
                    borderTop: '1px solid rgba(255,255,255,0.06)',
                    paddingTop: '0.75rem',
                    marginTop: '0.75rem',
                    height: '160px',
                    display: 'flex',
                    flexDirection: 'column',
                    minHeight: '160px'
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
                        border: '1px solid rgba(255,255,255,0.08)'
                      }}
                    >
                      清空日志
                    </button>
                  </div>
                  <div 
                    ref={logConsoleRef}
                    style={{
                      flex: 1,
                      background: 'rgba(0,0,0,0.3)',
                      border: '1px solid rgba(255,255,255,0.04)',
                      borderRadius: '6px',
                      padding: '0.5rem 0.75rem',
                      overflowY: 'auto',
                      fontFamily: 'Consolas, Monaco, monospace',
                      fontSize: '0.8rem',
                      lineHeight: '1.4',
                      color: '#81c784',
                    }}
                  >
                    {monitorLogs.map((log, idx) => {
                      let color = '#81c784';
                      if (log.includes('❌') || log.includes('失败')) color = 'var(--color-short)';
                      if (log.includes('⚡') || log.includes('手动')) color = '#64b5f6';
                      if (log.includes('🔄') || log.includes('启动')) color = '#ffb74d';
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
                </div>
              </div>
            </section>

            {/* Right Side: Prediction & Feiyang Console */}
            <section className="column-right">
              {/* Diagnose Trigger Panel */}
              <div className="panel">
                <div className="panel-header" style={{ marginBottom: '0.5rem', borderBottom: 'none', paddingBottom: 0, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div className="panel-title">
                    <span>💡 飞扬交易决策台</span>
                  </div>
                  <button
                    type="button"
                    className="btn btn-secondary"
                    style={{ fontSize: '0.75rem', padding: '0.2rem 0.55rem', display: 'flex', alignItems: 'center', gap: '0.3rem' }}
                    onClick={() => setActiveTab('prompt')}
                    title="自定义与调校 Prompt 人设规则"
                  >
                    <Brain size={13} style={{ color: '#F59E0B' }} />
                    <span>策略 Prompt</span>
                  </button>
                </div>
                <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: '1rem' }}>
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
                    <div className={`signal-box ${prediction.signal.signal_type}`}>
                      <div className="signal-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <div className={`signal-badge ${prediction.signal.signal_type}`}>
                          {prediction.signal.signal_type === 'long' && '📈 建议买入 (LONG)'}
                          {prediction.signal.signal_type === 'short' && '📉 建议做空 (SHORT)'}
                          {prediction.signal.signal_type === 'wait' && '⏳ 建议观望 (WAIT)'}
                        </div>
                        <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                          置信度评分: <span style={{ color: 'var(--color-warning)', fontWeight: 'bold', fontSize: '1.05rem' }}>{prediction.signal.confidence_score}</span> / 10
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
                          当前盘面不具备右侧安全盈亏比，别急着追，老实等待回踩，君子不立危墙之下！
                        </p>
                      )}
                    </div>

                    {/* Markdown Report Render */}
                    <div style={{ flex: 1, borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: '1rem' }}>
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
  );
}

function LoaderIcon() {
  return (
    <svg className="spinner" style={{ width: '16px', height: '16px', margin: 0 }} viewBox="0 0 24 24">
      <circle className="path" cx="12" cy="12" r="10" fill="none" strokeWidth="3"></circle>
    </svg>
  );
}
