import type { BatchMsg } from '../types';

type Ev = BatchMsg['events'][number];

function describe(e: Ev): string {
  const p = e.payload as Record<string, string | number>;
  switch (e.event_type) {
    case 'transaction':
      return `${p.sender} → ${p.receiver}  ${Number(p.amount).toFixed(2)}`;
    case 'user_join':
      return `${p.user_id} joined`;
    case 'user_leave':
      return `${p.user_id} left`;
    case 'device_link':
      return `${p.user_id} uses ${p.device_id}`;
    case 'card_link':
      return `${p.user_id} has ${p.card_id}`;
    case 'ip_link':
      return `${p.user_id} logged in from ${p.ip}`;
    case 'attribute_unlink':
      return `${p.user_id} dropped ${p.attr_id}`;
    default:
      return JSON.stringify(p);
  }
}

export function EventLog({ events, notices }: { events: Ev[]; notices: { t: number; text: string; kind: string }[] }) {
  return (
    <section className="panel log">
      <div className="panel-h">
        <h3>Event log</h3>
        <span className="hint">latest events and ring changes</span>
      </div>
      <div className="log-cols">
        <ul>
          {events.slice(-60).reverse().map((e) => (
            <li key={e.event_id}>
              <span className="seq">#{e.seq}</span>
              <span className={`et ${e.event_type}`}>{e.event_type.replace('_', ' ')}</span>
              <span className="txt">{describe(e)}</span>
            </li>
          ))}
        </ul>
        <ul>
          {notices.slice(-60).reverse().map((n, i) => (
            <li key={i} className={n.kind}>
              <span className="seq">{n.t.toFixed(1)}s</span>
              <span className="txt">{n.text}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
