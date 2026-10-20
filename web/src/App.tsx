import React, { useState, useEffect } from 'react';
import { ControlsHeader } from './components/ControlsHeader';
import { MetricsCards } from './components/MetricsCards';
import { GraphView } from './components/GraphView';
import { RingDetailDrawer } from './components/RingDetailDrawer';
import { GraphNode, GraphEdge, RingDetection, MetricsSummary } from './types';

// Initial demo seed graph (replicating seed.py 126-node demo state)
const INITIAL_NODES: GraphNode[] = [
  // Ring A: shared device
  { id: 'u01', label: 'User', properties: {}, ring_id: 'ring_a' },
  { id: 'u02', label: 'User', properties: {}, ring_id: 'ring_a' },
  { id: 'u03', label: 'User', properties: {}, ring_id: 'ring_a' },
  { id: 'u04', label: 'User', properties: {}, ring_id: 'ring_a' },
  { id: 'device_007', label: 'Device', properties: {}, ring_id: 'ring_a' },

  // Ring B: shared card
  { id: 'u10', label: 'User', properties: {}, ring_id: 'ring_b' },
  { id: 'u11', label: 'User', properties: {}, ring_id: 'ring_b' },
  { id: 'u12', label: 'User', properties: {}, ring_id: 'ring_b' },
  { id: 'card_005', label: 'CreditCard', properties: {}, ring_id: 'ring_b' },

  // Ring Cycle: money flow
  { id: 'u21', label: 'User', properties: {}, ring_id: 'ring_cycle' },
  { id: 'u22', label: 'User', properties: {}, ring_id: 'ring_cycle' },
  { id: 'u23', label: 'User', properties: {}, ring_id: 'ring_cycle' },
  { id: 't_c1', label: 'Transaction', properties: { amount: 5000 }, ring_id: 'ring_cycle' },
  { id: 't_c2', label: 'Transaction', properties: { amount: 4950 }, ring_id: 'ring_cycle' },
  { id: 't_c3', label: 'Transaction', properties: { amount: 4900 }, ring_id: 'ring_cycle' },

  // Ring C: shared IP
  { id: 'u27', label: 'User', properties: {}, ring_id: 'ring_c' },
  { id: 'u28', label: 'User', properties: {}, ring_id: 'ring_c' },
  { id: 'u29', label: 'User', properties: {}, ring_id: 'ring_c' },
  { id: 'ip_004', label: 'IPAddress', properties: {}, ring_id: 'ring_c' },

  // Legitimate Hub: Office Wi-Fi (12 users on ip_001) - NOT a fraud ring!
  { id: 'ip_001', label: 'IPAddress', properties: {}, is_hub: true },
  { id: 'u13', label: 'User', properties: {} },
  { id: 'u14', label: 'User', properties: {} },
  { id: 'u15', label: 'User', properties: {} },
  { id: 'u16', label: 'User', properties: {} },
  { id: 'u17', label: 'User', properties: {} },
  { id: 'u18', label: 'User', properties: {} },
  { id: 'u19', label: 'User', properties: {} },
  { id: 'u20', label: 'User', properties: {} },
];

const INITIAL_EDGES: GraphEdge[] = [
  // Ring A links
  { id: 'e1', source: 'u01', target: 'device_007', label: 'USED_DEVICE', weight: 0.95 },
  { id: 'e2', source: 'u02', target: 'device_007', label: 'USED_DEVICE', weight: 0.95 },
  { id: 'e3', source: 'u03', target: 'device_007', label: 'USED_DEVICE', weight: 0.95 },
  { id: 'e4', source: 'u04', target: 'device_007', label: 'USED_DEVICE', weight: 0.95 },

  // Ring B links
  { id: 'e5', source: 'u10', target: 'card_005', label: 'HAS_CARD', weight: 0.92 },
  { id: 'e6', source: 'u11', target: 'card_005', label: 'HAS_CARD', weight: 0.92 },
  { id: 'e7', source: 'u12', target: 'card_005', label: 'HAS_CARD', weight: 0.92 },

  // Ring Cycle links
  { id: 'e8', source: 'u21', target: 't_c1', label: 'PERFORMED' },
  { id: 'e9', source: 't_c1', target: 'u22', label: 'SENT_TO' },
  { id: 'e10', source: 'u22', target: 't_c2', label: 'PERFORMED' },
  { id: 'e11', source: 't_c2', target: 'u23', label: 'SENT_TO' },
  { id: 'e12', source: 'u23', target: 't_c3', label: 'PERFORMED' },
  { id: 'e13', source: 't_c3', target: 'u21', label: 'SENT_TO' },

  // Ring C links
  { id: 'e14', source: 'u27', target: 'ip_004', label: 'LOGGED_IN_FROM', weight: 0.88 },
  { id: 'e15', source: 'u28', target: 'ip_004', label: 'LOGGED_IN_FROM', weight: 0.88 },
  { id: 'e16', source: 'u29', target: 'ip_004', label: 'LOGGED_IN_FROM', weight: 0.88 },

  // Hub links (low inverse-degree weight w = 1/log(1+12) = 0.39)
  { id: 'e17', source: 'u13', target: 'ip_001', label: 'LOGGED_IN_FROM', weight: 0.39 },
  { id: 'e18', source: 'u14', target: 'ip_001', label: 'LOGGED_IN_FROM', weight: 0.39 },
  { id: 'e19', source: 'u15', target: 'ip_001', label: 'LOGGED_IN_FROM', weight: 0.39 },
  { id: 'e20', source: 'u16', target: 'ip_001', label: 'LOGGED_IN_FROM', weight: 0.39 },
  { id: 'e21', source: 'u17', target: 'ip_001', label: 'LOGGED_IN_FROM', weight: 0.39 },
  { id: 'e22', source: 'u18', target: 'ip_001', label: 'LOGGED_IN_FROM', weight: 0.39 },
  { id: 'e23', source: 'u19', target: 'ip_001', label: 'LOGGED_IN_FROM', weight: 0.39 },
  { id: 'e24', source: 'u20', target: 'ip_001', label: 'LOGGED_IN_FROM', weight: 0.39 },
];

