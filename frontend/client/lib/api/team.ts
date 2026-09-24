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

export const teamApi = {
  graph: () => httpClient.get<TeamGraphResponse>("/team/graph"),
};
