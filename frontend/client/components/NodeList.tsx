import type { ReactNode } from "react";
import type { NodeStatus } from "../lib/api/nodes";
import FileButton from "./FileButton";

/** One list row: own notes, notes shared with you, or team-space notes. */
export interface NodeRow {
  id: string;
  title: string;
  status?: NodeStatus;
  can_open_file?: boolean;
  /** Small text after the title, e.g. who shared it. */
  detail?: string;
  /** Shown before the title, e.g. the owner's color dot. */
  lead?: ReactNode;
}

const badgeStyle = {
  marginLeft: 8,
  padding: "2px 8px",
  borderRadius: 999,
  fontSize: 12,
  whiteSpace: "nowrap" as const,
};

function StatusBadge({ status }: { status?: NodeStatus }) {
  // READY gets no badge on purpose — only in-flight or broken nodes stand out.
  if (status === "PROCESSING") {
    return <span style={{ ...badgeStyle, background: "#fef3c7", color: "#92400e" }}>⏳ Processing</span>;
  }
  if (status === "FAILED") {
    return <span style={{ ...badgeStyle, background: "#fee2e2", color: "#991b1b" }}>⚠️ Failed</span>;
  }
  return null;
}

const DEFAULT_EMPTY = (
  <p>
    No notes yet. Save one from Slack with <code>/neurix save &lt;text&gt;</code>.
  </p>
);

export default function NodeList({ nodes, empty = DEFAULT_EMPTY }: { nodes: NodeRow[]; empty?: ReactNode }) {
  if (nodes.length === 0) {
    return <>{empty}</>;
  }

  return (
    <ul style={{ listStyle: "none", margin: 0, padding: 0, maxHeight: 480, overflowY: "auto" }}>
      {nodes.map((node) => (
        <li
          key={node.id}
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: 8,
            padding: "8px 0",
            borderBottom: "1px solid #eee",
          }}
        >
          <span style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
            {node.lead}
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={node.title}>
              {node.title}
            </span>
            {node.detail && <small style={{ color: "#64748b", whiteSpace: "nowrap" }}>{node.detail}</small>}
          </span>
          <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <StatusBadge status={node.status} />
            {node.can_open_file && <FileButton nodeId={node.id} title={node.title} />}
          </span>
        </li>
      ))}
    </ul>
  );
}
