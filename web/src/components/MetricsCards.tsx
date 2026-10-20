import React from 'react';
import { MetricsSummary } from '../types';

interface MetricsCardsProps {
  metrics: MetricsSummary;
}

export const MetricsCards: React.FC<MetricsCardsProps> = ({ metrics }) => {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 12, padding: '12px 24px' }}>
      <div className="glass-panel" style={{ padding: '12px 16px' }}>
        <div style={{ fontSize: 11, color: 'var(--text-dim)', textTransform: 'uppercase', fontWeight: 600 }}>Precision</div>
        <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--accent-emerald)', marginTop: 4 }}>
          {(metrics.precision * 100).toFixed(1)}%
        </div>
      </div>

      <div className="glass-panel" style={{ padding: '12px 16px' }}>
        <div style={{ fontSize: 11, color: 'var(--text-dim)', textTransform: 'uppercase', fontWeight: 600 }}>Recall</div>
        <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--accent-cyan)', marginTop: 4 }}>
          {(metrics.recall * 100).toFixed(1)}%
        </div>
      </div>

      <div className="glass-panel" style={{ padding: '12px 16px' }}>
        <div style={{ fontSize: 11, color: 'var(--text-dim)', textTransform: 'uppercase', fontWeight: 600 }}>F1-Score</div>
        <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--accent-violet)', marginTop: 4 }}>
          {(metrics.f1 * 100).toFixed(1)}%
        </div>
      </div>

      <div className="glass-panel" style={{ padding: '12px 16px' }}>
        <div style={{ fontSize: 11, color: 'var(--text-dim)', textTransform: 'uppercase', fontWeight: 600 }}>Latency (p95)</div>
        <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--accent-amber)', marginTop: 4 }}>
          {metrics.latency_p95_ms.toFixed(1)} <span style={{ fontSize: 12, fontWeight: 400 }}>ms</span>
        </div>
      </div>

      <div className="glass-panel" style={{ padding: '12px 16px' }}>
        <div style={{ fontSize: 11, color: 'var(--text-dim)', textTransform: 'uppercase', fontWeight: 600 }}>Throughput</div>
        <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--text-main)', marginTop: 4 }}>
          {metrics.throughput_events_per_sec.toFixed(0)} <span style={{ fontSize: 12, fontWeight: 400 }}>evt/s</span>
        </div>
      </div>

      <div className="glass-panel glow-rose" style={{ padding: '12px 16px' }}>
        <div style={{ fontSize: 11, color: 'var(--text-dim)', textTransform: 'uppercase', fontWeight: 600 }}>Active Rings</div>
        <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--accent-rose)', marginTop: 4 }}>
          {metrics.active_rings}
        </div>
      </div>
    </div>
  );
};
