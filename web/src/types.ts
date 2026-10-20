export interface GraphNode {
  id: string;
  label: 'User' | 'Device' | 'CreditCard' | 'IPAddress' | 'Transaction';
  properties: Record<string, any>;
  ring_id?: string;
  is_hub?: boolean;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  label: 'USED_DEVICE' | 'HAS_CARD' | 'LOGGED_IN_FROM' | 'PERFORMED' | 'SENT_TO';
  weight?: number;
  properties?: Record<string, any>;
}

export interface RingDetection {
  ring_id: string;
  status: 'candidate' | 'confirmed' | 'dissolved';
  ring_type: string;
  score: number;
  members: string[];
  features?: Record<string, any>;
  explanation?: {
    plain_english: string;
    feature_contributions?: Record<string, number>;
    counterfactual?: string;
    confidence_bucket?: string;
  };
  created_at: string;
  updated_at: string;
}

export interface MetricsSummary {
  precision: number;
  recall: number;
  f1: number;
  latency_p50_ms: number;
  latency_p95_ms: number;
  latency_p99_ms: number;
  throughput_events_per_sec: number;
  total_events: number;
  active_rings: number;
}
