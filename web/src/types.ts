// Shapes of the FraudSim API (src/fraudsim/api). Everything here comes from a live run.

export type RunStatus = 'idle' | 'running' | 'paused' | 'stopped' | 'finished';

export interface Status {
  run_id: string;
  status: RunStatus;
  scenario: string;
  seed: number;
  events_emitted: number;
  rings_detected: number;
  speed_multiplier: number;
  sim_time_s: number;
  rate: number;
}

export interface Scenario {
  name: string;
  description: string;
  duration_s: number;
  users: number;
  rings: number;
}

export interface DetectorInfo {
  type: string;
  name: string;
  enabled: boolean;
  incremental: boolean;
  params: Record<string, unknown>;
}

export interface LiveConfig {
  alert_threshold: number;
  thresholds_by_type: Record<string, number>;
  edge_threshold: number | null;
  hub_weight_method: string | null;
  model: string | null;
  batch_size: number;
}

export interface GraphNode {
  id: string;
  kind: 'user' | 'device' | 'card' | 'ip';
  rings?: string[];
  status?: 'alert' | 'confirmed' | 'candidate' | null;
  risk?: number | null;
  truth?: string | null;
  degree?: number;
  hub?: boolean;
  last_s?: number;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  kind: 'link' | 'transfer';
  count?: number;
  amount?: number;
}

export interface TrueRingRow {
  true_ring: string;
  type: string;
  evasion: string;
  size: number;
  activate_s: number | null;
  detected: boolean;
  flag_s: number | null;
  time_to_detect_s: number | null;
  miss_reason: string | null;
  members: string[];
}

export interface Oracle {
  tracked: Record<string, { verdict: 'true' | 'false'; true_ring: string | null; jaccard: number }>;
  true_rings: TrueRingRow[];
  spawned_not_active: string[];
}

export interface GraphSnapshot {
  sim_time_s?: number;
  nodes: GraphNode[];
  edges: GraphEdge[];
  shown_users: number;
  total_active_users: number;
  window_s?: number;
  oracle?: Oracle | null;
}

export interface Contribution {
  feature: string;
  value: number;
}

export interface RingExplanation {
  detectors: string[];
  risk: number | null;
  alerted_at: string | null;
  early_warned_at: string | null;
  reason_text?: string;
  feature_contributions?: Record<string, number>;
  counterfactual?: string;
  calibration_bucket?: string;
  threshold?: number;
  evidence_subgraph?: {
    nodes: { id: string; type: string; degree?: number; hub_weight?: number }[];
    edges: { source: string; target: string; type: string; amount?: number; t?: number }[];
  };
  evidence?: Record<string, unknown>;
}

export interface Ring {
  ring_id: string;
  status: 'candidate' | 'confirmed' | 'dissolved';
  ring_type: string;
  score: number;
  members: string[];
  features: Record<string, number>;
  explanation: RingExplanation;
  created_at: string;
  updated_at: string;
}

export interface Pct {
  count: number;
  p50: number | null;
  p95: number | null;
  p99: number | null;
  mean?: number | null;
}

export interface MetricsSummaryFull {
  true_rings: number;
  rings_flagged: number;
  rings_detected: number;
  rings_missed: number;
  false_positive_rings: number;
  ring: { precision: number | null; recall: number | null; f1: number | null };
  user: { precision: number | null; recall: number | null; f1: number | null };
  missed_reasons: Record<string, number>;
  time_to_detect_s: Pct;
  triage: Record<string, number | null>;
  latency_ms: Record<string, Pct>;
  throughput: { events: number; events_per_s: number | null };
}

export interface BatchMsg {
  type: 'BATCH';
  batch: number;
  n_events: number;
  region: number;
  timings_ms: Record<string, number>;
  detector_ms: Record<string, number>;
  sim_time_s: number;
  events_total: number;
  wall_throughput_eps: number;
  graph: Record<string, number>;
  events: { event_id: string; event_type: string; ts: string; payload: Record<string, unknown>; seq: number }[];
}

export interface RingUpdateMsg {
  type: 'RING_UPDATE';
  update: {
    ts: string;
    ring_id: string;
    change: string;
    status: string;
    members: string[];
    detectors: string[];
    score: number;
    details: Record<string, unknown>;
  };
}
