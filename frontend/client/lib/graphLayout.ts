import * as d3 from "d3";
import type { GraphConnection, GraphNode } from "./api/nodes";

export interface LayoutNode extends d3.SimulationNodeDatum {
  id: string;
  title: string;
  status: GraphNode["status"];
  degree: number;
}

export interface LayoutLink {
  source: LayoutNode;
  target: LayoutNode;
  score: number;
}

export interface GraphLayout {
  nodes: LayoutNode[];
  links: LayoutLink[];
  radius: (node: LayoutNode) => number;
  bounds: { x: number; y: number; width: number; height: number };
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
export function computeStaticLayout(graphNodes: GraphNode[], connections: GraphConnection[]): GraphLayout {
  const nodes: LayoutNode[] = graphNodes.map((n) => ({ ...n, degree: 0 }));
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const links: LayoutLink[] = [];
  for (const c of connections) {
    const source = byId.get(c.source_id);
    const target = byId.get(c.target_id);
    if (!source || !target) continue;
    source.degree += 1;
    target.degree += 1;
    links.push({ source, target, score: c.score });
  }

  const degrees = nodes.map((n) => n.degree);
  const minDegree = d3.min(degrees) ?? 0;
  let maxDegree = d3.max(degrees) ?? 0;
  if (maxDegree === minDegree) maxDegree = minDegree + 1;
  const radiusScale = d3.scaleSqrt().domain([minDegree, maxDegree]).range([10, 22]);
  const radius = (n: LayoutNode) => radiusScale(n.degree);

  const simulation = d3
    .forceSimulation<LayoutNode>(nodes)
    .force("link", d3.forceLink<LayoutNode, LayoutLink>(links).distance(150).strength(0.6))
    .force("charge", d3.forceManyBody<LayoutNode>().strength(-400))
    // Wider than D3Graph's r + 12: labels are always visible here, so they need room too.
    .force("collide", d3.forceCollide<LayoutNode>().radius((n) => radius(n) + 36))
    .force("x", d3.forceX(0).strength(0.05))
    .force("y", d3.forceY(0).strength(0.05))
    .stop();
  const ticks = Math.ceil(Math.log(simulation.alphaMin()) / Math.log(1 - simulation.alphaDecay()));
  simulation.tick(ticks);

  // Fit box, with room for labels under the nodes.
  const pad = 40;
  const xs = nodes.map((n) => n.x ?? 0);
  const ys = nodes.map((n) => n.y ?? 0);
  const minX = (d3.min(xs) ?? 0) - pad * 2;
  const maxX = (d3.max(xs) ?? 0) + pad * 2;
  const minY = (d3.min(ys) ?? 0) - pad;
  const maxY = (d3.max(ys) ?? 0) + pad * 1.5;
  return {
    nodes,
    links,
    radius,
    bounds: { x: minX, y: minY, width: Math.max(maxX - minX, 1), height: Math.max(maxY - minY, 1) },
  };
}
