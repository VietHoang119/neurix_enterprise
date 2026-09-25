import { useMemo } from "react";
import type { TeamGraphResponse } from "../lib/api/team";
import NodeList from "./NodeList";
import StaticGraph from "./StaticGraph";

// One color per contributing member, in member order (Tableau 10, readable
// on the graph's dark background).
const PALETTE = ["#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2", "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac"];
// Notes still in the team space whose owner has since left the team.
const FORMER_MEMBER = "#94a3b8";

const dot = (color: string) => (
  <span style={{ width: 12, height: 12, flex: "none", borderRadius: 999, background: color }} aria-hidden />
);

/**
 * The team space: notes shared to the org (references to the originals),
 * colored by original owner, with cross-member connections drawn as dashed
 * purple edges. Same static layout as the personal graph. Below it, the
 * team notes as a list, with a file button where the original can be opened.
 */
export default function TeamGraph({ team }: { team: TeamGraphResponse }) {
  const colors = useMemo(
    () => new Map(team.members.map((m, i) => [m.user_id, PALETTE[i % PALETTE.length]])),
    [team.members],
  );

  if (!team.org) {
    return <p>You're not in a team yet. Create one in the Team panel above.</p>;
  }

  const colorOf = (ownerId: string) => colors.get(ownerId) ?? FORMER_MEMBER;
  const names = new Map(team.members.map((m) => [m.user_id, m.is_me ? "you" : m.name]));
  const contributors = new Set(team.nodes.map((n) => n.owner_id));
  const hasFormer = team.nodes.some((n) => !colors.has(n.owner_id));
  const crossMember = team.connections.filter((c) => c.cross_member).length;
  return (
    <div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginBottom: 8, fontSize: 14 }}>
        <strong>{team.org.name}</strong>
        {team.members.map((m) => (
          <span key={m.user_id} style={{ display: "inline-flex", alignItems: "center", gap: 6, opacity: contributors.has(m.user_id) ? 1 : 0.5 }}>
            {dot(colorOf(m.user_id))}
            {m.name}
            {m.is_me ? " (you)" : ""}
          </span>
        ))}
        {hasFormer && (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            {dot(FORMER_MEMBER)}
            former member
          </span>
        )}
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <svg width={28} height={8} aria-hidden>
            <line x1={0} y1={4} x2={28} y2={4} stroke="#c084fc" strokeWidth={3} strokeDasharray="8 4" />
          </svg>
          cross-member ({crossMember})
        </span>
      </div>
      <StaticGraph
        graph={team}
        colorFor={(node) => colorOf(node.owner_id)}
        highlightCrossMember
        emptyText="The team space is empty. Add a note from Slack with /neurix share-team."
      />
      {team.nodes.length > 0 && (
        <details style={{ marginTop: 12 }}>
          <summary>Team notes ({team.nodes.length})</summary>
          <NodeList
            nodes={team.nodes.map((n) => ({
              id: n.id,
              title: n.title,
              status: n.status,
              can_open_file: n.can_open_file,
              detail: names.get(n.owner_id) ?? "former member",
              lead: dot(colorOf(n.owner_id)),
            }))}
          />
        </details>
      )}
    </div>
  );
}
