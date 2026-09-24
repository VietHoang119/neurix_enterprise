import { useMemo, useState } from "react";
import type { GraphResponse } from "../lib/api/nodes";
import { computeStaticLayout, selectTopConnections } from "../lib/graphLayout";

const TOP_N = 3;
const MAX_LABEL = 24;

/**
 * Personal graph, decluttered on purpose: a static layout (simulated once,
 * then frozen — no continuous ticking) and, by default, only each node's
 * top-N strongest connections. Colors follow neurix-ui's D3Graph. Positions
 * come from d3-force; React just draws them.
 */
export default function StaticGraph({ graph }: { graph: GraphResponse }) {
  const [showAll, setShowAll] = useState(false);

  const visibleConnections = useMemo(
    () => (showAll ? graph.connections : selectTopConnections(graph.connections, TOP_N)),
    [graph.connections, showAll],
  );
  const layout = useMemo(() => computeStaticLayout(graph.nodes, visibleConnections), [graph.nodes, visibleConnections]);
  const hidden = graph.connections.length - visibleConnections.length;

  if (graph.nodes.length === 0) {
    return <p>Your graph is empty. Saved notes appear here once processed.</p>;
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
              stroke="#cbd5f5"
              strokeWidth={2}
              strokeOpacity={0.35 + 0.45 * Math.min(Math.max(link.score, 0), 1)}
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
                <circle r={r} fill="url(#gradient-normal)" stroke="#3b82f6" strokeWidth={2.5} />
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
