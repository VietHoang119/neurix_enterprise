import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import * as d3 from "d3";
import { Minus, Plus } from "lucide-react";
import type { GraphResponse } from "../lib/api/nodes";
import {
  computeStaticLayout,
  selectKeyHubs,
  selectTopConnections,
  selectVisibleLabels,
  type LabelCandidate,
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

// Below this zoom level, only the non-overlapping "safe" label set (from
// selectVisibleLabels) is shown; past it every label is shown, since by
// then the zoomed-in spacing between nodes outgrows their (constant
// on-screen size) labels.
const LABEL_REVEAL_ZOOM = 1.6;
const ZOOM_MIN = 0.5;
const ZOOM_MAX = 6;
const BASE_FONT_SIZE = 12;

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

interface HoveredNode {
  id: string;
  title: string;
  owner: string;
  clientX: number;
  clientY: number;
}

interface StaticGraphProps {
  graph: GraphResponse;
  /** Only each note's top-N connections unless this is on. */
  showAll: boolean;
  /** Draw shared keys as diamond nodes linked to their notes by dotted lines. */
  showKeys: boolean;
  /** Node outline color; defaults to blue for own notes, purple for shared ones. */
  colorFor?: (node: LayoutNode) => string;
  /** Label for the hover tooltip's "owner" line. */
  ownerLabel?: (node: LayoutNode) => string;
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
 * container's height. Pannable/zoomable via d3-zoom (mouse wheel, trackpad
 * pinch, drag, and the on-screen +/- buttons); hovering (or tapping) a node
 * shows its full name and owner in a small floating tooltip.
 */
export default function StaticGraph({
  graph,
  showAll,
  showKeys,
  colorFor = (node) => (node.shared ? EDGE_SHARE : NODE_OWN),
  ownerLabel,
  highlightCrossMember = false,
  emptyText = "Your graph is empty. Saved nodes appear here once processed.",
  controls,
}: StaticGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const zoomRef = useRef<d3.ZoomBehavior<SVGSVGElement, unknown> | null>(null);
  const [transform, setTransform] = useState(d3.zoomIdentity);
  const [hovered, setHovered] = useState<HoveredNode | null>(null);
  // Dragged positions, keyed by node/hub id. Committed (drag-end) only —
  // matches neurix-ui's D3Graph, which doesn't persist drag positions beyond
  // the component's own in-memory simulation state, so a resize/refetch of
  // the graph (the reset effect below) drops them too.
  const [positions, setPositions] = useState<Record<string, { x: number; y: number }>>({});
  // Live position of whichever node/hub is actively being dragged, updated
  // on every pointer move so it visibly tracks the cursor. Kept separate from
  // `positions` so the expensive label-overlap recompute (which depends on
  // `positions`) only runs once per drag, at drag end, not every frame.
  const [dragPreview, setDragPreview] = useState<{ id: string; x: number; y: number } | null>(null);

  const visible = useMemo(
    () => visibleConnections(graph, showAll, highlightCrossMember),
    [graph, showAll, highlightCrossMember],
  );
  const keys = useMemo(() => (showKeys ? selectKeyHubs(graph.nodes, MAX_KEYS) : []), [graph.nodes, showKeys]);
  const layout = useMemo(() => computeStaticLayout(graph.nodes, visible, keys), [graph.nodes, visible, keys]);

  /** A node/hub's committed position (post drag-end), falling back to its static layout position. */
  const committedPos = (id: string, x: number, y: number) => positions[id] ?? { x, y };
  /** Same, but also applying the in-progress drag preview for whichever item is currently being dragged. */
  const effectivePos = (id: string, x: number, y: number) =>
    dragPreview?.id === id ? { x: dragPreview.x, y: dragPreview.y } : committedPos(id, x, y);

  const safeLabels = useMemo(() => {
    const hubCandidates: LabelCandidate[] = layout.hubs.map((hub) => {
      const p = committedPos(hub.id, hub.x ?? 0, hub.y ?? 0);
      return {
        id: hub.id,
        x: p.x,
        y: p.y,
        text: truncate(hub.text),
        labelY: p.y + 22,
      };
    });
    const nodeCandidates: LabelCandidate[] = [...layout.nodes]
      .sort((a, b) => b.degree - a.degree)
      .map((node) => {
        const p = committedPos(node.id, node.x ?? 0, node.y ?? 0);
        return {
          id: node.id,
          x: p.x,
          y: p.y,
          text: truncate(node.title),
          labelY: p.y + layout.radius(node) + 16,
        };
      });
    return selectVisibleLabels([...hubCandidates, ...nodeCandidates]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layout, positions]);

  // Drag-to-reposition, matching neurix-ui's D3Graph: a d3.drag() behavior
  // per node. Attached via a stable per-id ref callback (a Map, so it isn't
  // re-bound on every render) rather than D3Graph's imperative DOM mutation,
  // since StaticGraph renders from React state. d3-drag's default container
  // is the dragged element's parentNode; here that's the untransformed <g>
  // wrapping the nodes/hubs, one level inside the pan/zoom <g>, so
  // event.x/event.y already come out in graph space — the current zoom scale
  // and pan offset are accounted for automatically, the same way D3Graph
  // gets it "for free" from the DOM's own CTM.
  const dragRefCache = useRef(new Map<string, (el: SVGGElement | null) => void>());
  const dragBehavior = useMemo(
    () =>
      d3
        .drag<SVGGElement, { id: string }>()
        .on("drag", (event, d) => {
          setDragPreview({ id: d.id, x: event.x, y: event.y });
        })
        .on("end", (event, d) => {
          setPositions((prev) => ({ ...prev, [d.id]: { x: event.x, y: event.y } }));
          setDragPreview(null);
        }),
    [],
  );
  const getDragRef = (id: string) => {
    let ref = dragRefCache.current.get(id);
    if (!ref) {
      ref = (el) => {
        if (el) d3.select<SVGGElement, { id: string }>(el).datum({ id }).call(dragBehavior);
      };
      dragRefCache.current.set(id, ref);
    }
    return ref;
  };

  useEffect(() => {
    // Reset pan/zoom, hover, and any dragged positions whenever the underlying
    // graph changes shape. Drag positions aren't persisted (neither is
    // neurix-ui's), so a reload/refetch of the graph drops them, same as zoom.
    setTransform(d3.zoomIdentity);
    setHovered(null);
    setPositions({});
    setDragPreview(null);
  }, [graph, showAll, showKeys, highlightCrossMember]);

  useEffect(() => {
    if (!svgRef.current) return;
    const zoom = d3
      .zoom<SVGSVGElement, unknown>()
      .scaleExtent([ZOOM_MIN, ZOOM_MAX])
      .filter((event) => {
        // Allow wheel/touch/drag, but not the browser's native dblclick-to-zoom
        // (it would fight with node clicks).
        if (event.type === "dblclick") return false;
        return !event.button;
      })
      .on("zoom", (event) => setTransform(event.transform));
    const svg = d3.select(svgRef.current);
    svg.call(zoom);
    zoomRef.current = zoom;
    return () => {
      svg.on(".zoom", null);
    };
  }, []);

  const zoomBy = (factor: number) => {
    if (!svgRef.current || !zoomRef.current) return;
    d3.select(svgRef.current).transition().duration(200).call(zoomRef.current.scaleBy, factor);
  };

  if (graph.nodes.length === 0) {
    return (
      <div className="flex h-full min-h-[240px] items-center justify-center rounded-lg border border-white/5 bg-graph-canvas p-6 text-center text-sm text-muted-foreground">
        {emptyText}
      </div>
    );
  }

  const { bounds } = layout;
  const hidden = graph.connections.length - visible.length;
  const k = transform.k;
  const fontSize = k >= 1 ? BASE_FONT_SIZE / k : BASE_FONT_SIZE;
  const showAllLabels = k >= LABEL_REVEAL_ZOOM;
  const labelVisible = (id: string) => showAllLabels || safeLabels.has(id) || hovered?.id === id;

  const showNodeTooltip = (node: LayoutNode, event: React.MouseEvent) => {
    setHovered({
      id: node.id,
      title: node.title,
      owner: ownerLabel?.(node) ?? "",
      clientX: event.clientX,
      clientY: event.clientY,
    });
  };

  return (
    <div className="relative flex h-full min-h-0 flex-col gap-2">
      <div ref={containerRef} className="relative min-h-0 w-full flex-1">
        <svg
          ref={svgRef}
          viewBox={`${bounds.x} ${bounds.y} ${bounds.width} ${bounds.height}`}
          className="h-full w-full touch-none rounded-lg border border-white/5 bg-graph-canvas"
          role="img"
          aria-label={`Graph of ${graph.nodes.length} nodes`}
          onClick={(e) => {
            if (e.target === svgRef.current) setHovered(null);
          }}
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
          <g transform={`translate(${transform.x},${transform.y}) scale(${transform.k})`}>
            <g>
              {layout.keyLinks.map(({ hub, note }) => {
                const h = effectivePos(hub.id, hub.x ?? 0, hub.y ?? 0);
                const n = effectivePos(note.id, note.x ?? 0, note.y ?? 0);
                return (
                  <line
                    key={`${hub.id}-${note.id}`}
                    x1={h.x}
                    y1={h.y}
                    x2={n.x}
                    y2={n.y}
                    stroke={KEY_EDGE}
                    strokeWidth={1}
                    strokeDasharray="2 3"
                    strokeOpacity={0.5}
                  />
                );
              })}
            </g>
            <g>
              {layout.links.map((link) => {
                const s = effectivePos(link.source.id, link.source.x ?? 0, link.source.y ?? 0);
                const t = effectivePos(link.target.id, link.target.x ?? 0, link.target.y ?? 0);
                return (
                  <line
                    key={`${link.source.id}-${link.target.id}`}
                    x1={s.x}
                    y1={s.y}
                    x2={t.x}
                    y2={t.y}
                    {...edgeStyle(link, highlightCrossMember)}
                  />
                );
              })}
            </g>
            <g>
              {layout.hubs.map((hub) => {
                const pos = effectivePos(hub.id, hub.x ?? 0, hub.y ?? 0);
                return (
                  <g
                    key={hub.id}
                    ref={getDragRef(hub.id)}
                    transform={`translate(${pos.x},${pos.y})`}
                    style={{ cursor: "grab" }}
                  >
                    <title>{`Key: ${hub.text} · in ${hub.noteIds.length} nodes`}</title>
                    <path d={DIAMOND} fill="url(#gradient-key)" stroke={KEY_STROKE} strokeWidth={1.5} />
                    {labelVisible(hub.id) && (
                      <text
                        y={22}
                        textAnchor="middle"
                        fill={KEY_LABEL}
                        fillOpacity={0.85}
                        fontSize={fontSize}
                        fontWeight={600}
                        letterSpacing="0.08em"
                      >
                        {truncate(hub.text)}
                      </text>
                    )}
                  </g>
                );
              })}
            </g>
            <g>
              {layout.nodes.map((node) => {
                const r = layout.radius(node);
                const processing = node.status === "PROCESSING";
                const pos = effectivePos(node.id, node.x ?? 0, node.y ?? 0);
                return (
                  <g
                    key={node.id}
                    ref={getDragRef(node.id)}
                    transform={`translate(${pos.x},${pos.y})`}
                    opacity={processing ? 0.5 : 1}
                    style={{ cursor: "grab" }}
                    onMouseEnter={(e) => showNodeTooltip(node, e)}
                    onMouseMove={(e) => setHovered((h) => (h && h.id === node.id ? { ...h, clientX: e.clientX, clientY: e.clientY } : h))}
                    onMouseLeave={() => setHovered((h) => (h?.id === node.id ? null : h))}
                    onClick={(e) => {
                      e.stopPropagation();
                      if (hovered?.id === node.id) {
                        setHovered(null);
                      } else {
                        showNodeTooltip(node, e);
                      }
                    }}
                  >
                    <circle r={r} fill="url(#gradient-normal)" stroke={colorFor(node)} strokeWidth={3} />
                    {labelVisible(node.id) && (
                      <text y={r + 16} textAnchor="middle" fill="#e2e8f0" fontSize={fontSize}>
                        {truncate(node.title)}
                      </text>
                    )}
                  </g>
                );
              })}
            </g>
          </g>
        </svg>

        <div className="absolute right-2 top-2 flex flex-col gap-1">
          <button
            type="button"
            aria-label="Zoom in"
            onClick={() => zoomBy(1.4)}
            className="flex h-7 w-7 items-center justify-center rounded-md border border-white/10 bg-background/80 text-foreground/80 backdrop-blur hover:bg-background hover:text-foreground"
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            aria-label="Zoom out"
            onClick={() => zoomBy(1 / 1.4)}
            className="flex h-7 w-7 items-center justify-center rounded-md border border-white/10 bg-background/80 text-foreground/80 backdrop-blur hover:bg-background hover:text-foreground"
          >
            <Minus className="h-3.5 w-3.5" />
          </button>
        </div>

        {hovered && containerRef.current && (
          <div
            className="pointer-events-none absolute z-10 max-w-[220px] rounded-md border border-white/10 bg-popover px-2.5 py-1.5 text-xs text-popover-foreground shadow-lg"
            style={{
              left: hovered.clientX - containerRef.current.getBoundingClientRect().left + 12,
              top: hovered.clientY - containerRef.current.getBoundingClientRect().top + 12,
            }}
          >
            <p className="font-medium leading-snug">{hovered.title}</p>
            {hovered.owner && <p className="text-subtle-foreground">{hovered.owner}</p>}
          </div>
        )}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-xs text-subtle-foreground">
        <span>
          {graph.nodes.length} nodes · {visible.length} of {graph.connections.length} connections
          {!showAll && hidden > 0
            ? ` (top ${TOP_N} per node${highlightCrossMember ? " + every cross-member one" : ""})`
            : ""}
          {showKeys && ` · ${layout.hubs.length} shared ${layout.hubs.length === 1 ? "key" : "keys"}`}
        </span>
        {controls}
      </div>
    </div>
  );
}
