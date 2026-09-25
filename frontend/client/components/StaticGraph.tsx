import { useMemo } from "react";
import type { ReactNode } from "react";
import type { GraphResponse } from "../lib/api/nodes";
import {
  computeStaticLayout,
  selectKeyHubs,
  selectTopConnections,
  type LayoutLink,
  type LayoutNode,
} from "../lib/graphLayout";

const TOP_N = 3;
const MAX_KEYS = 20;
const MAX_LABEL = 24;
// Edge colors follow neurix-ui's D3Graph: normal gray, share purple.
const EDGE_NORMAL = "#cbd5f5";
export const EDGE_SHARE = "#c084fc";
export const NODE_OWN = "#3b82f6";
// Key hubs, as in neurix-ui's D3Graph: a fuchsia diamond, pink dotted links.
export const KEY_FILL = "#f0abfc";
const KEY_STROKE = "#f5d0fe";
const KEY_EDGE = "#f472b6";
const KEY_LABEL = "#fdf4ff";
// A small diamond: a square rotated 45°.
const DIAMOND = "M 0,-8 L 8,0 L 0,8 L -8,0 Z";

function edgeStyle(link: LayoutLink, highlightCrossMember: boolean) {
  const opacity = 0.35 + 0.45 * Math.min(Math.max(link.score, 0), 1);
  if (highlightCrossMember ? link.crossMember : link.isShare) {
    // Distinct in color, width and dash, so it reads without color vision too.
    return {
      stroke: EDGE_SHARE,
      strokeWidth: 3.5,
      strokeDasharray: "8 4",
      strokeOpacity: Math.max(opacity, 0.8),
    };
  }
  return {
    stroke: EDGE_NORMAL,
    strokeWidth: 2,
    strokeDasharray: undefined,
    strokeOpacity: opacity,
  };
}

const truncate = (text: string) => (text.length > MAX_LABEL ? `${text.slice(0, MAX_LABEL - 1)}…` : text);

interface StaticGraphProps {
  graph: GraphResponse;
  /** Only each note's top-N connections unless this is on. */
  showAll: boolean;
  /** Draw shared keys as diamond nodes linked to their notes by dotted lines. */
  showKeys: boolean;
  /** Node outline color; defaults to blue for own notes, purple for shared ones. */
  colorFor?: (node: LayoutNode) => string;
  /** Team mode: highlight edges between two different owners (else: share edges). */
  highlightCrossMember?: boolean;
  emptyText?: string;
  /** Shown at the right of the caption under the canvas (e.g. the "Show all connections" toggle). */
  controls?: ReactNode;
}

function visibleConnections(graph: GraphResponse, showAll: boolean, highlightCrossMember: boolean) {
  if (showAll) return graph.connections;
  const top = new Set(selectTopConnections(graph.connections, TOP_N));
  // In team mode the cross-member edges are the point of the graph: never
  // declutter them away, whatever their score.
  return graph.connections.filter((c) => top.has(c) || (highlightCrossMember && c.cross_member));
}

/**
 * A graph drawn on the deep canvas, decluttered on purpose: a static layout
 * (simulated once, then frozen — no continuous ticking) and, by default,
 * only each node's top-N strongest connections. Colors follow neurix-ui's
 * D3Graph. Positions come from d3-force; React just draws them. Fills its
 * container's height.
 */
export default function StaticGraph({
  graph,
  showAll,
  showKeys,
  colorFor = (node) => (node.shared ? EDGE_SHARE : NODE_OWN),
  highlightCrossMember = false,
  emptyText = "Your graph is empty. Saved notes appear here once processed.",
  controls,
}: StaticGraphProps) {
  const visible = useMemo(
    () => visibleConnections(graph, showAll, highlightCrossMember),
    [graph, showAll, highlightCrossMember],
  );
  const keys = useMemo(() => (showKeys ? selectKeyHubs(graph.nodes, MAX_KEYS) : []), [graph.nodes, showKeys]);
  const layout = useMemo(() => computeStaticLayout(graph.nodes, visible, keys), [graph.nodes, visible, keys]);

  if (graph.nodes.length === 0) {
    return (
      <div className="flex h-full min-h-[240px] items-center justify-center rounded-lg border border-white/5 bg-graph-canvas p-6 text-center text-sm text-muted-foreground">
        {emptyText}
      </div>
    );
  }

  const { bounds } = layout;
  const hidden = graph.connections.length - visible.length;
  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <svg
        viewBox={`${bounds.x} ${bounds.y} ${bounds.width} ${bounds.height}`}
        className="min-h-0 w-full flex-1 rounded-lg border border-white/5 bg-graph-canvas"
        role="img"
        aria-label={`Graph of ${graph.nodes.length} notes`}
      >
        <defs>
          <radialGradient id="gradient-normal">
            <stop offset="0%" stopColor="#dbeafe" />
            <stop offset="100%" stopColor="#0f172a" />
          </radialGradient>
          <radialGradient id="gradient-key">
            <stop offset="0%" stopColor="#fff7ed" />
            <stop offset="55%" stopColor={KEY_FILL} />
            <stop offset="100%" stopColor="#c026d3" />
          </radialGradient>
        </defs>
        <g>
          {layout.keyLinks.map(({ hub, note }) => (
            <line
              key={`${hub.id}-${note.id}`}
              x1={hub.x}
              y1={hub.y}
              x2={note.x}
              y2={note.y}
              stroke={KEY_EDGE}
              strokeWidth={1}
              strokeDasharray="2 3"
              strokeOpacity={0.5}
            />
          ))}
        </g>
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
          {layout.hubs.map((hub) => (
            <g key={hub.id} transform={`translate(${hub.x ?? 0},${hub.y ?? 0})`}>
              <title>{`Key: ${hub.text} · in ${hub.noteIds.length} notes`}</title>
              <path d={DIAMOND} fill="url(#gradient-key)" stroke={KEY_STROKE} strokeWidth={1.5} />
              <text
                y={22}
                textAnchor="middle"
                fill={KEY_LABEL}
                fillOpacity={0.85}
                fontSize={10}
                fontWeight={600}
                letterSpacing="0.08em"
              >
                {truncate(hub.text)}
              </text>
            </g>
          ))}
        </g>
        <g>
          {layout.nodes.map((node) => {
            const r = layout.radius(node);
            const processing = node.status === "PROCESSING";
            return (
              <g key={node.id} transform={`translate(${node.x ?? 0},${node.y ?? 0})`} opacity={processing ? 0.5 : 1}>
                <title>{processing ? `${node.title} · Processing…` : node.title}</title>
                <circle r={r} fill="url(#gradient-normal)" stroke={colorFor(node)} strokeWidth={3} />
                <text y={r + 16} textAnchor="middle" fill="#e2e8f0" fontSize={12}>
                  {truncate(node.title)}
                </text>
              </g>
            );
          })}
        </g>
      </svg>
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-xs text-subtle-foreground">
        <span>
          {graph.nodes.length} notes · {visible.length} of {graph.connections.length} connections
          {!showAll && hidden > 0
            ? ` (top ${TOP_N} per note${highlightCrossMember ? " + every cross-member one" : ""})`
            : ""}
          {showKeys && ` · ${layout.hubs.length} shared ${layout.hubs.length === 1 ? "key" : "keys"}`}
        </span>
        {controls}
      </div>
    </div>
  );
}
