import React, { useState, useEffect } from 'react';
import { Save, Loader2, RefreshCw, Copy, Check, ShieldAlert, Sparkles, Brain } from 'lucide-react';

export default function PromptEditorPanel({ apiBase, standalone = true }) {
  const [promptText, setPromptText] = useState('');
  const [promptIsCustom, setPromptIsCustom] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState(null);
  const [copied, setCopied] = useState(false);

  const fetchPrompt = () => {
    setLoading(true);
    fetch(`${apiBase}/api/prompt`)
      .then(res => res.json())
      .then(data => {
        setPromptText(data.prompt || '');
        setPromptIsCustom(!!data.is_custom);
        setLoading(false);
      })
      .catch(err => {
        console.error("Failed to load strategy prompt:", err);
        setLoading(false);
      });
  };

  useEffect(() => {
    fetchPrompt();
  }, [apiBase]);

  const handleSavePrompt = async () => {
    if (!promptText.trim()) {
      alert("Prompt 内容不能为空。如果需要恢复默认，请点击 '恢复内置默认' 按钮。");
      return;
    }
    if (!window.confirm('保存后，下一次诊断立即使用新 Prompt。确定保存自定义策略 Prompt 吗？')) return;
    
    setSaving(true);
    setMsg(null);
    try {
      const res = await fetch(`${apiBase}/api/prompt`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: promptText })
      });
      const data = await res.json();
      if (data.status === 'success') {
        setPromptIsCustom(true);
        setMsg({ type: 'success', text: data.message || '自定义 Prompt 保存成功！下一次诊断将立即生效。' });
      } else {
        setMsg({ type: 'error', text: data.detail || data.message || '保存失败' });
      }
    } catch (err) {
      setMsg({ type: 'error', text: `保存失败：${err.message}` });
    } finally {
      setSaving(false);
    }
  };

  const handleResetPrompt = async () => {
    if (!window.confirm('确定恢复为内置默认 Prompt 吗？当前自定义内容将被清除。')) return;
    setSaving(true);
    setMsg(null);
    try {
      const res = await fetch(`${apiBase}/api/prompt`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: '' })
      });
      const data = await res.json();
      if (data.status === 'success') {
        setPromptIsCustom(false);
        fetchPrompt();
        setMsg({ type: 'success', text: data.message || '已成功恢复为内置默认 Prompt。' });
      } else {
        setMsg({ type: 'error', text: data.detail || '恢复失败' });
      }
    } catch (err) {
      setMsg({ type: 'error', text: `恢复失败：${err.message}` });
    } finally {
      setSaving(false);
    }
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(promptText);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  // Anti-fool validation checks
  const hasJsonBlock = promptText.includes('```json');
  const hasSignalType = promptText.includes('signal_type');
  const isTooShort = promptText.trim().length > 0 && promptText.trim().length < 50;

  const lineCount = promptText ? promptText.split('\n').length : 0;
  const charCount = promptText ? promptText.length : 0;

  const content = (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', height: standalone ? '100%' : 'auto' }}>
      {/* Header Bar */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '0.75rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
          <Brain size={22} style={{ color: 'var(--color-wait)' }} />
          <h3 style={{ margin: 0, fontSize: '1.1rem', fontWeight: 700, color: 'var(--text-bright)' }}>
            飞扬策略 System Prompt 编辑器
          </h3>
          {promptIsCustom ? (
            <span style={{
              fontSize: '0.75rem', color: '#F59E0B', background: 'rgba(245, 158, 11, 0.12)',
              border: '1px solid rgba(245, 158, 11, 0.4)', borderRadius: '4px', padding: '0.2rem 0.5rem', fontWeight: 600
            }}>
              ★ 自定义生效中
            </span>
          ) : (
            <span style={{
              fontSize: '0.75rem', color: '#10B981', background: 'rgba(16, 185, 129, 0.12)',
              border: '1px solid rgba(16, 185, 129, 0.4)', borderRadius: '4px', padding: '0.2rem 0.5rem', fontWeight: 600
            }}>
              ✓ 内置默认
            </span>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={handleCopy}
            title="复制 Prompt 内容到剪贴板"
            style={{ fontSize: '0.8rem', padding: '0.4rem 0.75rem', display: 'flex', alignItems: 'center', gap: '0.35rem' }}
          >
            {copied ? <Check size={14} style={{ color: 'var(--color-long)' }} /> : <Copy size={14} />}
            <span>{copied ? '已复制' : '复制全文'}</span>
          </button>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={fetchPrompt}
            disabled={loading}
            title="重新加载当前生效 Prompt"
            style={{ fontSize: '0.8rem', padding: '0.4rem 0.75rem', display: 'flex', alignItems: 'center', gap: '0.35rem' }}
          >
            <RefreshCw size={14} className={loading ? 'spinner' : ''} />
            <span>刷新</span>
          </button>
        </div>
      </div>

      {/* Description & Warning Box */}
      <div style={{
        background: 'rgba(6, 182, 212, 0.06)',
        border: '1px solid rgba(6, 182, 212, 0.2)',
        borderRadius: '8px',
        padding: '0.85rem 1rem',
        fontSize: '0.82rem',
        color: 'var(--text-muted)',
        lineHeight: '1.6'
      }}>
        <div style={{ color: 'var(--text-bright)', fontWeight: 600, marginBottom: '0.3rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          <Sparkles size={15} style={{ color: '#06B6D4' }} />
          <span>实时调校策略思考链与人设</span>
        </div>
        <div>
          此处定义"飞扬"的核心风控原则、多周期分析框架以及 LLM 交易决策逻辑。
          保存后<strong>下一次诊断立即采用新 Prompt</strong>（Agent 在每次分析时按需装载，无需重启应用）。
        </div>
        
        {/* Anti-fool warnings */}
        {(!hasJsonBlock || !hasSignalType || isTooShort) && (
          <div style={{
            marginTop: '0.6rem',
            padding: '0.5rem 0.75rem',
            background: 'rgba(239, 68, 68, 0.1)',
            border: '1px solid rgba(239, 68, 68, 0.3)',
            borderRadius: '6px',
            color: '#F87171',
            fontSize: '0.78rem',
            display: 'flex',
            alignItems: 'flex-start',
            gap: '0.4rem'
          }}>
            <ShieldAlert size={16} style={{ flexShrink: 0, marginTop: '0.1rem' }} />
            <div>
              <strong>防呆警告：</strong>
              {!hasJsonBlock && <span> Prompt 缺失 <code>```json</code> 代码块指示，LLM 可能会输出普通文本，导致交易信号无法解析！</span>}
              {!hasSignalType && <span> Prompt 缺失 <code>signal_type</code> 字段约定，狙击引擎将无法正确下发买卖开单指令！</span>}
              {isTooShort && <span> Prompt 内容过短（低于 50 字符），系统保存时将被拦截。</span>}
            </div>
          </div>
        )}
      </div>

      {/* Message alert */}
      {msg && (
        <div style={{
          padding: '0.75rem 1rem',
          borderRadius: '6px',
          fontSize: '0.85rem',
          background: msg.type === 'success' ? 'rgba(16, 185, 129, 0.12)' : 'rgba(239, 68, 68, 0.12)',
          border: `1px solid ${msg.type === 'success' ? 'rgba(16, 185, 129, 0.4)' : 'rgba(239, 68, 68, 0.4)'}`,
          color: msg.type === 'success' ? 'var(--color-long)' : 'var(--color-short)',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center'
        }}>
          <span>{msg.text}</span>
          <button
            type="button"
            onClick={() => setMsg(null)}
            style={{ background: 'none', border: 'none', color: 'inherit', cursor: 'pointer', opacity: 0.7 }}
          >
            ✕
          </button>
        </div>
      )}

      {/* Editor Main Textarea */}
      <div style={{ flex: 1, minHeight: '380px', display: 'flex', flexDirection: 'column', position: 'relative' }}>
        {loading ? (
          <div className="loader-wrapper" style={{ minHeight: '380px', border: '1px solid var(--border-color)', borderRadius: '8px' }}>
            <div className="spinner"></div>
            <p>正在读取策略 Prompt...</p>
          </div>
        ) : (
          <>
            <textarea
              value={promptText}
              onChange={e => setPromptText(e.target.value)}
              placeholder="请输入飞扬策略 System Prompt..."
              style={{
                width: '100%',
                flex: 1,
                minHeight: '380px',
                fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
                fontSize: '0.82rem',
                lineHeight: '1.6',
                padding: '1rem',
                borderRadius: '8px',
                resize: 'vertical',
                background: 'rgba(15, 23, 42, 0.85)',
                color: '#E2E8F0',
                border: '1px solid var(--border-color)',
                boxSizing: 'border-box',
                whiteSpace: 'pre-wrap',
                outline: 'none'
              }}
            />
            {/* Status Footer Stats */}
            <div style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              fontSize: '0.75rem',
              color: 'var(--text-muted)',
              marginTop: '0.4rem',
              padding: '0 0.2rem'
            }}>
              <div>
                字符数: <strong style={{ color: 'var(--text-bright)' }}>{charCount}</strong> | 行数: <strong style={{ color: 'var(--text-bright)' }}>{lineCount}</strong>
              </div>
              <div>
                状态: {promptIsCustom ? <span style={{ color: '#F59E0B' }}>自定义覆盖文件生效中 (feiyang_prompt.txt)</span> : <span style={{ color: '#10B981' }}>内置默认代码 Prompt 生效中</span>}
              </div>
            </div>
          </>
        )}
      </div>

      {/* Footer Controls */}
      <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', flexWrap: 'wrap', marginTop: '0.5rem' }}>
        <button
          type="button"
          onClick={handleSavePrompt}
          disabled={saving || loading}
          className="btn btn-primary"
          style={{ padding: '0.65rem 1.5rem', display: 'flex', alignItems: 'center', gap: '0.5rem', fontWeight: 600 }}
        >
          {saving ? <Loader2 size={16} className="spinner" /> : <Save size={16} />}
          <span>{saving ? '正在保存...' : '保存自定义 Prompt'}</span>
        </button>

        <button
          type="button"
          onClick={handleResetPrompt}
          disabled={saving || loading || !promptIsCustom}
          className="btn btn-secondary"
          style={{
            padding: '0.65rem 1.25rem',
            color: promptIsCustom ? '#EF4444' : 'var(--text-muted)',
            borderColor: promptIsCustom ? 'rgba(239,68,68,0.4)' : 'var(--border-color)',
            opacity: !promptIsCustom ? 0.6 : 1,
            cursor: !promptIsCustom ? 'not-allowed' : 'pointer'
          }}
        >
          ↩️ 恢复内置默认 Prompt
        </button>

        <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)', marginLeft: 'auto' }}>
          💡 快捷提示：修改后无需重启服务器，下一次点击【开启行情诊断】或自动盯盘将直接使用最新 Prompt。
        </span>
      </div>
    </div>
  );

  if (!standalone) {
    return content;
  }

  return (
    <div className="panel" style={{ height: '100%', overflowY: 'auto', padding: '1.5rem' }}>
      {content}
    </div>
  );
}
