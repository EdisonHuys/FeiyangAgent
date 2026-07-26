import React, { useEffect, useRef } from 'react';
// Import createChart along with the specific series types required by version 5
import { createChart, CandlestickSeries, HistogramSeries, LineSeries } from 'lightweight-charts';

export default function KLineChart({ data }) {
  const chartContainerRef = useRef(null);
  const chartRef = useRef(null);

  useEffect(() => {
    if (!chartContainerRef.current || !data || data.length === 0) return;

    // Clear container to prevent duplicate charts on React double-renders (very common)
    chartContainerRef.current.innerHTML = '';

    // 1. Sanitize and sort data defensively (essential for lightweight-charts stability)
    const seenTimes = new Set();
    const cleanData = [];
    
    // Sort chronologically (oldest first)
    const sortedData = [...data].sort((a, b) => a.time - b.time);
    
    for (const d of sortedData) {
      if (!seenTimes.has(d.time)) {
        seenTimes.add(d.time);
        cleanData.push(d);
      }
    }

    if (cleanData.length === 0) return;

    // Determine initial dimensions. Default to 600px if layout is still reflowing (0 width)
    // Passing 0 or negative values to WebKit canvas drawing contexts will crash WebKit on macOS.
    const initialWidth = chartContainerRef.current.clientWidth > 100 
      ? chartContainerRef.current.clientWidth 
      : 600;

    // 2. Create Chart Instance
    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { color: 'transparent' },
        textColor: '#86868b',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: 'rgba(0, 0, 0, 0.05)' },
        horzLines: { color: 'rgba(0, 0, 0, 0.05)' },
      },
      crosshair: {
        mode: 1, // Magnet
        vertLine: {
          color: 'rgba(0, 122, 255, 0.4)',
          width: 1,
          style: 1,
          labelBackgroundColor: '#ffffff',
        },
        horzLine: {
          color: 'rgba(0, 122, 255, 0.4)',
          width: 1,
          style: 1,
          labelBackgroundColor: '#ffffff',
        },
      },
      timeScale: {
        borderColor: 'rgba(0, 0, 0, 0.08)',
        timeVisible: true,
        secondsVisible: false,
      },
      rightPriceScale: {
        borderColor: 'rgba(0, 0, 0, 0.08)',
      },
      width: initialWidth,
      height: 400,
    });

    chartRef.current = chart;

    // 3. Add Candlestick Series (Using v5 addSeries API)
    const candlestickSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#34c759',
      downColor: '#ff3b30',
      borderVisible: false,
      wickUpColor: '#34c759',
      wickDownColor: '#ff3b30',
    });

    // 4. Add Volume Series (Using v5 addSeries API)
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: '', // Overlay scale
    });

    volumeSeries.priceScale().applyOptions({
      scaleMargins: {
        top: 0.8, // Volume occupies bottom 20%
        bottom: 0,
      },
    });

    // 5. Add Indicator Line Series (Using v5 addSeries API)
    const ma5Series = chart.addSeries(LineSeries, { color: '#ff9500', lineWidth: 1.5, title: 'MA5' });
    const ma10Series = chart.addSeries(LineSeries, { color: '#007aff', lineWidth: 1.5, title: 'MA10' });
    const ma30Series = chart.addSeries(LineSeries, { color: '#af52de', lineWidth: 1.5, title: 'MA30' });
    const ema55Series = chart.addSeries(LineSeries, { color: '#ff3b30', lineWidth: 2, title: 'EMA55' });
    
    // Bollinger Bands Lines (Muted styles)
    const bbUpperSeries = chart.addSeries(LineSeries, { color: 'rgba(142, 142, 147, 0.4)', lineWidth: 1, lineStyle: 2, title: 'BB Upper' });
    const bbMiddleSeries = chart.addSeries(LineSeries, { color: 'rgba(142, 142, 147, 0.25)', lineWidth: 1, lineStyle: 1 });
    const bbLowerSeries = chart.addSeries(LineSeries, { color: 'rgba(142, 142, 147, 0.4)', lineWidth: 1, lineStyle: 2, title: 'BB Lower' });

    // 6. Map and Load Data
    const candleData = cleanData.map(d => ({
      time: d.time,
      open: d.open,
      high: d.high,
      low: d.low,
      close: d.close,
    }));

    const volumeData = cleanData.map(d => ({
      time: d.time,
      value: d.volume,
      color: d.close >= d.open ? 'rgba(52, 199, 89, 0.2)' : 'rgba(255, 59, 48, 0.2)',
    }));

    // Indicators (Filter out null values)
    const ma5Data = cleanData.filter(d => d.ma5 !== undefined).map(d => ({ time: d.time, value: d.ma5 }));
    const ma10Data = cleanData.filter(d => d.ma10 !== undefined).map(d => ({ time: d.time, value: d.ma10 }));
    const ma30Data = cleanData.filter(d => d.ma30 !== undefined).map(d => ({ time: d.time, value: d.ma30 }));
    const ema55Data = cleanData.filter(d => d.ema55 !== undefined).map(d => ({ time: d.time, value: d.ema55 }));
    
    const bbUpperData = cleanData.filter(d => d.bb_upper !== undefined).map(d => ({ time: d.time, value: d.bb_upper }));
    const bbMiddleData = cleanData.filter(d => d.bb_middle !== undefined).map(d => ({ time: d.time, value: d.bb_middle }));
    const bbLowerData = cleanData.filter(d => d.bb_lower !== undefined).map(d => ({ time: d.time, value: d.bb_lower }));

    // Set data to series
    candlestickSeries.setData(candleData);
    volumeSeries.setData(volumeData);
    ma5Series.setData(ma5Data);
    ma10Series.setData(ma10Data);
    ma30Series.setData(ma30Data);
    ema55Series.setData(ema55Data);
    bbUpperSeries.setData(bbUpperData);
    bbMiddleSeries.setData(bbMiddleData);
    bbLowerSeries.setData(bbLowerData);

    // Zoom to show the last 50 candles (readable width)
    const visibleBarCount = Math.min(cleanData.length, 50);
    if (visibleBarCount > 0) {
      chart.timeScale().setVisibleLogicalRange({
        from: cleanData.length - visibleBarCount,
        to: cleanData.length + 3, // Right-side margin
      });
    } else {
      chart.timeScale().fitContent();
    }

    // 7. Handle Resizing with minimum boundary checks (avoid 0 width crashes)
    const handleResize = () => {
      if (chartContainerRef.current && chartRef.current) {
        const currentWidth = chartContainerRef.current.clientWidth;
        if (currentWidth > 100) {
          chartRef.current.applyOptions({
            width: currentWidth,
          });
        }
      }
    };

    const resizeObserver = new ResizeObserver(handleResize);
    resizeObserver.observe(chartContainerRef.current);

    // Cleanup on unmount
    return () => {
      resizeObserver.disconnect();
      chart.remove();
    };
  }, [data]);

  const hasData = data && data.length > 0;

  return (
    <div style={{ position: 'relative', width: '100%', height: '400px' }}>
      {!hasData ? (
        <div className="loader-wrapper" style={{ height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)' }}>
          <div className="spinner" style={{ marginBottom: '0.75rem' }}></div>
          <p style={{ fontSize: '0.85rem' }}>正在获取 K 线技术指标行情数据...</p>
        </div>
      ) : (
        <>
          {/* Legend overlay */}
          <div className="chart-legend">
            <div className="legend-item">
              <div className="legend-color-dot" style={{ backgroundColor: '#ff9500' }}></div>
              <span>MA5</span>
            </div>
            <div className="legend-item">
              <div className="legend-color-dot" style={{ backgroundColor: '#007aff' }}></div>
              <span>MA10</span>
            </div>
            <div className="legend-item">
              <div className="legend-color-dot" style={{ backgroundColor: '#af52de' }}></div>
              <span>MA30</span>
            </div>
            <div className="legend-item">
              <div className="legend-color-dot" style={{ backgroundColor: '#ff3b30' }}></div>
              <span>EMA55</span>
            </div>
            <div className="legend-item">
              <div className="legend-color-dot" style={{ backgroundColor: 'rgba(142, 142, 147, 0.6)' }}></div>
              <span>BB (20, 2)</span>
            </div>
          </div>
          <div ref={chartContainerRef} style={{ width: '100%', height: '100%' }} />
        </>
      )}
    </div>
  );
}
