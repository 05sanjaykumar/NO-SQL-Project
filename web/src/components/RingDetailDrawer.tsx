import { useEffect, useRef, useState } from 'react';
import cytoscape from 'cytoscape';
import type { Oracle, Ring } from '../types';
import { FEATURE_LABELS, fmt, getJSON, postJSON } from '../api';

interface Props {
  ringId: string;
  oracle: Oracle | null | undefined;
  showOracle: boolean;
  onClose: () => void;
}

const css = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

export function RingDetailDrawer({ ringId, oracle, showOracle, onClose }: Props) {
  const [ring, setRing] = useState<Ring | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const evidenceRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      getJSON<Ring>(`/rings/${ringId}`)
        .then((r) => alive && setRing(r))
        .catch((e) => alive && setError(String(e)));
    load();
    const id = window.setInterval(load, 2000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [ringId]);

  const ev = ring?.explanation.evidence_subgraph;
  const evKey = ev ? ev.nodes.length + ':' + ev.edges.length : '';
  useEffect(() => {
    if (!ev || !evidenceRef.current) return;
    const cy = cytoscape({
      container: evidenceRef.current,
      userZoomingEnabled: false,
      elements: [
        ...ev.nodes.map((n) => ({ data: { id: n.id, kind: n.type, label: n.type === 'user' ? n.id : `${n.id} (${n.degree})` } })),
        ...ev.edges.map((e, i) => ({ data: { id: `e${i}`, source: e.source, target: e.target, kind: e.type } })),
      ],
      style: [
        { selector: 'node', style: { 'background-color': css('--fraud'), width: 12, height: 12, label: 'data(label)', 'font-size': 9, color: css('--ink'), 'text-valign': 'bottom', 'text-margin-y': 3 } },
        { selector: 'node[kind = "device"]', style: { shape: 'round-rectangle', 'background-color': css('--device'), width: 16, height: 16 } },
        { selector: 'node[kind = "card"]', style: { shape: 'diamond', 'background-color': css('--card'), width: 16, height: 16 } },
        { selector: 'node[kind = "ip"]', style: { shape: 'hexagon', 'background-color': css('--ip'), width: 18, height: 18 } },
        { selector: 'edge', style: { width: 1, 'line-color': css('--line'), 'curve-style': 'haystack' } as any },
        { selector: 'edge[kind = "transfer"]', style: { 'line-color': css('--fraud'), 'curve-style': 'bezier', 'target-arrow-shape': 'triangle', 'target-arrow-color': css('--fraud'), 'arrow-scale': 0.6, opacity: 0.6 } as any },
      ],
      layout: { name: 'cose', animate: false, padding: 14 } as any,
    });
    return () => cy.destroy();
  }, [evKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const send = async (action: 'confirm' | 'dismiss') => {
    try {
      const r = await postJSON<{ threshold_adaptation: Record<string, number> | null }>(
        `/rings/${ringId}/feedback`, { action, analyst_id: 'dashboard' });
      const a = r.threshold_adaptation;
      setFeedback(a ? `${action}ed · ${a.ring_type ?? ''} threshold ${fmt(a.threshold_before)} → ${fmt(a.threshold_after)}` : `${action}ed`);
    } catch (e) {
      setFeedback(String(e));
    }
  };

  const ex = ring?.explanation;
  const contribs = Object.entries(ex?.feature_contributions ?? {}).slice(0, 8);
  const maxAbs = Math.max(0.01, ...contribs.map(([, v]) => Math.abs(v)));
  const verdict = oracle?.tracked[ringId];

  return (
    <aside className="drawer" role="dialog" aria-label={`Ring ${ringId}`}>
      <div className="drawer-h">
        <div>
          <div className="muted small">ring {ringId}{ring ? ` · ${ring.ring_type.replace(/_/g, ' ')} · ${ring.members.length} members` : ''}</div>
          <div className="risk-big" style={{ color: ex?.alerted_at ? 'var(--alert)' : 'var(--warn)' }}>
            {ex?.risk != null ? fmt(ex.risk) : fmt(ring?.score)}
          </div>
          <div className="muted small">
            {ex?.risk != null ? `calibrated risk · threshold ${fmt(ex.threshold)} · ${ex.calibration_bucket ?? ''}` : 'detector score (no risk model)'}
          </div>
        </div>
        <button className="btn" onClick={onClose} aria-label="Close">✕</button>
      </div>
      {error && <div className="error">{error}</div>}
      {ring && (
        <>
          <div className="badges">
            <span className={`pill ${ex?.alerted_at ? 'alert' : ring.status}`}>{ex?.alerted_at ? 'alert' : ring.status}</span>
            {(ex?.detectors ?? []).map((d) => <span key={d} className="pill det">{d.replace('_', ' ')}</span>)}
            {showOracle && verdict && (
              <span className={`tag ${verdict.verdict === 'true' ? 'ok' : 'fp'}`}>
                {verdict.verdict === 'true' ? `true ring ${verdict.true_ring} (J ${fmt(verdict.jaccard)})` : 'false alarm'}
              </span>
            )}
          </div>
          <h4>Why it was flagged</h4>
          <p className="reason">{ex?.reason_text ?? 'No explanation yet.'}</p>
          {ex?.counterfactual && <div className="cf"><b>Counterfactual.</b> {ex.counterfactual}</div>}
          {contribs.length > 0 && (
            <>
              <h4>What moved the score</h4>
              <div className="bars">
                {contribs.map(([k, v]) => {
                  const w = (Math.abs(v) / maxAbs) * 50;
                  return (
                    <div className="row" key={k}>
                      <span title={k}>{FEATURE_LABELS[k] ?? k}</span>
                      <span className="track"><span className="mid" />
                        <span className="b" style={{ left: `${v >= 0 ? 50 : 50 - w}%`, width: `${w}%`, background: v >= 0 ? 'var(--fraud)' : 'var(--ok)' }} />
                      </span>
                      <span className="v">{v >= 0 ? '+' : ''}{fmt(v)}</span>
                    </div>
                  );
                })}
              </div>
            </>
          )}
          <h4>Evidence subgraph</h4>
          <div ref={evidenceRef} className="evidence" />
          <h4>Timeline</h4>
          <ul className="timeline">
            {((ring.explanation as any).history ?? []).slice(-12).map((h: any, i: number) => (
              <li key={i}><span className="seq">{String(h.ts).slice(11, 19)}</span> {h.change}{h.risk != null ? ` · risk ${fmt(h.risk)}` : ''}{h.added ? ` +${h.added.length}` : ''}{h.removed ? ` −${h.removed.length}` : ''}</li>
            ))}
          </ul>
          <div className="actions">
            <button className="btn primary" onClick={() => send('confirm')}>Confirm fraud</button>
            <button className="btn" onClick={() => send('dismiss')}>Dismiss</button>
          </div>
          {feedback && <div className="muted small">{feedback} (logged to the audit trail)</div>}
          <div className="muted small members">members: {ring.members.join(', ')}</div>
        </>
      )}
    </aside>
  );
}
