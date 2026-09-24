import { useMemo, useState } from "react";
import type { GraphResponse } from "../lib/api/nodes";
import { computeStaticLayout, selectTopConnections, type LayoutLink, type LayoutNode } from "../lib/graphLayout";

const TOP_N = 3;
const MAX_LABEL = 24;
// Edge colors follow neurix-ui's D3Graph: normal gray, share purple.
const EDGE_NORMAL = "#cbd5f5";
const EDGE_SHARE = "#c084fc";
const NODE_OWN = "#3b82f6";

function edgeStyle(link: LayoutLink, highlightCrossMember: boolean) {
  const opacity = 0.35 + 0.45 * Math.min(Math.max(link.score, 0), 1);
  if (highlightCrossMember ? link.crossMember : link.isShare) {
    // Distinct in color, width and dash, so it reads without color vision too.
    return { stroke: EDGE_SHARE, strokeWidth: 3.5, strokeDasharray: "8 4", strokeOpacity: Math.max(opacity, 0.8) };
  }
  return { stroke: EDGE_NORMAL, strokeWidth: 2, strokeDasharray: undefined, strokeOpacity: opacity };
}

interface StaticGraphProps {
  graph: GraphResponse;
  /** Node outline color; defaults to blue for own notes, purple for shared ones. */
  colorFor?: (node: LayoutNode) => string;
  /** Team mode: highlight edges between two different owners (else: share edges). */
  highlightCrossMember?: boolean;
  emptyText?: string;
}

/**
 * Personal graph, decluttered on purpose: a static layout (simulated once,
 * then frozen — no continuous ticking) and, by default, only each node's
 * top-N strongest connections. Colors follow neurix-ui's D3Graph. Positions
 * come from d3-force; React just draws them.
 */
export default function StaticGraph({
  graph,
  colorFor = (node) => (node.shared ? EDGE_SHARE : NODE_OWN),
  highlightCrossMember = false,
  emptyText = "Your graph is empty. Saved notes appear here once processed.",
}: StaticGraphProps) {
  const [showAll, setShowAll] = useState(false);

  const visibleConnections = useMemo(
    () => (showAll ? graph.connections : selectTopConnections(graph.connections, TOP_N)),
    [graph.connections, showAll],
  );
  const layout = useMemo(() => computeStaticLayout(graph.nodes, visibleConnections), [graph.nodes, visibleConnections]);
  const hidden = graph.connections.length - visibleConnections.length;

  if (graph.nodes.length === 0) {
    return <p>{emptyText}</p>;
  }

  const { bounds } = layout;
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <small>
          {graph.nodes.length} notes · {visibleConnections.length} of {graph.connections.length} connections
          {!showAll && hidden > 0 ? ` (top ${TOP_N} per note)` : ""}
        </small>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 14 }}>
          <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} />
          Show all connections
        </label>
      </div>
      <svg
        viewBox={`${bounds.x} ${bounds.y} ${bounds.width} ${bounds.height}`}
        style={{
          width: "100%",
          height: 480,
          borderRadius: 8,
          background: "linear-gradient(to bottom, rgb(2, 8, 23), rgb(15, 23, 42))",
        }}
      >
        <defs>
          <radialGradient id="gradient-normal">
            <stop offset="0%" stopColor="#dbeafe" />
            <stop offset="100%" stopColor="#0f172a" />
          </radialGradient>
        </defs>
        <g>
          {layout.links.map((link) => (
            <line
              key={`${link.source.id}-${link.target.id}`}
              x1={link.source.x}
              y1={link.source.y}
              x2={link.target.x}
              y2={link.target.y}
              {...edgeStyle(link, highlightCrossMember)}
            />
          ))}
        </g>
        <g>
          {layout.nodes.map((node) => {
            const r = layout.radius(node);
            const processing = node.status === "PROCESSING";
            const label = node.title.length > MAX_LABEL ? `${node.title.slice(0, MAX_LABEL - 1)}…` : node.title;
            return (
              <g key={node.id} transform={`translate(${node.x ?? 0},${node.y ?? 0})`} opacity={processing ? 0.5 : 1}>
                <title>{processing ? `${node.title} (processing)` : node.title}</title>
                <circle r={r} fill="url(#gradient-normal)" stroke={colorFor(node)} strokeWidth={3} />
                <text y={r + 16} textAnchor="middle" fill="#e2e8f0" fontSize={12}>
                  {label}
                </text>
              </g>
            );
          })}
        </g>
      </svg>
    </div>
  );
}
