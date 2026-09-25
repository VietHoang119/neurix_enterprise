import { useState } from "react";
import { nodesApi } from "../lib/api/nodes";

/**
 * Opens a note's original file in a new tab, through a short-lived signed
 * URL (the same link as Slack's `/neurix file`). Render it only for notes
 * whose `can_open_file` is true.
 */
export default function FileButton({ nodeId, title }: { nodeId: string; title: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const open = async () => {
    // Open the tab inside the click so popup blockers allow it, then point
    // it at the signed URL once we have it.
    const tab = window.open("", "_blank");
    setBusy(true);
    setError(null);
    try {
      const { url } = await nodesApi.fileLink(nodeId);
      if (tab) {
        tab.opener = null;
        tab.location.href = url;
      } else {
        window.location.assign(url);
      }
    } catch (err) {
      tab?.close();
      setError(err instanceof Error ? err.message : "Couldn't get the file");
    } finally {
      setBusy(false);
    }
  };

  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <button
        type="button"
        onClick={open}
        disabled={busy}
        title={`Open the original file of ${title}`}
        aria-label={`Open the original file of ${title}`}
        style={{ padding: "2px 8px", fontSize: 13, whiteSpace: "nowrap" }}
      >
        {busy ? "…" : "📄 File"}
      </button>
      {error && <small style={{ color: "#b91c1c" }}>{error}</small>}
    </span>
  );
}
