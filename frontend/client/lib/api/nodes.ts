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
}

/** One edge per node pair: strongest score across the keys they share. */
export interface GraphConnection {
  source_id: string;
  target_id: string;
  score: number;
  key_count: number;
}

export interface GraphResponse {
  nodes: GraphNode[];
  connections: GraphConnection[];
}

export const nodesApi = {
  list: () => httpClient.get<{ nodes: NodeListItem[] }>("/nodes"),
  graph: () => httpClient.get<GraphResponse>("/graph"),
};
