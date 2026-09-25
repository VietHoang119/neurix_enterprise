import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import type { GraphResponse } from "../lib/api/nodes";
import type { TeamGraphResponse } from "../lib/api/team";
import { memberColors, FORMER_MEMBER } from "../lib/memberColors";
import { cn } from "../lib/utils";
import StaticGraph, { EDGE_SHARE, KEY_FILL, NODE_OWN } from "./StaticGraph";
import { Card } from "./ui/card";
import { Switch } from "./ui/switch";
import { Tabs, TabsList, TabsTrigger } from "./ui/tabs";

type GraphTab = "personal" | "team";

const Dot = ({ color }: { color: string }) => (
  <span className="h-2.5 w-2.5 flex-none rounded-full" style={{ background: color }} aria-hidden />
);

const Diamond = () => (
  <span className="h-2 w-2 flex-none rotate-45 rounded-[1px]" style={{ background: KEY_FILL }} aria-hidden />
);

function LegendItem({ children, faded = false }: { children: ReactNode; faded?: boolean }) {
  return <span className={cn("inline-flex items-center gap-1.5", faded && "opacity-50")}>{children}</span>;
}

function PersonalLegend({ graph }: { graph: GraphResponse }) {
  return (
    <>
      <LegendItem>
        <Dot color={NODE_OWN} />
        Node
      </LegendItem>
      {graph.nodes.some((n) => n.shared) && (
        <LegendItem>
          <Dot color={EDGE_SHARE} />
          Shared with you
        </LegendItem>
      )}
      <LegendItem>
        <Diamond />
        Key
      </LegendItem>
    </>
  );
}

/** The team graph's existing legend: one color per member, former members, cross-member edges. */
function TeamLegend({ team, showKeys }: { team: TeamGraphResponse; showKeys: boolean }) {
  const colorOf = memberColors(team.members);
  const contributors = new Set(team.nodes.map((n) => n.owner_id));
  const hasFormer = team.nodes.some((n) => colorOf(n.owner_id) === FORMER_MEMBER);
  const crossMember = team.connections.filter((c) => c.cross_member).length;
  return (
    <>
      {team.members.map((m) => (
        <LegendItem key={m.user_id} faded={!contributors.has(m.user_id)}>
          <Dot color={colorOf(m.user_id)} />
          {m.is_me ? "you" : m.name}
        </LegendItem>
      ))}
      {hasFormer && (
        <LegendItem>
          <Dot color={FORMER_MEMBER} />
          former member
        </LegendItem>
      )}
      <LegendItem>
        <svg width={22} height={8} aria-hidden>
          <line x1={0} y1={4} x2={22} y2={4} stroke={EDGE_SHARE} strokeWidth={2.5} strokeDasharray="6 3" />
        </svg>
        cross-member ({crossMember})
      </LegendItem>
      {showKeys && (
        <LegendItem>
          <Diamond />
          Key
        </LegendItem>
      )}
    </>
  );
}

/**
 * The graph panel: a Personal / Team tab and a "Show keys" toggle, both
 * real state. "Show keys" and "Show all connections" are independent and
 * carry across tabs.
 */
export default function GraphPanel({
  graph,
  team,
  className,
}: {
  graph: GraphResponse | null;
  team: TeamGraphResponse | null;
  className?: string;
}) {
  const [activeGraphTab, setActiveGraphTab] = useState<GraphTab>("personal");
  const [showKeys, setShowKeys] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const teamColor = useMemo(() => memberColors(team?.members ?? []), [team?.members]);

  const showAllToggle = (
    <label className="inline-flex cursor-pointer items-center gap-2">
      <Switch
        checked={showAll}
        onCheckedChange={setShowAll}
        className="h-4 w-7 [&>span]:h-3 [&>span]:w-3 [&>span]:data-[state=checked]:translate-x-3"
      />
      Show all connections
    </label>
  );

  let legend: ReactNode = null;
  let body: ReactNode = <p className="text-sm text-muted-foreground">Loading…</p>;
  if (activeGraphTab === "personal" && graph) {
    legend = <PersonalLegend graph={graph} />;
    body = <StaticGraph graph={graph} showAll={showAll} showKeys={showKeys} controls={showAllToggle} />;
  } else if (activeGraphTab === "team" && team) {
    if (!team.org) {
      body = (
        <div className="flex h-full min-h-[240px] items-center justify-center rounded-lg border border-white/5 bg-graph-canvas p-6 text-center text-sm text-muted-foreground">
          You're not in a team yet. Create one in the Team panel.
        </div>
      );
    } else {
      legend = <TeamLegend team={team} showKeys={showKeys} />;
      body = (
        <StaticGraph
          graph={team}
          showAll={showAll}
          showKeys={showKeys}
          colorFor={(node) => teamColor(node.owner_id)}
          highlightCrossMember
          emptyText="The team space is empty. Add a node from Slack with /neurix share-team."
          controls={showAllToggle}
        />
      );
    }
  }

  return (
    <Card className={cn("flex min-h-0 flex-col gap-3 p-5", className)}>
      <h2 className="sr-only">Graph</h2>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Tabs value={activeGraphTab} onValueChange={(v) => setActiveGraphTab(v as GraphTab)}>
          <TabsList className="h-9 bg-background/60">
            <TabsTrigger value="personal" className="px-3 text-[13px]">
              Personal graph
            </TabsTrigger>
            <TabsTrigger value="team" className="px-3 text-[13px]">
              Team graph
            </TabsTrigger>
          </TabsList>
        </Tabs>
        <label className="inline-flex cursor-pointer items-center gap-2 text-[13px] text-muted-foreground">
          <Diamond />
          Show keys
          <Switch checked={showKeys} onCheckedChange={setShowKeys} aria-label="Show keys" />
        </label>
      </div>
      {legend && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
          {activeGraphTab === "team" && team?.org && (
            <span className="font-medium text-foreground">{team.org.name}</span>
          )}
          {legend}
        </div>
      )}
      <div className="min-h-[320px] flex-1">{body}</div>
    </Card>
  );
}
