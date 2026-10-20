// REST + WebSocket client. In development Vite proxies /api and /ws to :8000;
// in production the dashboard is served by FastAPI itself (same origin, at /ui).

export const API_BASE = import.meta.env.DEV ? '/api' : '';

export async function getJSON<T>(path: string): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`);
  if (!r.ok) throw new Error(`${path}: ${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

export async function postJSON<T>(path: string, body: unknown = {}): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${path}: ${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

export function wsUrl(): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${location.host}/ws`;
}

/** Reconnecting WebSocket; calls onMessage for every parsed JSON message. */
export function connectWs(onMessage: (msg: any) => void, onState: (open: boolean) => void): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let retry: number | undefined;
  const open = () => {
    ws = new WebSocket(wsUrl());
    ws.onopen = () => onState(true);
    ws.onmessage = (e) => {
      try {
        onMessage(JSON.parse(e.data));
      } catch {
        /* ignore malformed */
      }
    };
    ws.onclose = () => {
      onState(false);
      if (!closed) retry = window.setTimeout(open, 1500);
    };
  };
  open();
  return () => {
    closed = true;
    if (retry) window.clearTimeout(retry);
    ws?.close();
  };
}

export const fmt = (x: number | null | undefined, d = 2): string =>
  x === null || x === undefined || Number.isNaN(x) ? '–' : Number(x).toFixed(d);

export const pct = (x: number | null | undefined): string =>
  x === null || x === undefined ? '–' : `${Math.round(x * 100)}%`;

export const FEATURE_LABELS: Record<string, string> = {
  size: 'ring size',
  density: 'pairs directly linked',
  share_device: 'share one device',
  share_card: 'share one card',
  share_ip: 'share one IP',
  hub_adjusted_weight: 'hub-adjusted sharing weight',
  cycle_present: 'money returns to origin',
  cycle_length: 'cycle length',
  temporal_burstiness: 'linked within a short burst',
  account_age_similarity: 'similar account ages',
  account_age_min: 'age of oldest account',
  internal_txns_per_member: 'transfers between members',
  amount_mean_log: 'typical transfer size',
  amount_cv: 'variation of amounts',
  near_threshold_frac: 'just under 10k threshold',
  growth_rate: 'members added per minute',
  risk_signal_fraction: 'also in another flagged group',
  hub_exposure: 'busiest shared attribute',
  n_detectors: 'detectors agreeing',
};
