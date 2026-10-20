import type { MetricsSummaryFull, Oracle } from '../types';
import { fmt, pct } from '../api';

interface Props {
  summary: MetricsSummaryFull | null;
  oracle: Oracle | null | undefined;
  showOracle: boolean;
  graphInfo: { shown: number; total: number; links?: number; txns?: number };
}

export function MetricsCards({ summary, oracle, showOracle, graphInfo }: Props) {
  const s = summary;
  const cards: [string, string, string?][] = [
    ['rings detected', s ? `${s.rings_detected}/${s.true_rings}` : '–', 'good'],
    ['rings missed', s ? String(s.rings_missed) : '–', s && s.rings_missed ? 'bad' : ''],
    ['false rings', s ? String(s.false_positive_rings) : '–', s && s.false_positive_rings ? 'bad' : ''],
    ['precision', pct(s?.ring.precision)],
    ['recall', pct(s?.ring.recall)],
    ['F1', fmt(s?.ring.f1)],
    ['time to detect p50', s?.time_to_detect_s.p50 != null ? `${fmt(s.time_to_detect_s.p50, 1)} s` : '–'],
    ['graph', `${graphInfo.shown}/${graphInfo.total} users`],
  ];
  return (
    <section className="panel">
      <div className="panel-h">
        <h3>Scoreboard</h3>
        <span className="hint">vs simulator ground truth</span>
      </div>
      <div className="cards">
        {cards.map(([label, value, cls]) => (
          <div key={label} className={`card ${cls ?? ''}`}>
            <b>{value}</b>
            <span>{label}</span>
          </div>
        ))}
      </div>
      {showOracle && oracle && (
        <ul className="truth">
          {oracle.true_rings.map((r) => (
            <li key={r.true_ring}>
              <span>
                {r.true_ring} <i>{r.type}{r.evasion !== 'none' ? ` · ${r.evasion}` : ''}</i>
              </span>
              {r.detected ? (
                <span className="tag ok">caught {fmt(r.time_to_detect_s, 1)} s after activation</span>
              ) : (
                <span className="tag miss">missed · {r.miss_reason?.replace(/_/g, ' ')}</span>
              )}
            </li>
          ))}
          {oracle.spawned_not_active.length > 0 && (
            <li className="muted">forming (not active yet): {oracle.spawned_not_active.join(', ')}</li>
          )}
        </ul>
      )}
    </section>
  );
}
