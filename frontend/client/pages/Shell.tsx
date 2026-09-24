import { useCallback, useEffect, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { signOut } from "../lib/firebase";
import { healthApi } from "../lib/api/health";
import { nodesApi, type GraphResponse, type NodeListItem } from "../lib/api/nodes";
import { teamApi, type TeamGraphResponse } from "../lib/api/team";
import NodeList from "../components/NodeList";
import StaticGraph from "../components/StaticGraph";
import TeamGraph from "../components/TeamGraph";

// While any note is still PROCESSING, re-fetch so its badge clears (and its
// connections appear) without a manual reload.
const POLL_MS = 5000;

const panelStyle = { border: "1px solid #ccc", borderRadius: 8, padding: 16, minHeight: 200, minWidth: 0 };

function Panel({ title, children, style }: { title: string; children: ReactNode; style?: CSSProperties }) {
  return (
    <section style={{ ...panelStyle, ...style }}>
      <h2>{title}</h2>
      {children}
    </section>
  );
}

export default function Shell() {
  const [health, setHealth] = useState<string>("checking...");
  const [nodes, setNodes] = useState<NodeListItem[] | null>(null);
  const [graph, setGraph] = useState<GraphResponse | null>(null);
  const [team, setTeam] = useState<TeamGraphResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    healthApi
      .check()
      .then((res) => setHealth(`ok (uid: ${res.uid})`))
      .catch((err) => setHealth(`error: ${err instanceof Error ? err.message : "unknown"}`));
  }, []);

  const load = useCallback(async () => {
    try {
      const [list, graphData, teamData] = await Promise.all([nodesApi.list(), nodesApi.graph(), teamApi.graph()]);
      setNodes(list.nodes);
      setGraph(graphData);
      setTeam(teamData);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load notes");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const hasProcessing = nodes?.some((n) => n.status === "PROCESSING") ?? false;
  useEffect(() => {
    if (!hasProcessing) return;
    const timer = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(timer);
  }, [hasProcessing, load]);

  return (
    <div style={{ minHeight: "100vh", padding: 16 }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16, flexWrap: "wrap", gap: 8 }}>
        <h1>Neurix Enterprise</h1>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span>Backend health: {health}</span>
          <button onClick={() => load()}>Refresh</button>
          <button onClick={() => signOut()}>Sign out</button>
        </div>
      </header>

      {error && <p style={{ color: "#b91c1c" }}>Error: {error}</p>}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 16 }}>
        <Panel title="Node list">{nodes ? <NodeList nodes={nodes} /> : <p>Loading...</p>}</Panel>
        <Panel title="Graph" style={{ gridColumn: "1 / -1" }}>
          {graph ? <StaticGraph graph={graph} /> : <p>Loading...</p>}
        </Panel>
        <Panel title="Team graph" style={{ gridColumn: "1 / -1" }}>
          {team ? <TeamGraph team={team} /> : <p>Loading...</p>}
        </Panel>
        <Panel title="Ask">
          <p>
            Ask from Slack with <code>/neurix ask &lt;question&gt;</code>. A web Ask panel comes later.
          </p>
        </Panel>
        <Panel title="Insights">
          <p>
            Run <code>/neurix stats</code> in Slack for your and your team's insights and what's new.
          </p>
        </Panel>
      </div>
    </div>
  );
}
