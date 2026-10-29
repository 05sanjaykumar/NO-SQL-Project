import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import cytoscape, { Core, ElementDefinition } from 'cytoscape';
// @ts-expect-error: no type declarations
import svgExport from 'cytoscape-svg';
import type { GraphSnapshot } from '../types';

cytoscape.use(svgExport);

export interface GraphHandle {
  exportPng: () => void;
  exportSvg: () => void;
  fit: () => void;
}

interface Props {
  snapshot: GraphSnapshot;
  oracle: boolean;
  selectedRing: string | null;
  onSelectRing: (ringId: string) => void;
  /** When set, draw only these accounts and what they reach within `hops`. */
  focus?: { user_ids: string[]; hops: number } | null;
}

const css = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

function style(): cytoscape.Stylesheet[] {
  const c = {
    ink: css('--ink'), muted: css('--muted'), line: css('--line'), benign: css('--benign'),
    fraud: css('--fraud'), warn: css('--warn'), alert: css('--alert'), confirmed: css('--confirmed'),
    candidate: css('--candidate'), device: css('--device'), card: css('--card'), ip: css('--ip'),
    hub: css('--hub'), bg: css('--panel'),
  };
  return [
    { selector: 'node', style: { 'background-color': c.benign, width: 9, height: 9, 'border-width': 0, label: '', 'transition-property': 'background-color, border-color, width, height, opacity', 'transition-duration': 400 } as any },
    { selector: 'node[kind = "device"]', style: { shape: 'round-rectangle', 'background-color': c.device, width: 11, height: 11 } },
    { selector: 'node[kind = "card"]', style: { shape: 'diamond', 'background-color': c.card, width: 12, height: 12 } },
    { selector: 'node[kind = "ip"]', style: { shape: 'hexagon', 'background-color': c.ip, width: 12, height: 12 } },
    { selector: 'node[?hub]', style: { 'background-color': c.hub, width: 26, height: 26, label: 'data(label)', color: c.ink, 'font-size': 10, 'text-valign': 'top', 'text-margin-y': -4, 'text-outline-color': c.bg, 'text-outline-width': 2 } as any },
    { selector: 'node.candidate', style: { 'border-width': 2, 'border-color': c.candidate } },
    { selector: 'node.confirmed', style: { 'background-color': c.confirmed, width: 12, height: 12 } },
    { selector: 'node.alert', style: { 'background-color': c.alert, width: 14, height: 14 } },
    { selector: 'node.o-tp', style: { 'background-color': c.fraud, 'border-width': 0, width: 13, height: 13 } },
    { selector: 'node.o-fp', style: { 'background-color': c.warn, 'border-width': 0, width: 13, height: 13 } },
    { selector: 'node.o-miss', style: { 'background-color': c.bg, 'border-width': 2.5, 'border-color': c.fraud, width: 12, height: 12 } },
    { selector: 'node.o-benign', style: { 'background-color': c.benign, 'border-width': 0 } },
    { selector: 'node.selected', style: { 'border-width': 3, 'border-color': c.ink } },
    { selector: 'node.new', style: { opacity: 0 } },
    { selector: 'edge', style: { width: 0.7, 'line-color': c.line, 'curve-style': 'haystack', opacity: 0.8 } as any },
    { selector: 'edge[kind = "transfer"]', style: { width: 'mapData(count, 1, 6, 0.6, 2)', 'line-color': c.muted, opacity: 0.18, 'curve-style': 'bezier', 'target-arrow-shape': 'triangle', 'target-arrow-color': c.muted, 'arrow-scale': 0.6 } as any },
    { selector: 'edge.ring-transfer', style: { 'line-color': c.fraud, 'target-arrow-color': c.fraud, opacity: 0.95, width: 'mapData(count, 1, 6, 1.5, 3.5)' } as any },
    { selector: 'edge.flash', style: { opacity: 0.9 } },
  ];
}

