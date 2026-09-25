import type { NodeStatus } from "./api/nodes";

/** Friendly label for a note status; READY has none on purpose. Never show the raw enum. */
export function statusLabel(status?: NodeStatus): string | null {
  switch (status) {
    case "PROCESSING":
      return "Processing…";
    case "FAILED":
      return "Couldn't process";
    case "DRAFT":
      return "Draft";
    default:
      return null;
  }
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** "just now", "5m ago", "2h ago", "3d ago", then a short date. */
export function timeAgo(iso: string | null | undefined, now = Date.now()): string | null {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;
  const diff = Math.max(now - then, 0);
  if (diff < MINUTE) return "just now";
  if (diff < HOUR) return `${Math.floor(diff / MINUTE)}m ago`;
  if (diff < DAY) return `${Math.floor(diff / HOUR)}h ago`;
  if (diff < 7 * DAY) return `${Math.floor(diff / DAY)}d ago`;
  const date = new Date(then);
  const sameYear = date.getFullYear() === new Date(now).getFullYear();
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric", ...(sameYear ? {} : { year: "numeric" }) });
}

/** Up to two initials for an avatar: "Hoàng Nguyễn" -> "HN", "bob@x.com" -> "B". */
export function initials(name: string): string {
  const words = name
    .replace(/@.*/, "")
    .split(/[\s._-]+/)
    .filter(Boolean);
  const letters = words.length > 1 ? [words[0], words[words.length - 1]] : words.slice(0, 1);
  return letters.map((w) => Array.from(w)[0]?.toUpperCase() ?? "").join("") || "?";
}
