import { useEffect, useState } from "react";
import { signOut } from "../lib/firebase";
import { healthApi } from "../lib/api/health";

/**
 * Week 1 deliverable: four empty, routed panels — Node list / Graph / Ask /
 * Insights. No product functionality yet; later weeks fill each panel in.
 * The health check card proves the Firebase -> Cloud Run token chain works.
 */
export default function Shell() {
  const [health, setHealth] = useState<string>("checking...");

  useEffect(() => {
    healthApi
      .check()
      .then((res) => setHealth(`ok (uid: ${res.uid})`))
      .catch((err) => setHealth(`error: ${err instanceof Error ? err.message : "unknown"}`));
  }, []);

  const panels = [
    { title: "Node list", description: "Coming soon." },
    { title: "Graph", description: "Coming soon." },
    { title: "Ask", description: "Coming soon." },
    { title: "Insights", description: "Coming soon." },
  ];

  return (
    <div style={{ minHeight: "100vh", padding: 16 }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <h1>Neurix Enterprise</h1>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span>Backend health: {health}</span>
          <button onClick={() => signOut()}>Sign out</button>
        </div>
      </header>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 16 }}>
        {panels.map((panel) => (
          <section key={panel.title} style={{ border: "1px solid #ccc", borderRadius: 8, padding: 16, minHeight: 200 }}>
            <h2>{panel.title}</h2>
            <p>{panel.description}</p>
          </section>
        ))}
      </div>
    </div>
  );
}
