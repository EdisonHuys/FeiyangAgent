import React, { useRef, useState, useCallback } from 'react';

/**
 * LiquidGlass — Apple "Liquid Glass" inspired translucent panel.
 * Implements: backdrop blur + saturation, light-aware border rings,
 * cursor-following sheen, subtle 3D tilt, and elastic hover scale.
 *
 * CRITICAL: uses display:flex + flexDirection:column so parent .panel
 * layout (flex column) is preserved. The inner content wrapper also
 * stretches to fill available space.
 */
const LiquidGlass = ({
  children,
  className = '',
  style = {},
  blurAmount = 16,
  saturation = 160,
  elasticity = 0.15,
  cornerRadius = 16,
  padding = '16px',
  onClick,
  ...rest
}) => {
  const containerRef = useRef(null);
  const [coords, setCoords] = useState({ x: 0, y: 0 });
  const [isHovered, setIsHovered] = useState(false);
  const [tilt, setTilt] = useState({ x: 0, y: 0 });

  const handleMouseMove = useCallback((e) => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    setCoords({ x, y });

    const centerX = rect.width / 2;
    const centerY = rect.height / 2;
    const maxTilt = 0;
    const tiltX = Math.max(-maxTilt, Math.min(maxTilt, -(y - centerY) / centerY * (elasticity * 10)));
    const tiltY = Math.max(-maxTilt, Math.min(maxTilt, (x - centerX) / centerX * (elasticity * 10)));
    setTilt({ x: tiltX, y: tiltY });
  }, [elasticity]);

  const handleMouseEnter = useCallback(() => setIsHovered(true), []);
  const handleMouseLeave = useCallback(() => {
    setIsHovered(false);
    setTilt({ x: 0, y: 0 });
  }, []);

  return (
    <div
      ref={containerRef}
      className={`liquid-glass-panel ${className}`}
      onMouseMove={handleMouseMove}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      onClick={onClick}
      style={{
        display: 'flex',
        flexDirection: 'column',
        position: 'relative',
        borderRadius: `${cornerRadius}px`,
        padding: padding,
        backdropFilter: `blur(var(--glass-blur)) saturate(var(--glass-saturation))`,
        WebkitBackdropFilter: `blur(var(--glass-blur)) saturate(var(--glass-saturation))`,
        border: `1px solid ${isHovered ? 'var(--glass-border-strong)' : 'var(--glass-border)'}`,
        transition: 'box-shadow 0.3s ease, background 0.3s ease, border 0.3s ease',
        willChange: 'auto',
        boxSizing: 'border-box',
        overflow: 'hidden',
        background: isHovered
          ? 'var(--glass-bg-hover)'
          : 'var(--glass-bg)',
        boxShadow: isHovered
          ? 'var(--glass-shadow-elevated)'
          : 'var(--glass-shadow)',
        ...style,
      }}
      {...rest}
    >
      {/* Cursor-following light sheen */}
      <div
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          borderRadius: `${cornerRadius - 1}px`,
          pointerEvents: 'none',
          zIndex: 1,
          opacity: isHovered ? 1 : 0,
          transition: 'opacity 0.3s ease',
          background: `radial-gradient(600px circle at ${coords.x}px ${coords.y}px, rgba(255, 255, 255, 0.06), transparent 55%)`,
          mixBlendMode: 'screen',
        }}
      />
      {/* Top edge highlight — simulates light catching the glass rim */}
      <div
        style={{
          position: 'absolute',
          top: 0,
          left: '10%',
          right: '10%',
          height: '1px',
          pointerEvents: 'none',
          zIndex: 1,
          background: 'linear-gradient(90deg, transparent, rgba(255,255,255,0.2) 30%, rgba(255,255,255,0.35) 50%, rgba(255,255,255,0.2) 70%, transparent)',
          borderRadius: '1px',
        }}
      />
      {/* Content wrapper — fills the panel */}
      <div style={{
        position: 'relative',
        zIndex: 2,
        display: 'flex',
        flexDirection: 'column',
        flex: 1,
        minHeight: 0,
        width: '100%',
      }}>
        {children}
      </div>
    </div>
  );
};

export default LiquidGlass;
