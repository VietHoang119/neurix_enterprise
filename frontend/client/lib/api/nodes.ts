import { httpClient } from "./client";

export type NodeStatus = "DRAFT" | "PROCESSING" | "READY" | "FAILED";

export interface NodeListItem {
  id: string;
  title: string;
  status: NodeStatus;
  source: string | null;
  origin: string | null;
  created_at: string | null;
}

export interface GraphNode {
  id: string;
  title: string;
  status: NodeStatus;
  /** user_id of the note's original owner (shared notes are references, not copies). */
  owner_id: string;
  /** Owned by someone else: reached through an accepted or team share. */
  shared: boolean;
}

/** One edge per node pair: strongest score across the keys they share. */
export interface GraphConnection {
  source_id: string;
  target_id: string;
  score: number;
  key_count: number;
  /** Formed by a share (a persisted share connection). */
  is_share: boolean;
  /** Links notes of two different owners. */
  cross_member: boolean;
}

export interface GraphResponse {
  nodes: GraphNode[];
  connections: GraphConnection[];
}

export const nodesApi = {
  list: () => httpClient.get<{ nodes: NodeListItem[] }>("/nodes"),
  graph: () => httpClient.get<GraphResponse>("/graph"),
};
