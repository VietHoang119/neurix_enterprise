import type { NodeListItem } from "../lib/api/nodes";

const badgeStyle = {
  marginLeft: 8,
  padding: "2px 8px",
  borderRadius: 999,
  fontSize: 12,
  whiteSpace: "nowrap" as const,
};

function StatusBadge({ status }: { status: NodeListItem["status"] }) {
  // READY gets no badge on purpose — only in-flight or broken nodes stand out.
  if (status === "PROCESSING") {
    return <span style={{ ...badgeStyle, background: "#fef3c7", color: "#92400e" }}>⏳ Processing</span>;
  }
  if (status === "FAILED") {
    return <span style={{ ...badgeStyle, background: "#fee2e2", color: "#991b1b" }}>⚠️ Failed</span>;
  }
  return null;
}

export default function NodeList({ nodes }: { nodes: NodeListItem[] }) {
  if (nodes.length === 0) {
    return <p>No notes yet. Save one from Slack with <code>/neurix save &lt;text&gt;</code>.</p>;
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
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={node.title}>
            {node.title}
          </span>
          <StatusBadge status={node.status} />
        </li>
      ))}
    </ul>
  );
}
