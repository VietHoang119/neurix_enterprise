import { useState } from "react";
import { FileText, Loader2 } from "lucide-react";
import { nodesApi } from "../lib/api/nodes";
import { cn } from "../lib/utils";
import { Button } from "./ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/tooltip";

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
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={open}
          disabled={busy}
          aria-label={`Open the original file of ${title}`}
          className={cn(
            "h-7 w-7 flex-none text-subtle-foreground hover:text-foreground",
            error && "text-status-failed",
          )}
        >
          {busy ? <Loader2 className="animate-spin" /> : <FileText />}
        </Button>
      </TooltipTrigger>
      <TooltipContent className={cn("max-w-64 text-xs", error && "text-status-failed")}>
        {error ?? "Open original file"}
      </TooltipContent>
    </Tooltip>
  );
}
