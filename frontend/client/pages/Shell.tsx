import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import type { User } from "firebase/auth";
import { ChevronDown, ExternalLink, LogOut, MessageCircle, RefreshCw, X } from "lucide-react";
import { signOut } from "../lib/firebase";
import { nodesApi, type GraphResponse, type NodeListResponse } from "../lib/api/nodes";
import { teamApi, type TeamGraphResponse, type TeamView } from "../lib/api/team";
import { initials, timeAgo } from "../lib/format";
import { memberColors } from "../lib/memberColors";
import { cn } from "../lib/utils";
import GraphPanel from "../components/GraphPanel";
import Logo from "../components/Logo";
import NodeList from "../components/NodeList";
import TeamManagement from "../components/TeamManagement";
import { Avatar, AvatarFallback, AvatarImage } from "../components/ui/avatar";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipTrigger } from "../components/ui/tooltip";

// While any note is still PROCESSING, re-fetch so its status clears (and its
// connections appear) without a manual reload.
const POLL_MS = 5000;

// Where the Slack OAuth install callback sends people back (?slack_install=…).
const INSTALL_NOTICES: Record<string, { ok: boolean; text: string }> = {
  linked: { ok: true, text: "Neurix is installed, and your team is now linked to this Slack workspace." },
  installed: {
    ok: true,
    text: "Neurix is installed. Your team links to this Slack workspace the first time a member runs a /neurix command there.",
  },
  error: { ok: false, text: "The Slack install didn't finish. Please try again." },
};

function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <h3 className="mb-1 mt-5 text-[11px] font-medium uppercase tracking-wider text-subtle-foreground">{children}</h3>
  );
}

function UserMenu({ user, onRefresh }: { user: User; onRefresh: () => void }) {
  // The person's name, never an id: Google gives displayName; fall back to the email.
  const name = user.displayName || user.email || "Your account";
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="flex items-center gap-2 rounded-full border border-border bg-card py-1 pl-1 pr-3 text-sm transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Avatar className="h-7 w-7">
            {user.photoURL && (
              <AvatarImage src={user.photoURL} alt="" className="object-cover" referrerPolicy="no-referrer" />
            )}
            <AvatarFallback className="bg-graph-note text-[11px] font-semibold text-white">
              {initials(name)}
            </AvatarFallback>
          </Avatar>
          <span className="max-w-[180px] truncate">{name}</span>
          <ChevronDown className="h-4 w-4 text-subtle-foreground" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        {user.email && user.email !== name && (
          <>
            <DropdownMenuLabel className="truncate text-xs font-normal text-muted-foreground">
              {user.email}
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
          </>
        )}
        <DropdownMenuItem onSelect={onRefresh}>
          <RefreshCw className="mr-2 h-4 w-4" />
          Refresh
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => signOut()}>
          <LogOut className="mr-2 h-4 w-4" />
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** Links to the team's own Slack workspace; disabled (with the reason) until one is linked. Never a fixed URL. */
function WorkspaceLink({ teamView }: { teamView: TeamView | null }) {
  const url = teamView?.org?.slack_workspace_url;
  if (url) {
    return (
      <Button
        asChild
        variant="outline"
        size="sm"
        className="h-8 rounded-full border-primary/40 bg-primary/10 text-violet-200 hover:bg-primary/20 hover:text-violet-100"
      >
        <a href={url} target="_blank" rel="noopener noreferrer">
          Open Neurix Workspace
          <ExternalLink className="h-3.5 w-3.5" />
        </a>
      </Button>
    );
  }
  const reason = teamView?.org
    ? "Your team isn't linked to a Slack workspace yet. It links the first time a member runs a /neurix command in Slack."
    : "Join or create a team to link its Slack workspace.";
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        {/* A disabled button fires no hover events; the wrapper carries the tooltip. */}
        <span
          tabIndex={0}
          className="rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Button variant="outline" size="sm" disabled className="pointer-events-none h-8 rounded-full bg-transparent">
            Open Neurix Workspace
          </Button>
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-72 text-xs">{reason}</TooltipContent>
    </Tooltip>
  );
}

