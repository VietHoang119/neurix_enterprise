import * as d3 from "d3";
import type { GraphConnection, GraphNode } from "./api/nodes";

export interface LayoutNode extends d3.SimulationNodeDatum, GraphNode {
  degree: number;
}

export interface LayoutLink {
  source: LayoutNode;
  target: LayoutNode;
  score: number;
  isShare: boolean;
  crossMember: boolean;
}

/** A key shown as its own (diamond) node, linked to the notes that contain it. */
export interface KeyHub extends d3.SimulationNodeDatum {
  id: string;
  text: string;
  noteIds: string[];
}

export interface KeyLink {
  hub: KeyHub;
  note: LayoutNode;
}

export interface GraphLayout {
  nodes: LayoutNode[];
  links: LayoutLink[];
  hubs: KeyHub[];
  keyLinks: KeyLink[];
  radius: (node: LayoutNode) => number;
  bounds: { x: number; y: number; width: number; height: number };
}

export interface LabelCandidate {
  id: string;
  x: number;
  y: number;
  /** Already-truncated label text. */
  text: string;
  /** Y of the label's vertical center (node y + its offset below the node/hub). */
  labelY: number;
}

/**
 * Greedily keep labels that don't overlap an already-kept one, in the given
 * priority order (caller sorts: hubs first, then nodes by degree). Label
 * width is estimated from character count, since real text metrics aren't
 * available outside the DOM. This runs once per layout (at zoom level 1);
 * StaticGraph reveals the rest as the user zooms in, since node spacing
 * grows with zoom while label font size is counter-scaled to stay constant
 * on screen.
 */
export function selectVisibleLabels(candidates: LabelCandidate[], fontSize = 12): Set<string> {
  const charWidth = fontSize * 0.62;
  const lineHeight = fontSize + 6;
  const placed: { x0: number; x1: number; y0: number; y1: number }[] = [];
  const visible = new Set<string>();
  for (const c of candidates) {
    const halfWidth = Math.max((c.text.length * charWidth) / 2, fontSize);
    const x0 = c.x - halfWidth;
    const x1 = c.x + halfWidth;
    const y0 = c.labelY - lineHeight / 2;
    const y1 = c.labelY + lineHeight / 2;
    const overlaps = placed.some((p) => x0 < p.x1 && x1 > p.x0 && y0 < p.y1 && y1 > p.y0);
    if (!overlaps) {
      placed.push({ x0, x1, y0, y1 });
      visible.add(c.id);
    }
  }
  return visible;
}

// Smallest viewBox (in layout units ≈ px at 1:1) the graph is drawn in.
const MIN_VIEW_WIDTH = 680;
const MIN_VIEW_HEIGHT = 400;

type SimNode = LayoutNode | KeyHub;
interface SimLink extends d3.SimulationLinkDatum<SimNode> {
  kind: "note" | "key";
}

const isHub = (n: SimNode): n is KeyHub => "noteIds" in n;

/**
 * The keys worth drawing: those two or more notes share (a key on one note
 * connects nothing), most-shared first, at most `max`. Same rule as
 * neurix-ui's D3Graph key hubs. Keys match by text, as in share scoring, so
 * a key two owners both extracted is one hub.
 */
export function selectKeyHubs(nodes: GraphNode[], max: number): { text: string; noteIds: string[] }[] {
  const byKey = new Map<string, Set<string>>();
  for (const node of nodes) {
    for (const key of node.keys ?? []) {
      if (!byKey.has(key)) byKey.set(key, new Set());
      byKey.get(key)!.add(node.id);
    }
  }
  return Array.from(byKey, ([text, ids]) => ({ text, noteIds: Array.from(ids) }))
    .filter((k) => k.noteIds.length >= 2)
    .sort((a, b) => b.noteIds.length - a.noteIds.length || a.text.localeCompare(b.text))
    .slice(0, max);
}

/**
 * Keep an edge if it is among the `topN` strongest edges (score, then number
 * of shared keys) of either of its endpoints.
 */
export function selectTopConnections(connections: GraphConnection[], topN: number): GraphConnection[] {
  const byNode = new Map<string, GraphConnection[]>();
  for (const c of connections) {
    for (const id of [c.source_id, c.target_id]) {
      if (!byNode.has(id)) byNode.set(id, []);
      byNode.get(id)!.push(c);
    }
  }
  const keep = new Set<GraphConnection>();
  for (const edges of byNode.values()) {
    edges
      .slice()
      .sort((a, b) => b.score - a.score || b.key_count - a.key_count)
      .slice(0, topN)
      .forEach((c) => keep.add(c));
  }
  return connections.filter((c) => keep.has(c));
}

