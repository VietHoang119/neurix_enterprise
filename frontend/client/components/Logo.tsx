import { cn } from "../lib/utils";

/**
 * The Neurix mark — neurix-ui's navbar logo (public/9.svg there), copied to
 * public/logo.svg. Swapping the asset is a change to that one file.
 */
export default function Logo({ className }: { className?: string }) {
  return <img src="/logo.svg" alt="" aria-hidden className={cn("object-contain", className)} />;
}
