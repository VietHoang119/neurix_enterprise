import { httpClient } from "./client";

export type NodeStatus = "DRAFT" | "PROCESSING" | "READY" | "FAILED";

export interface NodeListItem {
  id: string;
  title: string;
  status: NodeStatus;
  source: string | null;
  origin: string | null;
  created_at: string | null;
  /** The original file can be opened (owner, or a share with view_raw). */
  can_open_file: boolean;
}

/** A note another person shared with you (accepted). */
export interface SharedNodeItem {
  id: string;
  title: string;
  status: NodeStatus;
  owner_email: string | null;
  can_open_file: boolean;
}

export interface NodeListResponse {
  nodes: NodeListItem[];
  shared_with_me: SharedNodeItem[];
}

export interface FileLink {
  url: string;
  expires_in: number;
  title: string;
}

export interface GraphNode {
  id: string;
  title: string;
  status: NodeStatus;
  /** user_id of the note's original owner (shared notes are references, not copies). */
  owner_id: string;
  /** Owned by someone else: reached through an accepted or team share. */
  shared: boolean;
  /** Team graph only: the original file can be opened. */
  can_open_file?: boolean;
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
  list: () => httpClient.get<NodeListResponse>("/nodes"),
  /** Signed, short-lived download URL for a note's original file. */
  fileLink: (nodeId: string) => httpClient.get<FileLink>(`/nodes/${encodeURIComponent(nodeId)}/file`),
  graph: () => httpClient.get<GraphResponse>("/graph"),
};
