import type { ReactNode } from "react";
import type { NodeStatus } from "../lib/api/nodes";
import { statusLabel } from "../lib/format";
import { cn } from "../lib/utils";
import FileButton from "./FileButton";

/** One list row: own notes, notes shared with you, or team-space notes. */
export interface NodeRow {
  id: string;
  title: string;
  status?: NodeStatus;
  can_open_file?: boolean;
  /** Second line, e.g. "Shared to team · 2h ago" or who shared it. A non-ready status replaces it. */
  detail?: string;
  /** Shown before the title, e.g. the owner's color dot. */
  lead?: ReactNode;
}

function StatusDot({ status }: { status?: NodeStatus }) {
  // READY gets no dot on purpose — only in-flight or broken notes stand out.
  if (status !== "PROCESSING" && status !== "FAILED") return null;
  return (
    <span
      className={cn(
        "h-1.5 w-1.5 flex-none rounded-full",
        status === "PROCESSING" ? "bg-status-processing" : "bg-status-failed",
      )}
      aria-hidden
    />
  );
}

const DEFAULT_EMPTY = (
  <p className="py-2 text-sm text-muted-foreground">
    No notes yet. Save one from Slack with <code>/neurix save &lt;text&gt;</code>.
  </p>
);

/** A plain row list with dividers — no card per item. */
export default function NodeList({ nodes, empty = DEFAULT_EMPTY }: { nodes: NodeRow[]; empty?: ReactNode }) {
  if (nodes.length === 0) {
    return <>{empty}</>;
  }

  return (
    <ul className="divide-y divide-border">
      {nodes.map((node) => {
        const status = statusLabel(node.status);
        const second = status ?? node.detail;
        return (
          <li
            key={node.id}
            className="-mx-2 flex items-center gap-3 rounded-md px-2 py-2.5 transition-colors hover:bg-white/[0.03]"
          >
            {node.lead}
            <div className="min-w-0 flex-1">
              {/* Long titles end in an ellipsis; the full title is the hover tooltip. */}
              <p className="truncate text-sm text-foreground" title={node.title}>
                {node.title}
              </p>
              {second && (
                <p
                  className={cn(
                    "truncate text-xs",
                    node.status === "FAILED" ? "text-status-failed" : "text-subtle-foreground",
                  )}
                  title={second}
                >
                  {second}
                </p>
              )}
            </div>
            <StatusDot status={node.status} />
            {node.can_open_file && <FileButton nodeId={node.id} title={node.title} />}
          </li>
        );
      })}
    </ul>
  );
}
