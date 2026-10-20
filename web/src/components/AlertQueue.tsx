import type { Oracle, Ring } from '../types';
import { fmt } from '../api';

interface Props {
  rings: Ring[];
  oracle: Oracle | null | undefined;
  showOracle: boolean;
  selected: string | null;
  onSelect: (id: string) => void;
}

/** Live rings ranked by calibrated risk (alerts first). */
export function AlertQueue({ rings, oracle, showOracle, selected, onSelect }: Props) {
  const live = rings
    .filter((r) => r.status !== 'dissolved')
    .sort((a, b) => {
      const aa = a.explanation.alerted_at ? 1 : 0, ba = b.explanation.alerted_at ? 1 : 0;
      if (aa !== ba) return ba - aa;
      return (b.explanation.risk ?? b.score) - (a.explanation.risk ?? a.score);
    });
  return (
    <section className="panel queue">
      <div className="panel-h">
        <h3>Alert queue</h3>
        <span className="hint">{live.length} live rings · ranked by risk</span>
      </div>
      <ul>
        {live.slice(0, 40).map((r) => {
          const verdict = oracle?.tracked[r.ring_id];
          const risk = r.explanation.risk;
          const state = r.explanation.alerted_at ? 'alert' : r.status;
          return (
            <li key={r.ring_id} className={selected === r.ring_id ? 'sel' : ''} onClick={() => onSelect(r.ring_id)}>
              <span className={`pill ${state}`}>{state}</span>
              <span className="rid">{r.ring_id}</span>
              <span className="rtype">{r.ring_type.replace(/_/g, ' ')} · {r.members.length}</span>
              <span className="risk">{risk != null ? fmt(risk) : '–'}</span>
              {showOracle && verdict && (
                <span className={`tag ${verdict.verdict === 'true' ? 'ok' : 'fp'}`}>
                  {verdict.verdict === 'true' ? verdict.true_ring : 'false alarm'}
                </span>
              )}
            </li>
          );
        })}
        {!live.length && <li className="empty">No rings yet.</li>}
      </ul>
    </section>
  );
}
