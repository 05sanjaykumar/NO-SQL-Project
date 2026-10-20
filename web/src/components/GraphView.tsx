import React, { useEffect, useRef } from 'react';
import cytoscape from 'cytoscape';
import { GraphNode, GraphEdge } from '../types';

interface GraphViewProps {
  nodes: GraphNode[];
  edges: GraphEdge[];
  onSelectNode: (nodeId: string) => void;
  oracleView: boolean;
}

export const GraphView: React.FC<GraphViewProps> = ({
  nodes,
  edges,
  onSelectNode,
  oracleView,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const cy = cytoscape({
      container: containerRef.current,
      style: [
        {
          selector: 'node',
          style: {
            'label': 'data(id)',
            'color': '#cbd5e1',
            'font-size': '10px',
            'font-family': 'JetBrains Mono',
            'text-valign': 'bottom',
            'text-margin-y': 4,
            'background-color': '#475569',
            'border-width': 1.5,
            'border-color': '#94a3b8',
            'width': 22,
            'height': 22,
            'transition-property': 'background-color, border-color, width, height',
            'transition-duration': 0.3,
          },
        },
        {
          selector: 'node[label = "User"]',
          style: {
            'background-color': '#6366f1',
            'border-color': '#818cf8',
            'width': 26,
            'height': 26,
          },
        },
        {
          selector: 'node[label = "Device"]',
          style: {
            'background-color': '#f59e0b',
            'border-color': '#fbbf24',
          },
        },
        {
          selector: 'node[label = "CreditCard"]',
          style: {
            'background-color': '#10b981',
            'border-color': '#34d399',
          },
        },
        {
          selector: 'node[label = "IPAddress"]',
          style: {
            'background-color': '#06b6d4',
            'border-color': '#22d3ee',
          },
        },
        {
          selector: 'node[label = "Transaction"]',
          style: {
            'background-color': '#ec4899',
            'border-color': '#f472b6',
            'shape': 'diamond',
            'width': 18,
            'height': 18,
          },
        },
        {
          selector: 'node[?is_hub]',
          style: {
            'width': 38,
            'height': 38,
            'border-width': 3,
            'border-color': '#38bdf8',
            'background-opacity': 0.8,
          },
        },
        {
          selector: 'node[ring_id]',
          style: {
            'border-width': 3,
            'border-color': '#f43f5e',
            'border-style': 'solid',
          },
        },
        {
          selector: 'edge',
          style: {
            'width': 1.2,
            'line-color': '#334155',
            'target-arrow-color': '#334155',
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
            'opacity': 0.7,
          },
        },
        {
          selector: 'edge[weight]',
          style: {
            'width': 'mapData(weight, 0, 1, 1, 3.5)',
            'line-color': '#38bdf8',
            'opacity': 0.85,
          },
        },
      ],
      layout: {
        name: 'cose',
        animate: false,
        randomize: false,
        componentSpacing: 60,
      },
    });

    cy.on('tap', 'node', (evt) => {
      onSelectNode(evt.target.id());
    });

    cyRef.current = cy;

    return () => {
      cy.destroy();
    };
  }, []);

  // Update elements
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;

    cy.batch(() => {
      cy.elements().remove();
      const cyNodes = nodes.map((n) => ({
        data: {
          id: n.id,
          label: n.label,
          is_hub: n.is_hub,
          ring_id: oracleView || n.ring_id ? n.ring_id : undefined,
        },
      }));
      const cyEdges = edges.map((e) => ({
        data: {
          id: e.id,
          source: e.source,
          target: e.target,
          label: e.label,
          weight: e.weight,
        },
      }));

      cy.add([...cyNodes, ...cyEdges]);
      cy.layout({ name: 'cose', animate: false }).run();
    });
  }, [nodes, edges, oracleView]);

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
      <div
        style={{
          position: 'absolute',
          bottom: 16,
          left: 16,
          display: 'flex',
          gap: 12,
          padding: '8px 14px',
          background: 'rgba(10, 13, 20, 0.85)',
          backdropFilter: 'blur(10px)',
          borderRadius: 8,
          border: '1px solid rgba(255, 255, 255, 0.08)',
          fontSize: 12,
        }}
      >
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 10, height: 10, borderRadius: '50%', background: '#6366f1' }} /> User
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 10, height: 10, borderRadius: '50%', background: '#f59e0b' }} /> Device
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 10, height: 10, borderRadius: '50%', background: '#10b981' }} /> Card
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 10, height: 10, borderRadius: '50%', background: '#06b6d4' }} /> IP
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 10, height: 10, transform: 'rotate(45deg)', background: '#ec4899' }} /> Txn
        </span>
      </div>
    </div>
  );
};