export const GraphView = forwardRef<GraphHandle, Props>(function GraphView(
  { snapshot, oracle, selectedRing, onSelectRing, focus },
  ref,
) {
  const host = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const lastLayout = useRef(0);
  // Follow the graph (fit after each layout) until the user zooms or pans by hand.
  const userMoved = useRef(false);
  const seenEdges = useRef<Map<string, number>>(new Map());
  const selectRef = useRef(onSelectRing);
  selectRef.current = onSelectRing;

  useEffect(() => {
    if (!host.current) return;
    const cy = cytoscape({
      container: host.current,
      style: style(),
      wheelSensitivity: 0.25,
      minZoom: 0.15,
      maxZoom: 4,
      boxSelectionEnabled: false,
    });
    cy.on('scrollzoom pinchzoom dragpan', () => {
      userMoved.current = true;
    });
    cy.on('tap', 'node', (e) => {
      const rings = e.target.data('rings') as string[] | undefined;
      if (rings && rings.length) selectRef.current(rings[0]);
    });
    cyRef.current = cy;
    const mq = matchMedia('(prefers-color-scheme: dark)');
    const restyle = () => cy.style(style());
    mq.addEventListener('change', restyle);
    return () => {
      mq.removeEventListener('change', restyle);
      cy.destroy();
    };
  }, []);

  // Apply a new snapshot as a diff so the graph animates instead of redrawing.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const tracked = snapshot.oracle?.tracked ?? {};
    const want = new Set<string>();
    const added: string[] = [];
    // Level of detail: users with nothing drawn around them only add noise.
    const connected = new Set<string>();
    for (const e of snapshot.edges) {
      connected.add(e.source);
      connected.add(e.target);
    }
    // A focus narrows the view to some accounts plus whatever they reach within
    // `hops`. One hop is usually what you want: it keeps the shared device or IP
    // that links them, which is the whole point of looking.
    const focused = focus?.user_ids?.length ? new Set(focus.user_ids) : null;
    let visible: Set<string> | null = null;
    if (focused) {
      visible = new Set(focused);
      for (let h = 0; h < Math.max(0, focus?.hops ?? 1); h++) {
        const edge = new Set(visible);
        for (const e of snapshot.edges) {
          if (edge.has(e.source)) visible.add(e.target);
          if (edge.has(e.target)) visible.add(e.source);
        }
      }
    }

    const nodes = snapshot.nodes.filter((n) => {
      if (visible && !visible.has(n.id)) return false;
      if (focused?.has(n.id)) return true;
      return n.kind !== 'user' || connected.has(n.id) || (n.rings ?? []).length > 0 || (oracle && n.truth);
    });
    // Groups (rings, hubs) and their slots; computed first so edges can be filtered.
    const { pos: positions, groupOf } = clusterLayout(nodes, snapshot.edges, oracle);
    cy.batch(() => {
      for (const n of nodes) {
        want.add(n.id);
        const classes: string[] = [];
        if (n.status) classes.push(n.status);
        if (oracle && n.kind === 'user') {
          const flaggedTrue = (n.rings ?? []).some((r) => tracked[r]?.verdict === 'true');
          const flagged = n.status === 'alert' || n.status === 'confirmed';
          if (flagged && (flaggedTrue || n.truth)) classes.push('o-tp');
          else if (flagged) classes.push('o-fp');
          else if (n.truth) classes.push('o-miss');
          else classes.push('o-benign');
        }
        if (selectedRing && (n.rings ?? []).includes(selectedRing)) classes.push('selected');
        const data = { ...n, label: n.hub ? `${n.id} · ${n.degree} users` : n.id };
        const existing = cy.getElementById(n.id);
        if (existing.nonempty()) {
          existing.data(data);
          existing.classes(classes);
        } else {
          // Place new nodes next to an already drawn neighbour so the layout stays calm.
          const nb = snapshot.edges.find((e) => (e.source === n.id && cy.getElementById(e.target).nonempty()) || (e.target === n.id && cy.getElementById(e.source).nonempty()));
          const anchor = nb ? cy.getElementById(nb.source === n.id ? nb.target : nb.source).position() : { x: 0, y: 0 };
          cy.add({ group: 'nodes', data, classes: [...classes, 'new'], position: { x: anchor.x + (Math.random() - 0.5) * 60, y: anchor.y + (Math.random() - 0.5) * 60 } } as ElementDefinition);
          added.push(n.id);
        }
      }
      cy.nodes().forEach((node) => {
        if (!want.has(node.id())) node.remove();
      });
      const wantE = new Set<string>();
      const ringOf = new Map(nodes.map((n) => [n.id, new Set(n.rings ?? [])]));
      for (const e of snapshot.edges) {
        if (!want.has(e.source) || !want.has(e.target)) continue;
        // Only draw transfers inside one group; cross-group benign traffic is noise here.
        if (e.kind === 'transfer' && groupOf.get(e.source) !== groupOf.get(e.target)) continue;
        wantE.add(e.id);
        const a = ringOf.get(e.source), b = ringOf.get(e.target);
        const sameRing = !!a && !!b && [...a].some((r) => b.has(r))
          && (groupOf.get(e.source) ?? '').startsWith('ring:');
        const classes = e.kind === 'transfer' && sameRing ? ['ring-transfer'] : [];
        const prev = seenEdges.current.get(e.id);
        if (e.kind === 'transfer' && prev !== undefined && (e.count ?? 0) > prev) classes.push('flash');
        seenEdges.current.set(e.id, e.count ?? 0);
        const ex = cy.getElementById(e.id);
        if (ex.nonempty()) {
          ex.data(e);
          ex.classes(classes);
        } else {
          cy.add({ group: 'edges', data: { ...e }, classes: [...classes, 'flash'] } as ElementDefinition);
        }
      }
      cy.edges().forEach((edge) => {
        if (!wantE.has(edge.id())) edge.remove();
      });
    });
    // Fade new nodes in, and let transfer flashes decay.
    window.setTimeout(() => cy.nodes('.new').removeClass('new'), 30);
    window.setTimeout(() => cy.edges('.flash').removeClass('flash'), 900);
    // Deterministic clustered layout: each tracked ring (or, in oracle view, each true
    // ring) and each busy hub is a group with a stable slot; members sit on a small
    // circle around the group centre. Nodes glide to their slot, so rings visibly
    // form, grow and dissolve without the whole picture reshuffling.
    const moved = cy.nodes().filter((n) => {
      const target = positions.get(n.id());
      if (!target) return false;
      const cur = n.position();
      return Math.hypot(cur.x - target.x, cur.y - target.y) > 1;
    });
    moved.forEach((n) => {
      const target = positions.get(n.id())!;
      if (n.hasClass('new') || added.includes(n.id())) n.position(target);
      else {
        n.stop(true, false); // drop queued moves so nodes never lag behind their slot
        n.animate({ position: target } as any, { duration: 700, easing: 'ease-in-out-cubic', queue: false } as any);
      }
    });
    const now = Date.now();
    if (!userMoved.current && (added.length || moved.length || now - lastLayout.current > 4000)) {
      lastLayout.current = now;
      // Fit once the nodes have reached their new slots.
      window.setTimeout(() => {
        if (!userMoved.current) cy.animate({ fit: { eles: cy.elements(), padding: 30 } } as any, { duration: 400 });
      }, 750);
    }
  }, [snapshot, oracle, selectedRing]);

  useImperativeHandle(ref, () => ({
    exportPng: () => {
      const cy = cyRef.current;
      if (!cy) return;
      const blob = cy.png({ full: true, scale: 2, bg: css('--panel'), output: 'blob' }) as unknown as Blob;
      download(blob, 'fraudsim-graph.png');
    },
    exportSvg: () => {
      const cy = cyRef.current as any;
      if (!cy) return;
      const svg: string = cy.svg({ full: true, scale: 1, bg: css('--panel') });
      download(new Blob([svg], { type: 'image/svg+xml' }), 'fraudsim-graph.svg');
    },
    fit: () => {
      userMoved.current = false;
      cyRef.current?.animate({ fit: { eles: cyRef.current.elements(), padding: 24 } } as any, { duration: 400 });
    },
  }));

  return <div ref={host} className="graph-canvas" data-testid="graph" />;
});

