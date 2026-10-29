import { useCallback, useEffect, useRef, useState } from 'react';
import { ControlsHeader } from './components/ControlsHeader';
import { MetricsCards } from './components/MetricsCards';
import { GraphHandle, GraphView } from './components/GraphView';
import { RingDetailDrawer } from './components/RingDetailDrawer';
import { AlertQueue } from './components/AlertQueue';
import { EventLog } from './components/EventLog';
import { LatencyChart, LatencyPoint, ThroughputChart } from './components/Charts';
import { connectWs, getJSON, postJSON } from './api';
import type {
  BatchMsg, DetectorInfo, GraphSnapshot, LiveConfig, MetricsSummaryFull, Ring, RingUpdateMsg,
  Scenario, Status,
} from './types';
import AskPanel from './components/AskPanel';

const EMPTY: GraphSnapshot = { nodes: [], edges: [], shown_users: 0, total_active_users: 0 };

function pctOf(xs: number[], p: number): number {
  if (!xs.length) return 0;
  const s = [...xs].sort((a, b) => a - b);
  const k = (s.length - 1) * p;
  const lo = Math.floor(k), hi = Math.ceil(k);
  return +(s[lo] + (s[hi] - s[lo]) * (k - lo)).toFixed(2);
}

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [connected, setConnected] = useState(false);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [catalog, setCatalog] = useState({ ring_types: [] as string[], evasion_modes: [] as string[] });
  const [detectors, setDetectors] = useState<DetectorInfo[]>([]);
  const [config, setConfig] = useState<LiveConfig | null>(null);
  const [oracle, setOracle] = useState(false);
  const [snapshot, setSnapshot] = useState<GraphSnapshot>(EMPTY);
  const [rings, setRings] = useState<Ring[]>([]);
  const [summary, setSummary] = useState<MetricsSummaryFull | null>(null);
  const [series, setSeries] = useState<LatencyPoint[]>([]);
  const [events, setEvents] = useState<BatchMsg['events']>([]);
  const [notices, setNotices] = useState<{ t: number; text: string; kind: string }[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const graph = useRef<GraphHandle>(null);
  const detectMs = useRef<number[]>([]);
  const simT = useRef(0);

  const refreshStatus = useCallback(() => getJSON<Status>('/status').then(setStatus).catch(() => undefined), []);
  const refreshLive = useCallback(() => {
    getJSON<DetectorInfo[]>('/detectors').then(setDetectors).catch(() => undefined);
    getJSON<LiveConfig>('/config').then(setConfig).catch(() => undefined);
  }, []);

  useEffect(() => {
    getJSON<Scenario[]>('/scenarios').then(setScenarios).catch((e) => setError(String(e)));
    getJSON<typeof catalog>('/catalog').then(setCatalog).catch(() => undefined);
    refreshStatus();
    refreshLive();
    const stop = connectWs((msg) => {
      if (msg.type === 'BATCH') {
        const b = msg as BatchMsg;
        simT.current = b.sim_time_s;
        const det = (b.timings_ms.dirty ?? 0) + (b.timings_ms.view ?? 0) + (b.timings_ms.full_view ?? 0)
          + Object.values(b.detector_ms).reduce((a, x) => a + x, 0) + (b.timings_ms.track ?? 0) + (b.timings_ms.score ?? 0);
        detectMs.current = [...detectMs.current.slice(-29), det];
        setSeries((s) => [...s.slice(-299), {
          t: +b.sim_time_s.toFixed(1),
          p50: pctOf(detectMs.current, 0.5), p95: pctOf(detectMs.current, 0.95), p99: pctOf(detectMs.current, 0.99),
          eps: Math.round(b.wall_throughput_eps), users: b.graph.active_users ?? 0, region: b.region,
        }]);
        setEvents((e) => [...e.slice(-200), ...b.events]);
      } else if (msg.type === 'METRICS') {
        if (msg.summary && Object.keys(msg.summary).length) setSummary(msg.summary);
      } else if (msg.type === 'RING_UPDATE') {
        const u = (msg as RingUpdateMsg).update;
        if (['alert', 'early_warning', 'confirmed', 'dissolved', 'created'].includes(u.change)) {
          const text = u.change === 'alert'
            ? `ALERT ${u.ring_id} · ${u.members.length} users · risk ${u.score.toFixed(2)}`
            : `${u.change.replace('_', ' ')} ${u.ring_id} · ${u.members.length} users`;
          setNotices((n) => [...n.slice(-200), { t: simT.current, text, kind: u.change }]);
        }
      } else if (msg.type === 'RING_INJECTED') {
        setNotices((n) => [...n, { t: simT.current, text: `injected ${msg.ring_type} (${msg.evasion}, ${msg.size}) as ${msg.ring_id}`, kind: 'inject' }]);
      } else if (['RUN_STARTED', 'RUN_PAUSED', 'RUN_RESUMED', 'RUN_STOPPED', 'RUN_SPEED'].includes(msg.type)) {
        refreshStatus();
      }
    }, setConnected);
    return stop;
  }, [refreshStatus, refreshLive]);

  // Poll the windowed graph, the ring list and the status while a run exists.
  useEffect(() => {
    const tick = () => {
      refreshStatus();
      getJSON<GraphSnapshot>(`/graph?max_users=180&window_s=30&oracle=${oracle}`).then(setSnapshot).catch(() => undefined);
      getJSON<Ring[]>('/rings').then(setRings).catch(() => undefined);
    };
    tick();
    const id = window.setInterval(tick, 1200);
    return () => window.clearInterval(id);
  }, [oracle, refreshStatus]);

  const act = async (fn: () => Promise<unknown>) => {
    try {
      setError(null);
      await fn();
    } catch (e) {
      setError(String(e));
    }
    refreshStatus();
    refreshLive();
  };

  return (
    <div className="app">
      <ControlsHeader
        status={status}
        connected={connected}
        scenarios={scenarios}
        catalog={catalog}
        detectors={detectors}
        config={config}
        oracle={oracle}
        onStart={(scenario, seed, speed) => act(async () => {
          setSeries([]); setEvents([]); setNotices([]); setSummary(null); setSelected(null); detectMs.current = [];
          await postJSON('/runs/start', { scenario, seed, speed_multiplier: speed });
        })}
        onPause={() => act(() => postJSON('/runs/pause'))}
        onResume={() => act(() => postJSON('/runs/resume'))}
        onStop={() => act(() => postJSON('/runs/stop'))}
        onSpeed={(s) => act(() => postJSON('/runs/speed', { speed_multiplier: s }))}
        onInject={(t, e, n) => act(() => postJSON('/runs/inject', { ring_type: t, evasion: e, size: n }))}
        onDetector={(t, en) => act(() => postJSON(`/detectors/${t}`, { enabled: en }))}
        onConfig={(c) => act(() => postJSON('/config', c))}
        onOracle={setOracle}
        onExport={(k) => (k === 'png' ? graph.current?.exportPng() : graph.current?.exportSvg())}
      />
      {error && <div className="error banner" onClick={() => setError(null)}>{error}</div>}
      <main className="grid">
        <section className="panel graph-panel">
          <div className="panel-h">
            <h3>Transaction graph</h3>
            <span className="hint">
              {snapshot.shown_users}/{snapshot.total_active_users} active users shown (ring members, hubs and the last {snapshot.window_s ?? 45}s of activity) · click a flagged user for its ring
            </span>
            <button className="btn small" onClick={() => graph.current?.fit()}>fit</button>
          </div>
          <GraphView ref={graph} snapshot={snapshot} oracle={oracle} selectedRing={selected} onSelectRing={setSelected} />
          <div className="legend">
            {oracle ? (
              <>
                <span><i style={{ background: 'var(--fraud)' }} />fraud, flagged</span>
                <span><i style={{ background: 'var(--warn)' }} />legitimate, flagged (false alarm)</span>
                <span><i style={{ border: '2px solid var(--fraud)' }} />fraud, not flagged (missed)</span>
                <span><i style={{ background: 'var(--benign)' }} />legitimate</span>
              </>
            ) : (
              <>
                <span><i style={{ background: 'var(--alert)' }} />alert</span>
                <span><i style={{ background: 'var(--confirmed)' }} />confirmed ring</span>
                <span><i style={{ border: '2px solid var(--candidate)' }} />candidate</span>
                <span><i style={{ background: 'var(--benign)' }} />user</span>
              </>
            )}
            <span><i className="sq" style={{ background: 'var(--device)' }} />device</span>
            <span><i className="sq" style={{ background: 'var(--card)' }} />card</span>
            <span><i className="sq" style={{ background: 'var(--ip)' }} />IP</span>
            <span><i style={{ background: 'var(--hub)' }} />busy hub (≥10 users)</span>
            <span style={{ color: 'var(--fraud)' }}>→ transfer inside a flagged ring</span>
          </div>
        </section>
        <div className="side">
          <MetricsCards summary={summary} oracle={snapshot.oracle} showOracle={oracle}
            graphInfo={{ shown: snapshot.shown_users, total: snapshot.total_active_users }} />
          <AlertQueue rings={rings} oracle={snapshot.oracle} showOracle={oracle} selected={selected} onSelect={setSelected} />
          <AskPanel />
        </div>
        <LatencyChart data={series} />
        <ThroughputChart data={series} />
        <EventLog events={events} notices={notices} />
      </main>
      {selected && (
        <RingDetailDrawer ringId={selected} oracle={snapshot.oracle} showOracle={oracle} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}
