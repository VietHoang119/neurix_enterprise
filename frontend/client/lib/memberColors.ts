// One color per team member, in member order (Tableau 10, readable on the
// graph's dark canvas). The team graph, its legend, the member avatars and
// the team-space rows all use this, so a person is one color everywhere.
const PALETTE = [
  "#4e79a7",
  "#f28e2b",
  "#59a14f",
  "#e15759",
  "#76b7b2",
  "#edc948",
  "#b07aa1",
  "#ff9da7",
  "#9c755f",
  "#bab0ac",
];
// Notes still in the team space whose owner has since left the team.
export const FORMER_MEMBER = "#94a3b8";

export function memberColors(members: { user_id: string }[]): (userId: string) => string {
  const colors = new Map(members.map((m, i) => [m.user_id, PALETTE[i % PALETTE.length]]));
  return (userId) => colors.get(userId) ?? FORMER_MEMBER;
}