function download(blob: Blob, name: string) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  window.setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

type P = { x: number; y: number };

/** Group centres on a sunflower spiral (hubs first), members on rings around them. */
function clusterLayout(
  nodes: { id: string; kind: string; rings?: string[]; truth?: string | null; hub?: boolean }[],
  edges: { source: string; target: string; kind: string }[],
  oracle: boolean,
): { pos: Map<string, P>; groupOf: Map<string, string> } {
  const groupOf = new Map<string, string>();
  const hubs = nodes.filter((n) => n.hub).map((n) => n.id).sort();
  const hubUsers = new Map<string, string>();
  for (const e of edges) {
    if (e.kind === 'link' && hubs.includes(e.target) && !hubUsers.has(e.source)) hubUsers.set(e.source, e.target);
  }
  for (const n of nodes) {
    if (n.kind !== 'user') continue;
    const g = (n.rings && n.rings.length ? `ring:${[...n.rings].sort()[0]}` : null)
      ?? (oracle && n.truth ? `truth:${n.truth}` : null)
      ?? (hubUsers.has(n.id) ? `hub:${hubUsers.get(n.id)}` : 'background');
    groupOf.set(n.id, g);
  }
  const members = new Map<string, string[]>();
  for (const [u, g] of groupOf) members.set(g, [...(members.get(g) ?? []), u]);
  const order = [...members.keys()].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
  const centres = new Map<string, P>();
  const golden = Math.PI * (3 - Math.sqrt(5));
  order.forEach((g, i) => {
    const r = 170 * Math.sqrt(i + 0.5);
    centres.set(g, { x: r * Math.cos(i * golden), y: r * Math.sin(i * golden) });
  });
  const pos = new Map<string, P>();
  for (const g of order) {
    const us = (members.get(g) ?? []).sort();
    const c = centres.get(g)!;
    const radius = Math.max(28, 9 * us.length);
    us.forEach((u, i) => {
      const a = (2 * Math.PI * i) / us.length;
      pos.set(u, { x: c.x + radius * Math.cos(a), y: c.y + radius * Math.sin(a) });
    });
  }
  // Hubs sit at the centre of their users' group; other attributes at their users' centroid.
  for (const n of nodes) {
    if (n.kind === 'user') continue;
    if (n.hub && centres.has(`hub:${n.id}`)) {
      pos.set(n.id, centres.get(`hub:${n.id}`)!);
      continue;
    }
    const users = edges.filter((e) => e.target === n.id && pos.has(e.source)).map((e) => pos.get(e.source)!);
    if (users.length) {
      const x = users.reduce((s, p) => s + p.x, 0) / users.length;
      const y = users.reduce((s, p) => s + p.y, 0) / users.length;
      pos.set(n.id, { x: x + 6, y: y + 6 });
    }
  }
  return { pos, groupOf };
}

function rank(group: string): number {
  if (group.startsWith('hub:')) return 0;
  if (group.startsWith('ring:')) return 1;
  if (group.startsWith('truth:')) return 2;
  return 3;
}
