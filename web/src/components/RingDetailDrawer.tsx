import React from 'react';
import { RingDetection } from '../types';

interface RingDetailDrawerProps {
  ring: RingDetection | null;
  onClose: () => void;
  onFeedback: (ringId: string, action: 'confirm' | 'dismiss') => void;
}

export const RingDetailDrawer: React.FC<RingDetailDrawerProps> = ({
  ring,
  onClose,
  onFeedback,
}) => {
  if (!ring) return null;

  return (
    <div
      className="glass-panel"
      style={{
        position: 'absolute',
        top: 80,
        right: 24,
        bottom: 24,
        width: 380,
        padding: 24,
        display: 'flex',
        flexDirection: 'column',
        gap: 18,
        zIndex: 40,
        overflowY: 'auto',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <span className={`badge badge-${ring.status}`}>{ring.status}</span>
          <h2 style={{ fontSize: 18, fontWeight: 700, marginTop: 6 }}>{ring.ring_id}</h2>
        </div>
        <button className="btn" onClick={onClose} style={{ padding: '4px 8px' }}>
          ✕
        </button>
      </div>

      <div style={{ background: 'rgba(0,0,0,0.25)', padding: 12, borderRadius: 8 }}>
        <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>Risk Score</div>
        <div style={{ fontSize: 28, fontWeight: 700, color: ring.score > 0.8 ? 'var(--accent-rose)' : 'var(--accent-amber)' }}>
          {(ring.score * 100).toFixed(0)} / 100
        </div>
        <div style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 4 }}>
          Confidence: {ring.explanation?.confidence_bucket || 'High (calibrated)'}
        </div>
      </div>

      <div>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-muted)', marginBottom: 6 }}>
          Plain-English Reason
        </div>
        <div
          style={{
            fontSize: 13,
            lineHeight: 1.5,
            background: 'rgba(255,255,255,0.03)',
            padding: 12,
            borderRadius: 8,
            border: '1px solid var(--border-subtle)',
          }}
        >
          {ring.explanation?.plain_english ||
            `${ring.members.length} users share infrastructure with high hub-adjusted density.`}
        </div>
      </div>

      <div>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-muted)', marginBottom: 6 }}>
          Members ({ring.members.length})
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {ring.members.map((m) => (
            <span
              key={m}
              style={{
                background: 'rgba(99, 102, 241, 0.15)',
                color: '#a5b4fc',
                padding: '3px 8px',
                borderRadius: 4,
                fontFamily: 'var(--font-mono)',
                fontSize: 12,
              }}
            >
              {m}
            </span>
          ))}
        </div>
      </div>

      {ring.explanation?.counterfactual && (
        <div>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-muted)', marginBottom: 6 }}>
            Counterfactual Explanation
          </div>
          <div
            style={{
              fontSize: 12,
              lineHeight: 1.4,
              color: 'var(--accent-cyan)',
              background: 'rgba(6, 182, 212, 0.08)',
              padding: 10,
              borderRadius: 6,
              border: '1px solid rgba(6, 182, 212, 0.2)',
            }}
          >
            {ring.explanation.counterfactual}
          </div>
        </div>
      )}

      <div style={{ marginTop: 'auto', display: 'flex', gap: 10, paddingTop: 12 }}>
        <button
          className="btn btn-primary"
          style={{ flex: 1, justifyContent: 'center' }}
          onClick={() => onFeedback(ring.ring_id, 'confirm')}
        >
          ✓ Confirm Ring
        </button>
        <button
          className="btn btn-danger"
          style={{ flex: 1, justifyContent: 'center' }}
          onClick={() => onFeedback(ring.ring_id, 'dismiss')}
        >
          ✕ Dismiss
        </button>
      </div>
    </div>
  );
};
