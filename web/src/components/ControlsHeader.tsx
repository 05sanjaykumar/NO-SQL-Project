import { useState } from 'react';
import type { DetectorInfo, LiveConfig, Scenario, Status } from '../types';
import { fmt } from '../api';

interface Props {
  status: Status | null;
  connected: boolean;
  scenarios: Scenario[];
  catalog: { ring_types: string[]; evasion_modes: string[] };
  detectors: DetectorInfo[];
  config: LiveConfig | null;
  oracle: boolean;
  onStart: (scenario: string, seed: number, speed: number) => void;
  onPause: () => void;
  onResume: () => void;
  onStop: () => void;
  onSpeed: (speed: number) => void;
  onInject: (ringType: string, evasion: string, size: number) => void;
  onDetector: (type: string, enabled: boolean) => void;
  onConfig: (changes: Partial<LiveConfig>) => void;
  onOracle: (on: boolean) => void;
  onExport: (kind: 'png' | 'svg') => void;
}

const SPEEDS = [1, 2, 5, 10, 25, 0];

export function ControlsHeader(p: Props) {
  const [scenario, setScenario] = useState('demo_story');
  const [seed, setSeed] = useState(7);
  const [speed, setSpeed] = useState(5);
  const [ringType, setRingType] = useState('shared_ip');
  const [evasion, setEvasion] = useState('hub_hiding');
  const [size, setSize] = useState(4);
  const running = p.status?.status === 'running';
  const paused = p.status?.status === 'paused';
  const live = running || paused;

  return (
    <header className="controls">
      <div className="brand">
        <span className={`dot ${p.connected ? 'on' : ''}`} title={p.connected ? 'connected' : 'disconnected'} />
        <div>
          <div className="title">FraudSim live</div>
          <div className="sub">
            {p.status ? `run ${p.status.run_id} · ${p.status.scenario} · seed ${p.status.seed} · ${p.status.status}` : 'no run'}
          </div>
        </div>
      </div>

      <div className="group">
        <select value={scenario} onChange={(e) => setScenario(e.target.value)} aria-label="Scenario">
          {p.scenarios.map((s) => (
            <option key={s.name} value={s.name} title={s.description}>
              {s.name} ({s.users} users, {s.duration_s}s)
            </option>
          ))}
        </select>
        <label className="mini">seed
          <input type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} style={{ width: 64 }} />
        </label>
        <button className="btn primary" onClick={() => p.onStart(scenario, seed, speed)}>{live ? 'Restart' : 'Start'}</button>
        {running && <button className="btn" onClick={p.onPause}>Pause</button>}
        {paused && <button className="btn" onClick={p.onResume}>Resume</button>}
        {live && <button className="btn" onClick={p.onStop}>Stop</button>}
      </div>

      <div className="group">
        <span className="lbl">speed</span>
        <div className="seg">
          {SPEEDS.map((s) => (
            <button key={s} className={speed === s ? 'on' : ''} onClick={() => { setSpeed(s); if (live) p.onSpeed(s); }}>
              {s === 0 ? 'max' : `${s}×`}
            </button>
          ))}
        </div>
        <span className="clock">t = {fmt(p.status?.sim_time_s, 1)} s · {p.status?.events_emitted ?? 0} events</span>
      </div>

      <div className="group">
        <span className="lbl">inject</span>
        <select value={ringType} onChange={(e) => setRingType(e.target.value)} aria-label="Ring type">
          {p.catalog.ring_types.map((t) => <option key={t}>{t}</option>)}
        </select>
        <select value={evasion} onChange={(e) => setEvasion(e.target.value)} aria-label="Evasion">
          {p.catalog.evasion_modes.map((t) => <option key={t}>{t}</option>)}
        </select>
        <input type="number" min={2} max={12} value={size} onChange={(e) => setSize(Number(e.target.value))} style={{ width: 48 }} aria-label="Ring size" />
        <button className="btn" disabled={!live} onClick={() => p.onInject(ringType, evasion, size)}>Inject ring</button>
      </div>

      <div className="group wide">
        <span className="lbl">detectors</span>
        {p.detectors.map((d) => (
          <label key={d.type} className="chk" title={d.incremental ? 'incremental' : 'full graph, on a timer'}>
            <input type="checkbox" checked={d.enabled} disabled={!live} onChange={(e) => p.onDetector(d.type, e.target.checked)} />
            {d.type.replace('_', ' ')}
          </label>
        ))}
      </div>

      <div className="group wide">
        <label className="slider">alert threshold <b>{fmt(p.config?.alert_threshold)}</b>
          <input type="range" min={0.05} max={0.95} step={0.05} disabled={!live || !p.config?.model}
            value={p.config?.alert_threshold ?? 0.5}
            onChange={(e) => p.onConfig({ alert_threshold: Number(e.target.value) })} />
        </label>
        <label className="slider">hub edge threshold <b>{fmt(p.config?.edge_threshold)}</b>
          <input type="range" min={0.2} max={1.5} step={0.05} disabled={!live}
            value={p.config?.edge_threshold ?? 0.5}
            onChange={(e) => p.onConfig({ edge_threshold: Number(e.target.value) })} />
        </label>
        <select value={p.config?.hub_weight_method ?? 'inverse_log_degree'} disabled={!live}
          onChange={(e) => p.onConfig({ hub_weight_method: e.target.value })} aria-label="Hub weighting">
          <option value="inverse_log_degree">1/ln(1+deg)</option>
          <option value="adamic_adar">Adamic-Adar</option>
          <option value="inverse_degree">1/(deg-1)</option>
          <option value="none">no hub weighting</option>
        </select>
        <label className="chk oracle">
          <input type="checkbox" checked={p.oracle} onChange={(e) => p.onOracle(e.target.checked)} /> Oracle view
        </label>
        <button className="btn" onClick={() => p.onExport('png')}>PNG</button>
        <button className="btn" onClick={() => p.onExport('svg')}>SVG</button>
      </div>
    </header>
  );
}