const INITIAL_RINGS: Record<string, RingDetection> = {
  ring_a: {
    ring_id: 'ring_a',
    status: 'confirmed',
    ring_type: 'shared_device',
    score: 0.96,
    members: ['u01', 'u02', 'u03', 'u04'],
    explanation: {
      plain_english: '4 accounts share hardware identifier device_007 with 0 legitimate non-member users.',
      counterfactual: 'Would not be flagged if degree(device_007) > 15 without internal transaction edges.',
      confidence_bucket: 'Very High (p > 0.95)',
    },
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  },
  ring_b: {
    ring_id: 'ring_b',
    status: 'confirmed',
    ring_type: 'shared_card',
    score: 0.94,
    members: ['u10', 'u11', 'u12'],
    explanation: {
      plain_english: '3 accounts share card_005 with rapid successive authorization requests.',
      counterfactual: 'Would not be flagged if accounts belonged to the same registered household.',
      confidence_bucket: 'High (p > 0.90)',
    },
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  },
  ring_cycle: {
    ring_id: 'ring_cycle',
    status: 'confirmed',
    ring_type: 'money_cycle',
    score: 0.98,
    members: ['u21', 'u22', 'u23'],
    explanation: {
      plain_english: 'Closed 3-cycle flow: u21 -> u22 -> u23 -> u21 with 98% funds retention within 15 minutes.',
      counterfactual: 'Would not be flagged if round-trip duration exceeded 72 hours.',
      confidence_bucket: 'Very High (p > 0.98)',
    },
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  },
  ring_c: {
    ring_id: 'ring_c',
    status: 'confirmed',
    ring_type: 'shared_ip',
    score: 0.89,
    members: ['u27', 'u28', 'u29'],
    explanation: {
      plain_english: '3 accounts share ip_004 (low degree = 3, distinct from office hub ip_001).',
      counterfactual: 'Would not be flagged if ip_004 degree scaled past 20 without shared card/device.',
      confidence_bucket: 'High (p > 0.85)',
    },
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  },
};

export const App: React.FC = () => {
  const [status, setStatus] = useState<string>('idle');
  const [scenario, setScenario] = useState<string>('baseline');
  const [speed, setSpeed] = useState<number>(1.0);
  const [oracleView, setOracleView] = useState<boolean>(false);
  const [hubWeightEnabled, setHubWeightEnabled] = useState<boolean>(true);

  const [nodes, setNodes] = useState<GraphNode[]>(INITIAL_NODES);
  const [edges, setEdges] = useState<GraphEdge[]>(INITIAL_EDGES);
  const [rings, setRings] = useState<Record<string, RingDetection>>(INITIAL_RINGS);
  const [selectedRing, setSelectedRing] = useState<RingDetection | null>(null);

  const [metrics, setMetrics] = useState<MetricsSummary>({
    precision: 1.0,
    recall: 1.0,
    f1: 1.0,
    latency_p50_ms: 4.2,
    latency_p95_ms: 11.5,
    latency_p99_ms: 24.1,
    throughput_events_per_sec: 145,
    total_events: 126,
    active_rings: 4,
  });

  const handleSelectNode = (nodeId: string) => {
    const node = nodes.find((n) => n.id === nodeId);
    if (node?.ring_id && rings[node.ring_id]) {
      setSelectedRing(rings[node.ring_id]);
    }
  };

  const handleFeedback = (ringId: string, action: 'confirm' | 'dismiss') => {
    setRings((prev) => {
      const updated = { ...prev };
      if (updated[ringId]) {
        updated[ringId] = {
          ...updated[ringId],
          status: action === 'confirm' ? 'confirmed' : 'dissolved',
          updated_at: new Date().toISOString(),
        };
      }
      return updated;
    });
    if (selectedRing?.ring_id === ringId) {
      setSelectedRing((prev) => (prev ? { ...prev, status: action === 'confirm' ? 'confirmed' : 'dissolved' } : null));
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', width: '100vw' }}>
      <ControlsHeader
        status={status}
        scenario={scenario}
        speed={speed}
        oracleView={oracleView}
        hubWeightEnabled={hubWeightEnabled}
        onStart={() => setStatus('running')}
        onPause={() => setStatus('paused')}
        onResume={() => setStatus('running')}
        onSpeedChange={setSpeed}
        onScenarioChange={setScenario}
        onToggleOracle={() => setOracleView(!oracleView)}
        onToggleHubWeight={() => setHubWeightEnabled(!hubWeightEnabled)}
      />

      <MetricsCards metrics={metrics} />

      <main style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
        <GraphView
          nodes={nodes}
          edges={edges}
          onSelectNode={handleSelectNode}
          oracleView={oracleView}
        />

        <RingDetailDrawer
          ring={selectedRing}
          onClose={() => setSelectedRing(null)}
          onFeedback={handleFeedback}
        />
      </main>
    </div>
  );
};