function Stat({ value, label, title }: { value: number | string; label: string; title?: string }) {
  return (
    <div className="min-w-0 flex-1 px-3 first:pl-0 last:pr-0" title={title}>
      <p className="text-xl font-semibold tabular-nums leading-tight">{value}</p>
      <p className="truncate text-xs text-subtle-foreground">{label}</p>
    </div>
  );
}

export default function Shell({ user }: { user: User }) {
  const [nodes, setNodes] = useState<NodeListResponse | null>(null);
  const [graph, setGraph] = useState<GraphResponse | null>(null);
  const [team, setTeam] = useState<TeamGraphResponse | null>(null);
  const [teamView, setTeamView] = useState<TeamView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const [list, graphData, teamData, teamViewData] = await Promise.all([
        nodesApi.list(),
        nodesApi.graph(),
        teamApi.graph(),
        teamApi.get(),
      ]);
      setNodes(list);
      setGraph(graphData);
      setTeam(teamData);
      setTeamView(teamViewData);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't load your nodes. Please try again.");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Back from the Slack install: say how it went once, then drop the param.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const outcome = params.get("slack_install");
    if (!outcome) return;
    setNotice(INSTALL_NOTICES[outcome] ?? null);
    params.delete("slack_install");
    const query = params.toString();
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`,
    );
  }, []);

  // A membership change also changes the team graph (and what it shows).
  const onTeamChange = useCallback((view: TeamView) => {
    setTeamView(view);
    teamApi
      .graph()
      .then(setTeam)
      .catch(() => undefined);
  }, []);

  const hasProcessing = nodes?.nodes.some((n) => n.status === "PROCESSING") ?? false;
  useEffect(() => {
    if (!hasProcessing) return;
    const timer = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(timer);
  }, [hasProcessing, load]);

  const inTeamSpace = useMemo(() => new Set(team?.nodes.map((n) => n.id) ?? []), [team]);
  const teamColor = useMemo(() => memberColors(team?.members ?? []), [team?.members]);
  // Re-keys the list (a subtle fade-in) only when what it shows changed, not on every poll.
  const listVersion = useMemo(
    () => (nodes ? [...nodes.nodes, ...nodes.shared_with_me].map((n) => `${n.id}:${n.status}`).join("|") : ""),
    [nodes],
  );

  const myNotes = nodes?.nodes.map((n) => ({
    ...n,
    detail:
      [inTeamSpace.has(n.id) ? "Shared to team" : null, timeAgo(n.created_at)].filter(Boolean).join(" · ") || undefined,
  }));
  const names = new Map(team?.members.map((m) => [m.user_id, m.name]) ?? []);
  const meId = team?.members.find((m) => m.is_me)?.user_id;
  // Others' notes only: your own team-space notes are already above, marked "Shared to team".
  const teamNotes = (team?.nodes ?? [])
    .filter((n) => n.owner_id !== meId)
    .map((n) => ({
      id: n.id,
      title: n.title,
      status: n.status,
      can_open_file: n.can_open_file,
      detail: `from ${names.get(n.owner_id) ?? "a former member"}`,
      lead: (
        <span className="h-2 w-2 flex-none rounded-full" style={{ background: teamColor(n.owner_id) }} aria-hidden />
      ),
    }));
  const sharedToTeam = nodes?.nodes.filter((n) => inTeamSpace.has(n.id)).length ?? 0;

  return (
    <div className="flex min-h-screen flex-col lg:h-screen">
      <header className="flex flex-none items-center justify-between gap-4 px-4 py-4 sm:px-6">
        <div className="flex items-center gap-2">
          <Logo className="h-6 w-6" />
          <span className="text-lg font-semibold tracking-tight">Neurix</span>
          <span className="hidden text-sm text-subtle-foreground sm:inline">Enterprise</span>
        </div>
        <UserMenu user={user} onRefresh={load} />
      </header>

      {(error || notice) && (
        <div className="space-y-2 px-4 pb-4 sm:px-6">
          {error && (
            <p
              role="alert"
              className="rounded-lg border border-destructive/50 bg-destructive/15 px-4 py-2 text-sm text-red-200"
            >
              {error}
            </p>
          )}
          {notice && (
            <div
              role="status"
              className={cn(
                "flex items-center justify-between gap-3 rounded-lg border px-4 py-2 text-sm",
                notice.ok
                  ? "border-primary/40 bg-primary/10 text-violet-100"
                  : "border-destructive/50 bg-destructive/15 text-red-200",
              )}
            >
              {notice.text}
              <button
                type="button"
                onClick={() => setNotice(null)}
                aria-label="Dismiss"
                className="text-current/70 hover:text-current"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          )}
        </div>
      )}

      {/* 300px | 1fr | 320px over 1fr | 200px on wide screens; one column below. */}
      <main className="grid min-h-0 flex-1 grid-cols-1 gap-5 px-4 pb-6 sm:px-6 lg:grid-cols-[300px_minmax(0,1fr)_320px] lg:grid-rows-[minmax(0,1fr)_200px]">
        <Card className="flex min-h-0 flex-col p-5 lg:col-start-1 lg:row-span-2 lg:row-start-1">
          <div className="mb-2 flex items-baseline justify-between gap-2">
            <h2 className="text-base font-semibold">Knowledge Management</h2>
            {nodes && <span className="text-sm tabular-nums text-subtle-foreground">{nodes.nodes.length}</span>}
          </div>
          {nodes ? (
            <div
              key={listVersion}
              className="-mr-2 min-h-0 flex-1 overflow-y-auto pr-2 duration-300 animate-in fade-in"
            >
              <NodeList nodes={myNotes ?? []} />
              {nodes.shared_with_me.length > 0 && (
                <>
                  <SectionLabel>Shared with me</SectionLabel>
                  <NodeList
                    nodes={nodes.shared_with_me.map((n) => ({
                      ...n,
                      detail: n.owner_email ? `from ${n.owner_email}` : undefined,
                    }))}
                  />
                </>
              )}
              {teamNotes.length > 0 && (
                <>
                  <SectionLabel>Team space</SectionLabel>
                  <NodeList nodes={teamNotes} />
                </>
              )}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">Loading…</p>
          )}
        </Card>

        <GraphPanel graph={graph} team={team} className="min-h-[520px] lg:col-start-2 lg:row-start-1 lg:min-h-0" />

        <Card className="min-h-0 overflow-y-auto p-5 lg:col-start-3 lg:row-start-1">
          {teamView ? (
            <TeamManagement team={teamView} onChange={onTeamChange} />
          ) : (
            <p className="text-sm text-muted-foreground">Loading…</p>
          )}
        </Card>

        <Card className="flex flex-col gap-3 p-5 lg:col-start-2 lg:row-start-2">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="flex items-center gap-2 text-base font-semibold">
              <MessageCircle className="h-4 w-4 text-muted-foreground" />
              Ask
            </h2>
            <WorkspaceLink teamView={teamView} />
          </div>
          <p className="text-sm text-muted-foreground">
            Ask your team's knowledge with <code>/neurix ask &lt;question&gt;</code> in Slack. Answers cite the exact
            nodes they came from.
          </p>
        </Card>

        <Card className="flex flex-col gap-3 p-5 lg:col-start-3 lg:row-start-2">
          <h2 className="text-base font-semibold">Insights</h2>
          <div className="flex divide-x divide-border">
            <Stat value={nodes?.nodes.length ?? "–"} label="nodes" />
            <Stat
              value={graph?.connections.length ?? "–"}
              label="connections"
              title="Connections in your personal graph"
            />
            <Stat value={nodes ? sharedToTeam : "–"} label="shared" title="Your nodes in the team space" />
          </div>
          <p className="text-sm text-muted-foreground">
            Run <code>/neurix stats</code> in Slack for the full team breakdown.
          </p>
        </Card>
      </main>
    </div>
  );
}
