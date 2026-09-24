import { useMemo } from "react";
import type { TeamGraphResponse } from "../lib/api/team";
import StaticGraph from "./StaticGraph";

// One color per contributing member, in member order (Tableau 10, readable
// on the graph's dark background).
const PALETTE = ["#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2", "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac"];

/**
 * The team space: notes shared to the org (references to the originals),
 * colored by original owner, with cross-member connections drawn as dashed
 * purple edges. Same static layout as the personal graph.
 */
export default function TeamGraph({ team }: { team: TeamGraphResponse }) {
  const colors = useMemo(
    () => new Map(team.members.map((m, i) => [m.user_id, PALETTE[i % PALETTE.length]])),
    [team.members],
  );

  if (!team.org) {
    return (
      <p>
        You're not in a team yet. Create one from Slack with <code>/neurix team create &lt;name&gt;</code>.
      </p>
    );
  }

  const contributors = new Set(team.nodes.map((n) => n.owner_id));
  const crossMember = team.connections.filter((c) => c.cross_member).length;
  return (
    <div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginBottom: 8, fontSize: 14 }}>
        <strong>{team.org.name}</strong>
        {team.members.map((m) => (
          <span key={m.user_id} style={{ display: "inline-flex", alignItems: "center", gap: 6, opacity: contributors.has(m.user_id) ? 1 : 0.5 }}>
            <span style={{ width: 12, height: 12, borderRadius: 999, background: colors.get(m.user_id) }} />
            {m.name}
            {m.is_me ? " (you)" : ""}
          </span>
        ))}
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <svg width={28} height={8} aria-hidden>
            <line x1={0} y1={4} x2={28} y2={4} stroke="#c084fc" strokeWidth={3} strokeDasharray="8 4" />
          </svg>
          cross-member ({crossMember})
        </span>
      </div>
      <StaticGraph
        graph={team}
        colorFor={(node) => colors.get(node.owner_id) ?? "#94a3b8"}
        highlightCrossMember
        emptyText="The team space is empty. Add a note from Slack with /neurix share-team."
      />
    </div>
  );
}