/**
 * Static layout: run the force simulation to convergence once, synchronously,
 * and return frozen positions. Nothing ticks afterwards — no animation.
 * Forces and the degree -> radius scale follow neurix-ui's D3Graph.
 */
export function computeStaticLayout(
  graphNodes: GraphNode[],
  connections: GraphConnection[],
  keys: { text: string; noteIds: string[] }[] = [],
): GraphLayout {
  const nodes: LayoutNode[] = graphNodes.map((n) => ({ ...n, degree: 0 }));
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const links: LayoutLink[] = [];
  for (const c of connections) {
    const source = byId.get(c.source_id);
    const target = byId.get(c.target_id);
    if (!source || !target) continue;
    source.degree += 1;
    target.degree += 1;
    links.push({ source, target, score: c.score, isShare: c.is_share, crossMember: c.cross_member });
  }

  const degrees = nodes.map((n) => n.degree);
  const minDegree = d3.min(degrees) ?? 0;
  let maxDegree = d3.max(degrees) ?? 0;
  if (maxDegree === minDegree) maxDegree = minDegree + 1;
  const radiusScale = d3.scaleSqrt().domain([minDegree, maxDegree]).range([10, 22]);
  const radius = (n: LayoutNode) => radiusScale(n.degree);

  // Key hubs join the simulation (so notes sharing a key pull together)
  // but not the degree -> radius scale above: sizes stay what they are with
  // keys hidden.
  const hubs: KeyHub[] = keys.map((k) => ({ id: `key:${k.text}`, text: k.text, noteIds: k.noteIds }));
  const keyLinks: KeyLink[] = hubs.flatMap((hub) =>
    hub.noteIds.flatMap((id) => {
      const note = byId.get(id);
      return note ? [{ hub, note }] : [];
    }),
  );
  const simLinks: SimLink[] = [
    ...links.map((l) => ({ source: l.source, target: l.target, kind: "note" as const })),
    ...keyLinks.map((l) => ({ source: l.hub, target: l.note, kind: "key" as const })),
  ];

  const simulation = d3
    .forceSimulation<SimNode>([...nodes, ...hubs])
    .force(
      "link",
      d3
        .forceLink<SimNode, SimLink>(simLinks)
        .distance((l) => (l.kind === "key" ? 90 : 150))
        .strength((l) => (l.kind === "key" ? 0.3 : 0.6)),
    )
    .force(
      "charge",
      d3.forceManyBody<SimNode>().strength((n) => (isHub(n) ? -200 : -400)),
    )
    // Wider than D3Graph's r + 12: labels are always visible here, so they need room too.
    .force(
      "collide",
      d3.forceCollide<SimNode>().radius((n) => (isHub(n) ? 28 : radius(n) + 36)),
    )
    .force("x", d3.forceX(0).strength(0.05))
    .force("y", d3.forceY(0).strength(0.05))
    .stop();
  const ticks = Math.ceil(Math.log(simulation.alphaMin()) / Math.log(1 - simulation.alphaDecay()));
  simulation.tick(ticks);

  // Fit box, with room for labels under the nodes.
  const pad = 40;
  const placed: SimNode[] = [...nodes, ...hubs];
  const xs = placed.map((n) => n.x ?? 0);
  const ys = placed.map((n) => n.y ?? 0);
  let minX = (d3.min(xs) ?? 0) - pad * 2;
  let maxX = (d3.max(xs) ?? 0) + pad * 2;
  let minY = (d3.min(ys) ?? 0) - pad;
  let maxY = (d3.max(ys) ?? 0) + pad * 1.5;
  // The canvas fills its panel, so a small graph would be scaled up until
  // its labels are huge: never zoom in past a minimum box, kept centered.
  const grow = (lo: number, hi: number, min: number): [number, number] => {
    const extra = Math.max(min - (hi - lo), 0) / 2;
    return [lo - extra, hi + extra];
  };
  [minX, maxX] = grow(minX, maxX, MIN_VIEW_WIDTH);
  [minY, maxY] = grow(minY, maxY, MIN_VIEW_HEIGHT);
  return {
    nodes,
    links,
    hubs,
    keyLinks,
    radius,
    bounds: { x: minX, y: minY, width: Math.max(maxX - minX, 1), height: Math.max(maxY - minY, 1) },
  };
}
