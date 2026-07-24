import React, { useState, useEffect } from 'react';
import { Activity, TrendingUp, Calendar, Newspaper, AlertTriangle, RefreshCw } from 'lucide-react';

export default function SentimentPanel({ apiBase }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  const fetchSentiment = async () => {
    try {
      setLoading(true);
      const res = await fetch(`${apiBase}/api/sentiment`);
      const json = await res.json();
      setData(json);
    } catch (err) {
      console.error("Failed to fetch sentiment:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSentiment();
    const interval = setInterval(fetchSentiment, 60000); // Refresh every 60s
    return () => clearInterval(interval);
  }, [apiBase]);

  const getFngColor = (val) => {
    if (val >= 75) return '#10B981';
    if (val >= 55) return '#84CC16';
    if (val >= 45) return '#F59E0B';
    if (val >= 25) return '#F97316';
    return '#EF4444';
  };

  const getRiskStyle = (level) => {
    switch (level) {
      case 'extreme': return { color: '#EF4444', bg: 'rgba(239,68,68,0.12)', border: 'rgba(239,68,68,0.4)', label: '🚨 极端风险' };
      case 'elevated': return { color: '#F97316', bg: 'rgba(249,115,22,0.12)', border: 'rgba(249,115,22,0.4)', label: '⚠️ 风险偏高' };
      case 'normal': return { color: '#F59E0B', bg: 'rgba(245,158,11,0.12)', border: 'rgba(245,158,11,0.4)', label: '📊 风险正常' };
      default: return { color: '#10B981', bg: 'rgba(16,185,129,0.12)', border: 'rgba(16,185,129,0.4)', label: '✅ 低风险' };
    }
  };

  const getBiasLabel = (bias) => {
    switch (bias) {
      case 'aggressive': return '🟢 积极进攻';
      case 'normal': return '🟡 正常操作';
      case 'cautious': return '🟠 谨慎减仓';
      case 'stand_aside': return '🔴 观望不动';
      default: return '🟡 正常操作';
    }
  };

  if (loading && !data) {
    return (
      <div style={{ padding: '1.5rem', textAlign: 'center', color: 'var(--text-muted)', fontSize: '0.82rem' }}>
        正在加载市场情绪数据...
      </div>
    );
  }

  const risk = getRiskStyle(data?.risk_level || 'normal');

  return (
    <div style={{
      background: 'linear-gradient(135deg, rgba(15, 23, 42, 0.9), rgba(30, 41, 59, 0.7))',
      border: '1px solid rgba(139, 92, 246, 0.25)',
      borderRadius: '12px',
      padding: '1rem 1.25rem',
      marginBottom: '1rem'
    }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.85rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <Activity size={18} style={{ color: '#8B5CF6' }} />
          <span style={{ fontWeight: 700, fontSize: '0.95rem', color: 'var(--text-bright)' }}>📡 市场情绪面实时监控</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          {/* Risk Badge */}
          <span style={{
            padding: '3px 10px', borderRadius: '6px', fontSize: '0.72rem', fontWeight: 700,
            background: risk.bg, color: risk.color, border: `1px solid ${risk.border}`
          }}>
            {risk.label}
          </span>
          <span style={{
            padding: '3px 10px', borderRadius: '6px', fontSize: '0.72rem', fontWeight: 600,
            background: 'rgba(139, 92, 246, 0.1)', color: '#A78BFA', border: '1px solid rgba(139, 92, 246, 0.3)'
          }}>
            策略建议: {getBiasLabel(data?.trading_bias)}
          </span>
          <button
            onClick={fetchSentiment}
            style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', padding: '2px' }}
            title="刷新情绪数据"
          >
            <RefreshCw size={14} className={loading ? 'spin' : ''} />
          </button>
        </div>
      </div>

      {/* Content Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '0.75rem' }}>
        {/* Fear & Greed */}
        <div style={{ background: 'rgba(0,0,0,0.2)', borderRadius: '8px', padding: '0.75rem', border: '1px solid rgba(255,255,255,0.05)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.5rem', fontSize: '0.72rem', color: 'var(--text-muted)' }}>
            <TrendingUp size={13} />
            <span>恐惧 & 贪婪指数</span>
          </div>
          {data?.fear_greed ? (
            <div>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.5rem' }}>
                <span style={{ fontSize: '1.5rem', fontWeight: 800, color: getFngColor(data.fear_greed.value) }}>
                  {data.fear_greed.value}
                </span>
                <span style={{ fontSize: '0.78rem', color: getFngColor(data.fear_greed.value), fontWeight: 600 }}>
                  {data.fear_greed.label}
                </span>
              </div>
              {/* Progress bar */}
              <div style={{ height: '4px', background: 'rgba(255,255,255,0.08)', borderRadius: '2px', marginTop: '0.4rem', overflow: 'hidden' }}>
                <div style={{ height: '100%', width: `${data.fear_greed.value}%`, background: getFngColor(data.fear_greed.value), borderRadius: '2px', transition: 'width 0.5s' }} />
              </div>
              {data.fear_greed.trend && (
                <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: '0.3rem' }}>
                  趋势: {data.fear_greed.trend === 'rising' ? '📈 上升' : '📉 下降'}
                  {data.fear_greed.prev_value !== undefined && ` (昨日 ${data.fear_greed.prev_value})`}
                </div>
              )}
            </div>
          ) : (
            <div style={{ color: 'var(--text-muted)', fontSize: '0.78rem' }}>数据暂不可用</div>
          )}
        </div>

        {/* Funding Rates */}
        <div style={{ background: 'rgba(0,0,0,0.2)', borderRadius: '8px', padding: '0.75rem', border: '1px solid rgba(255,255,255,0.05)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.5rem', fontSize: '0.72rem', color: 'var(--text-muted)' }}>
            <Activity size={13} />
            <span>资金费率 (Funding Rate)</span>
          </div>
          {data?.funding_rates && Object.keys(data.funding_rates).length > 0 ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
              {Object.entries(data.funding_rates).slice(0, 4).map(([sym, rate]) => (
                <div key={sym} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '0.75rem' }}>
                  <span style={{ color: 'var(--text-bright)', fontWeight: 500 }}>{sym.replace('/USDT', '')}</span>
                  <span style={{
                    fontFamily: 'monospace', fontWeight: 600,
                    color: rate > 0.0003 ? '#EF4444' : rate < -0.0003 ? '#10B981' : 'var(--text-muted)'
                  }}>
                    {(rate * 100).toFixed(4)}%
                    {Math.abs(rate) > 0.0005 && <span style={{ marginLeft: '3px', fontSize: '0.65rem' }}>⚡</span>}
                  </span>
                </div>
              ))}
              <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)', marginTop: '0.2rem' }}>
                正值=多头付费给空头(过热) | 负值=空头付费给多头
              </div>
            </div>
          ) : (
            <div style={{ color: 'var(--text-muted)', fontSize: '0.78rem' }}>数据暂不可用</div>
          )}
        </div>

        {/* Macro Events */}
        <div style={{ background: 'rgba(0,0,0,0.2)', borderRadius: '8px', padding: '0.75rem', border: '1px solid rgba(255,255,255,0.05)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.5rem', fontSize: '0.72rem', color: 'var(--text-muted)' }}>
            <Calendar size={13} />
            <span>宏观经济事件</span>
          </div>
          {data?.macro_event ? (
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                <AlertTriangle size={14} style={{ color: data.macro_event.impact === 'critical' ? '#EF4444' : '#F59E0B' }} />
                <span style={{ fontSize: '0.82rem', fontWeight: 600, color: 'var(--text-bright)' }}>
                  {data.macro_event.event}
                </span>
              </div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.35rem' }}>
                {data.macro_event.hours_until > 0
                  ? `⏰ 约 ${Math.round(data.macro_event.hours_until)} 小时后公布`
                  : `📢 已于 ${Math.abs(Math.round(data.macro_event.hours_until))} 小时前公布`}
              </div>
              <span style={{
                display: 'inline-block', marginTop: '0.3rem', padding: '1px 6px', borderRadius: '4px',
                fontSize: '0.65rem', fontWeight: 600,
                background: data.macro_event.impact === 'critical' ? 'rgba(239,68,68,0.15)' : 'rgba(245,158,11,0.15)',
                color: data.macro_event.impact === 'critical' ? '#EF4444' : '#F59E0B'
              }}>
                影响力: {data.macro_event.impact === 'critical' ? '极高' : data.macro_event.impact === 'high' ? '高' : '中'}
              </span>
            </div>
          ) : (
            <div style={{ color: '#10B981', fontSize: '0.78rem' }}>✅ 24h 内无重大宏观事件</div>
          )}
        </div>

        {/* News Headlines */}
        <div style={{ background: 'rgba(0,0,0,0.2)', borderRadius: '8px', padding: '0.75rem', border: '1px solid rgba(255,255,255,0.05)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.5rem', fontSize: '0.72rem', color: 'var(--text-muted)' }}>
            <Newspaper size={13} />
            <span>加密市场快讯</span>
          </div>
          {data?.news && data.news.length > 0 ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
              {data.news.slice(0, 4).map((item, idx) => (
                <div key={idx} style={{ fontSize: '0.72rem', color: 'var(--text-bright)', lineHeight: '1.3', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  <span style={{ color: '#8B5CF6', marginRight: '4px' }}>•</span>
                  {item.title}
                </div>
              ))}
            </div>
          ) : (
            <div style={{ color: 'var(--text-muted)', fontSize: '0.78rem' }}>暂无最新快讯</div>
          )}
        </div>
      </div>
    </div>
  );
}
