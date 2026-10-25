import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

export interface LatencyPoint {
  t: number;
  p50: number;
  p95: number;
  p99: number;
  eps: number;
  users: number;
  region: number;
}

const color = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

export function LatencyChart({ data }: { data: LatencyPoint[] }) {
  return (
    <section className="panel">
      <div className="panel-h">
        <h3>Detection latency per batch</h3>
        <span className="hint">rolling p50 / p95 / p99 over the last 30 batches (ms, wall clock)</span>
      </div>
      <div className="chart">
        <ResponsiveContainer width="100%" height={170}>
          <LineChart data={data} margin={{ top: 6, right: 12, bottom: 0, left: -12 }}>
            <CartesianGrid stroke={color('--line')} strokeDasharray="3 3" />
            <XAxis dataKey="t" tick={{ fontSize: 11, fill: color('--muted') }} unit="s" type="number" domain={['dataMin', 'dataMax']} />
            <YAxis tick={{ fontSize: 11, fill: color('--muted') }} />
            <Tooltip contentStyle={{ background: color('--panel'), border: `1px solid ${color('--line')}`, fontSize: 12 }} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Line type="monotone" dataKey="p50" stroke={color('--ok')} dot={false} isAnimationActive={false} />
            <Line type="monotone" dataKey="p95" stroke={color('--warn')} dot={false} isAnimationActive={false} />
            <Line type="monotone" dataKey="p99" stroke={color('--fraud')} dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

export function ThroughputChart({ data }: { data: LatencyPoint[] }) {
  return (
    <section className="panel">
      <div className="panel-h">
        <h3>Throughput and graph size</h3>
        <span className="hint">events/s (wall) · active users · users re-evaluated per batch</span>
      </div>
      <div className="chart">
        <ResponsiveContainer width="100%" height={170}>
          <LineChart data={data} margin={{ top: 6, right: 0, bottom: 0, left: -12 }}>
            <CartesianGrid stroke={color('--line')} strokeDasharray="3 3" />
            <XAxis dataKey="t" tick={{ fontSize: 11, fill: color('--muted') }} unit="s" type="number" domain={['dataMin', 'dataMax']} />
            <YAxis yAxisId="l" tick={{ fontSize: 11, fill: color('--muted') }} />
            <YAxis yAxisId="r" orientation="right" tick={{ fontSize: 11, fill: color('--muted') }} />
            <Tooltip contentStyle={{ background: color('--panel'), border: `1px solid ${color('--line')}`, fontSize: 12 }} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Line yAxisId="l" type="monotone" dataKey="eps" name="events/s" stroke={color('--ip')} dot={false} isAnimationActive={false} />
            <Line yAxisId="r" type="monotone" dataKey="users" name="active users" stroke={color('--device')} dot={false} isAnimationActive={false} />
            <Line yAxisId="r" type="monotone" dataKey="region" name="dirty region" stroke={color('--muted')} strokeDasharray="4 3" dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}
