import React, { useState, useEffect } from 'react';
import { Save, Loader2, ShieldCheck } from 'lucide-react';

export default function SettingsPanel({ apiBase }) {
  const [config, setConfig] = useState({
    symbol: 'BTC/USDT',
    exchange: 'binance',
    timeframes: ['1M', '1W', '1D', '4h', '1h'],
    fib_lookback: 100,
    llm_model: 'gpt-4o',
    llm_temp: 0.1,
    llm_max_tokens: 3000,
    notify_enabled: false,
    notify_channels: ['telegram'],
    telegram_chat_id: '',
    openai_api_key: '',
    openai_api_base: '',
    telegram_bot_token: '',
    serverchan_send_key: '',
    bark_device_key: '',
  });

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState(null);
  const [testingConnection, setTestingConnection] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [testingNotification, setTestingNotification] = useState(false);
  const [notificationTestResult, setNotificationTestResult] = useState(null);
  const [newSymbol, setNewSymbol] = useState('');

  // Fetch current configs on mount
  useEffect(() => {
    fetch(`${apiBase}/api/config`)
      .then(res => {
        if (!res.ok) throw new Error("Failed to fetch settings.");
        return res.json();
      })
      .then(data => {
        try {
          const draft = localStorage.getItem('feiyang_settings_draft');
          if (draft) {
            setConfig(prev => ({ ...data, ...JSON.parse(draft) }));
          } else {
            setConfig(data);
          }
        } catch (e) {
          setConfig(data);
        }
        setLoading(false);
      })
      .catch(err => {
        console.error(err);
        setLoading(false);
      });
  }, [apiBase]);

  // Persist draft config on change
  useEffect(() => {
    if (!loading) {
      try {
        localStorage.setItem('feiyang_settings_draft', JSON.stringify(config));
      } catch (e) {}
    }
  }, [config, loading]);

  const handleChange = (e) => {
    const { name, value, type, checked } = e.target;
    if (type === 'checkbox') {
      setConfig(prev => ({ ...prev, [name]: checked }));
    } else if (type === 'number') {
      setConfig(prev => ({ ...prev, [name]: parseFloat(value) || value }));
    } else {
      setConfig(prev => ({ ...prev, [name]: value }));
    }
  };

  const handleChannelChange = (channel, checked) => {
    setConfig(prev => {
      let channels = [...prev.notify_channels];
      if (checked && !channels.includes(channel)) {
        channels.push(channel);
      } else if (!checked) {
        channels = channels.filter(c => c !== channel);
      }
      return { ...prev, notify_channels: channels };
    });
  };

  const handleSave = (e) => {
    e.preventDefault();
    setSaving(true);
    setMessage(null);

    fetch(`${apiBase}/api/config`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    })
      .then(res => {
        if (!res.ok) throw new Error("Failed to save settings.");
        return res.json();
      })
      .then(data => {
        setSaving(false);
        try { localStorage.removeItem('feiyang_settings_draft'); } catch (e) {}
        setMessage({ type: 'success', text: '配置保存成功！' });
        setTimeout(() => setMessage(null), 3000);
      })
      .catch(err => {
        console.error(err);
        setSaving(false);
        setMessage({ type: 'error', text: '保存失败，请检查后端状态。' });
      });
  };

  const handleTestConnection = () => {
    if (!config.openai_api_key) {
      setTestResult({ status: 'error', message: '请先填写 API Key 再测试。' });
      return;
    }
    setTestingConnection(true);
    setTestResult(null);

    fetch(`${apiBase}/api/test-llm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        openai_api_key: config.openai_api_key,
        openai_api_base: config.openai_api_base || 'https://api.openai.com/v1',
        llm_model: config.llm_model,
        llm_temp: config.llm_temp,
      }),
    })
      .then(res => {
        if (!res.ok) throw new Error("测试接口请求失败");
        return res.json();
      })
      .then(data => {
        setTestingConnection(false);
        if (data.status === 'success') {
          setTestResult({ status: 'success', message: data.message });
        } else {
          setTestResult({ status: 'error', message: data.message });
        }
      })
      .catch(err => {
        console.error(err);
        setTestingConnection(false);
        setTestResult({ status: 'error', message: `连接测试超时或失败: ${err.message}` });
      });
  };

  const handleTestNotification = () => {
    if (config.notify_channels.length === 0) {
      setNotificationTestResult({ status: 'error', message: '请先选择至少一个通知管道再测试。' });
      return;
    }
    setTestingNotification(true);
    setNotificationTestResult(null);

    fetch(`${apiBase}/api/test-notification`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        notify_channels: config.notify_channels,
        telegram_bot_token: config.telegram_bot_token,
        telegram_chat_id: config.telegram_chat_id,
        serverchan_send_key: config.serverchan_send_key,
        bark_device_key: config.bark_device_key,
      }),
    })
      .then(res => {
        if (!res.ok) throw new Error("推送测试接口请求失败");
        return res.json();
      })
      .then(data => {
        setTestingNotification(false);
        if (data.status === 'success') {
          setNotificationTestResult({ status: 'success', message: data.message });
        } else {
          setNotificationTestResult({ status: 'error', message: data.message });
        }
      })
      .catch(err => {
        console.error(err);
        setTestingNotification(false);
        setNotificationTestResult({ status: 'error', message: `推送测试超时或失败: ${err.message}` });
      });
  };

  const handleAddSymbol = () => {
    const trimmed = newSymbol.trim().toUpperCase();
    if (!trimmed) return;
    if (!trimmed.includes('/')) {
      alert("交易对格式错误，必须包含斜杠（例如：BTC/USDT）");
      return;
    }
    if (config.symbols && config.symbols.includes(trimmed)) {
      alert("交易对已在监控列表中");
      return;
    }
    setConfig(prev => ({
      ...prev,
      symbols: [...(prev.symbols || []), trimmed]
    }));
    setNewSymbol('');
  };

  const handleDeleteSymbol = (symToDelete) => {
    setConfig(prev => {
      const nextSymbols = (prev.symbols || []).filter(s => s !== symToDelete);
      let nextSymbol = prev.symbol;
      if (prev.symbol === symToDelete) {
        nextSymbol = nextSymbols.length > 0 ? nextSymbols[0] : '';
      }
      return {
        ...prev,
        symbols: nextSymbols,
        symbol: nextSymbol
      };
    });
  };

  if (loading) {
    return (
      <div className="loader-wrapper">
        <div className="spinner"></div>
        <p>正在加载系统配置...</p>
      </div>
    );
  }

  return (
    <form onSubmit={handleSave} className="panel" style={{ gap: '1rem', height: '100%', overflowY: 'auto' }}>
      <div className="panel-header">
        <div className="panel-title">
          <ShieldCheck size={20} className="pulse-indicator" style={{ background: 'var(--color-wait)', boxShadow: '0 0 0 0 rgba(41, 121, 255, 0.4)' }} />
          <span>系统核心配置与秘钥管理</span>
        </div>
        <button type="submit" className="btn btn-primary" disabled={saving}>
          {saving ? <Loader2 size={16} className="spinner" /> : <Save size={16} />}
          <span>{saving ? '正在保存...' : '保存配置'}</span>
        </button>
      </div>

      {message && (
        <div 
          className="signal-box" 
          style={{ 
            background: message.type === 'success' ? 'rgba(0, 230, 118, 0.1)' : 'rgba(255, 23, 68, 0.1)',
            borderColor: message.type === 'success' ? 'var(--color-long)' : 'var(--color-short)',
            padding: '0.75rem',
            borderRadius: '6px',
            fontSize: '0.9rem'
          }}
        >
          {message.text}
        </div>
      )}

      {/* Grid container */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.5rem' }}>
        {/* Left Config Column */}
        <div>
          <h4 style={{ color: 'var(--text-bright)', marginBottom: '1rem', borderBottom: '1px solid rgba(255,255,255,0.05)', paddingBottom: '0.25rem' }}>⚙️ 交易与大模型参数</h4>
          
          <div className="form-group">
            <label className="form-label">主页默认展示交易对 (启动时加载的默认币对)</label>
            <select 
              name="symbol" 
              value={config.symbol} 
              onChange={handleChange} 
              className="form-control"
            >
              {config.symbols && config.symbols.map(sym => (
                <option key={sym} value={sym}>{sym}</option>
              ))}
            </select>
          </div>

          <div className="form-group" style={{ marginBottom: '1.25rem' }}>
            <label className="form-label" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span>自选监控交易对列表</span>
              <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>已添加 {config.symbols ? config.symbols.length : 0} 个</span>
            </label>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', marginBottom: '0.75rem', padding: '0.5rem', background: 'rgba(255,255,255,0.01)', borderRadius: '6px', border: '1px solid rgba(255,255,255,0.04)', minHeight: '38px', alignItems: 'center' }}>
              {config.symbols && config.symbols.map(sym => (
                <div 
                  key={sym} 
                  className="badge" 
                  style={{ 
                    background: 'rgba(255,255,255,0.04)', 
                    border: '1px solid rgba(255,255,255,0.08)', 
                    borderRadius: '4px', 
                    padding: '0.2rem 0.5rem', 
                    display: 'flex', 
                    alignItems: 'center', 
                    gap: '0.4rem',
                    fontSize: '0.8rem',
                    color: 'var(--text-bright)'
                  }}
                >
                  <span>{sym}</span>
                  <span 
                    onClick={() => handleDeleteSymbol(sym)} 
                    style={{ cursor: 'pointer', color: 'var(--color-short)', fontWeight: 'bold', fontSize: '1.1rem', lineHeight: '1' }}
                    title="删除"
                  >
                    ×
                  </span>
                </div>
              ))}
              {(!config.symbols || config.symbols.length === 0) && (
                <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>暂无监控交易对</span>
              )}
            </div>
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <input 
                type="text" 
                value={newSymbol} 
                onChange={(e) => setNewSymbol(e.target.value.toUpperCase())} 
                placeholder="输入新交易对 (如 ZEC/USDT)" 
                className="form-control"
                style={{ flex: 1, margin: 0, padding: '0.4rem 0.6rem', fontSize: '0.85rem' }}
              />
              <button 
                type="button" 
                onClick={handleAddSymbol} 
                className="btn btn-secondary" 
                style={{ padding: '0.4rem 1rem', fontSize: '0.85rem', height: 'auto', minHeight: 'unset' }}
              >
                添加
              </button>
            </div>
          </div>

          <div className="form-group">
            <label className="form-label">极值斐波那契回撤计算窗口 (天)</label>
            <input 
              type="number" 
              name="fib_lookback" 
              value={config.fib_lookback} 
              onChange={handleChange} 
              className="form-control" 
              required
            />
          </div>

          <div className="form-group">
            <label className="form-label">LLM API Key (OpenAI / DeepSeek / Gemini)</label>
            <input 
              type="password" 
              name="openai_api_key" 
              value={config.openai_api_key} 
              onChange={handleChange} 
              className="form-control" 
              placeholder="sk-..."
            />
          </div>

          <div className="form-group">
            <label className="form-label">LLM API Endpoint Base</label>
            <input 
              type="text" 
              name="openai_api_base" 
              value={config.openai_api_base} 
              onChange={handleChange} 
              className="form-control" 
              placeholder="https://api.openai.com/v1"
            />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '0.75rem' }}>
            <div className="form-group">
              <label className="form-label">大模型 Model Name</label>
              <input 
                type="text" 
                name="llm_model" 
                value={config.llm_model} 
                onChange={handleChange} 
                className="form-control" 
                placeholder="gpt-4o"
                required
              />
            </div>
            <div className="form-group">
              <label className="form-label">Temperature</label>
              <input 
                type="number" 
                step="0.05" 
                min="0" 
                max="1.5" 
                name="llm_temp" 
                value={config.llm_temp} 
                onChange={handleChange} 
                className="form-control" 
                required
              />
            </div>
          </div>

          <div className="form-group" style={{ marginTop: '1rem' }}>
            <button 
              type="button" 
              className="btn btn-secondary" 
              onClick={handleTestConnection} 
              disabled={testingConnection}
              style={{ width: '100%', justifyContent: 'center', display: 'flex', alignItems: 'center', gap: '0.5rem', padding: '0.6rem' }}
            >
              {testingConnection ? <Loader2 size={14} className="spinner" /> : null}
              <span>{testingConnection ? '正在测试连接...' : '🔌 测试大模型连通性'}</span>
            </button>
            {testResult && (
              <div 
                style={{ 
                  marginTop: '0.75rem', 
                  padding: '0.75rem', 
                  borderRadius: '6px', 
                  fontSize: '0.85rem',
                  border: '1px solid',
                  background: testResult.status === 'success' ? 'rgba(0, 230, 118, 0.08)' : 'rgba(255, 23, 68, 0.08)',
                  borderColor: testResult.status === 'success' ? 'rgba(0, 230, 118, 0.3)' : 'rgba(255, 23, 68, 0.3)',
                  color: testResult.status === 'success' ? 'var(--color-long)' : 'var(--color-short)',
                  lineHeight: '1.4'
                }}
              >
                {testResult.message}
              </div>
            )}
          </div>
        </div>

        {/* Right Notifier Column */}
        <div>
          <h4 style={{ color: 'var(--text-bright)', marginBottom: '1rem', borderBottom: '1px solid rgba(255,255,255,0.05)', paddingBottom: '0.25rem' }}>🔔 消息通道设置</h4>
          
          <div className="form-group" style={{ marginBottom: '1rem' }}>
            <div className="checkbox-group">
              <input 
                type="checkbox" 
                name="notify_enabled" 
                checked={config.notify_enabled} 
                onChange={handleChange} 
                id="notify_enabled_chk"
                className="checkbox-input"
              />
              <label htmlFor="notify_enabled_chk" style={{ cursor: 'pointer', fontWeight: 600 }}>启用通知消息推送</label>
            </div>
          </div>

          {config.notify_enabled && (
            <div style={{ padding: '0.75rem', background: 'rgba(255,255,255,0.01)', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.05)' }}>
              <div className="form-group">
                <label className="form-label">通知管道选择</label>
                <div style={{ display: 'flex', gap: '1rem', marginTop: '0.5rem' }}>
                  {['telegram', 'serverchan', 'bark'].map(ch => (
                    <div className="checkbox-group" key={ch}>
                      <input 
                        type="checkbox" 
                        checked={config.notify_channels.includes(ch)} 
                        onChange={(e) => handleChannelChange(ch, e.target.checked)} 
                        id={`ch_${ch}`}
                        className="checkbox-input"
                      />
                      <label htmlFor={`ch_${ch}`} style={{ textTransform: 'capitalize', cursor: 'pointer' }}>{ch === 'serverchan' ? 'Server酱 (WeChat)' : ch}</label>
                    </div>
                  ))}
                </div>
              </div>

              {config.notify_channels.includes('telegram') && (
                <>
                  <div className="form-group">
                    <label className="form-label">Telegram Bot Token</label>
                    <input 
                      type="password" 
                      name="telegram_bot_token" 
                      value={config.telegram_bot_token} 
                      onChange={handleChange} 
                      className="form-control" 
                      placeholder="bot_token..."
                    />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Telegram Chat ID</label>
                    <input 
                      type="text" 
                      name="telegram_chat_id" 
                      value={config.telegram_chat_id} 
                      onChange={handleChange} 
                      className="form-control" 
                      placeholder="e.g. -100123456"
                    />
                  </div>
                </>
              )}

              {config.notify_channels.includes('serverchan') && (
                <div className="form-group">
                  <label className="form-label">Server酱 Send Key</label>
                  <input 
                    type="password" 
                    name="serverchan_send_key" 
                    value={config.serverchan_send_key} 
                    onChange={handleChange} 
                    className="form-control" 
                    placeholder="SCT..."
                  />
                </div>
              )}

              {config.notify_channels.includes('bark') && (
                <div className="form-group">
                  <label className="form-label">Bark Device Key</label>
                  <input 
                    type="password" 
                    name="bark_device_key" 
                    value={config.bark_device_key} 
                    onChange={handleChange} 
                    className="form-control" 
                    placeholder="device_key..."
                  />
                </div>
              )}

              <div className="form-group" style={{ marginTop: '1rem' }}>
                <button 
                  type="button" 
                  className="btn btn-secondary" 
                  onClick={handleTestNotification} 
                  disabled={testingNotification}
                  style={{ width: '100%', justifyContent: 'center', display: 'flex', alignItems: 'center', gap: '0.5rem', padding: '0.6rem' }}
                >
                  {testingNotification ? <Loader2 size={14} className="spinner" /> : null}
                  <span>{testingNotification ? '正在发送测试推送...' : '🔔 发送测试推送'}</span>
                </button>
                {notificationTestResult && (
                  <div 
                    style={{ 
                      marginTop: '0.75rem', 
                      padding: '0.75rem', 
                      borderRadius: '6px', 
                      fontSize: '0.85rem',
                      border: '1px solid',
                      background: notificationTestResult.status === 'success' ? 'rgba(0, 230, 118, 0.08)' : 'rgba(255, 23, 68, 0.08)',
                      borderColor: notificationTestResult.status === 'success' ? 'rgba(0, 230, 118, 0.3)' : 'rgba(255, 23, 68, 0.3)',
                      color: notificationTestResult.status === 'success' ? 'var(--color-long)' : 'var(--color-short)',
                      lineHeight: '1.4'
                    }}
                  >
                    {notificationTestResult.message}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

    </form>
  );
}
