import { httpClient } from "./client";
import type { GraphResponse } from "./nodes";

export interface TeamMember {
  user_id: string;
  name: string;
  is_me: boolean;
}

export interface TeamGraphResponse extends GraphResponse {
  org: { id: string; name: string } | null;
  members: TeamMember[];
}

export type TeamRole = "admin" | "member";

export interface TeamMemberRow {
  user_id: string;
  name: string;
  email: string | null;
  role: TeamRole;
  /** Their notes in the team space. */
  shared_notes: number;
  is_me: boolean;
}

export interface TeamView {
  org: {
    id: string;
    name: string;
    role: TeamRole;
    /** The team's own Slack workspace; null until it is linked (see backend 0005). */
    slack_workspace_url: string | null;
  } | null;
  members: TeamMemberRow[];
}

export const teamApi = {
  graph: () => httpClient.get<TeamGraphResponse>("/team/graph"),
  get: () => httpClient.get<TeamView>("/team"),
  create: (name: string) => httpClient.post<TeamView>("/team", { name }),
  invite: (email: string) => httpClient.post<TeamView>("/team/members", { email }),
  remove: (userId: string) => httpClient.delete<TeamView>(`/team/members/${encodeURIComponent(userId)}`),
};
