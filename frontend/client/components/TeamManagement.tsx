import { useState } from "react";
import type { FormEvent } from "react";
import { teamApi, type TeamView } from "../lib/api/team";

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong. Please try again.");

const cell = { padding: "6px 8px", borderBottom: "1px solid #eee", textAlign: "left" as const };

function CreateTeam({ onChange }: { onChange: (view: TeamView) => void }) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onChange(await teamApi.create(name));
      setName("");
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 8, maxWidth: 360 }}>
      <p style={{ margin: 0 }}>You're not in a team yet. Create one — you'll be its admin.</p>
      <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        Team name
        <input value={name} onChange={(e) => setName(e.target.value)} maxLength={100} required />
      </label>
      <button type="submit" disabled={busy || !name.trim()} style={{ alignSelf: "flex-start" }}>
        {busy ? "Creating..." : "Create team"}
      </button>
      {error && <p style={{ color: "#b91c1c", margin: 0 }}>{error}</p>}
    </form>
  );
}

function InviteMember({ onChange }: { onChange: (view: TeamView) => void }) {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      onChange(await teamApi.invite(email));
      setMessage({ ok: true, text: `Added ${email.trim()}.` });
      setEmail("");
    } catch (err) {
      setMessage({ ok: false, text: errorText(err) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 16 }}>
      <label htmlFor="invite-email">
        <strong>Add a member</strong> — the email they signed in to Neurix with
      </label>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <input
          id="invite-email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="name@company.com"
          required
          style={{ flex: "1 1 220px", minWidth: 0 }}
        />
        <button type="submit" disabled={busy || !email.trim()}>
          {busy ? "Adding..." : "Add"}
        </button>
      </div>
      {message && <p style={{ color: message.ok ? "#15803d" : "#b91c1c", margin: 0 }}>{message.text}</p>}
    </form>
  );
}

/**
 * The Team page. No team: a create form. Member: a read-only member list.
 * Admin: the same list plus add-by-email and remove. The backend enforces
 * every rule (admin only, one team per person, never zero admins); this
 * only decides what to show.
 */
export default function TeamManagement({ team, onChange }: { team: TeamView; onChange: (view: TeamView) => void }) {
  const [removing, setRemoving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!team.org) {
    return <CreateTeam onChange={onChange} />;
  }

  const isAdmin = team.org.role === "admin";
  const adminCount = team.members.filter((m) => m.role === "admin").length;

  const remove = async (userId: string, name: string) => {
    if (!window.confirm(`Remove ${name} from ${team.org?.name}? Notes they already shared stay in the team space.`)) return;
    setRemoving(userId);
    setError(null);
    try {
      onChange(await teamApi.remove(userId));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setRemoving(null);
    }
  };

  return (
    <div>
      <p style={{ marginTop: 0 }}>
        <strong>{team.org.name}</strong> · {team.members.length} member{team.members.length === 1 ? "" : "s"} · you're{" "}
        {isAdmin ? "an admin" : "a member"}
        {!isAdmin && " (only an admin can change the team)"}
      </p>
      <div style={{ overflowX: "auto" }}>
        <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 14 }}>
          <thead>
            <tr>
              <th style={cell}>Name</th>
              <th style={cell}>Email</th>
              <th style={cell}>Role</th>
              <th style={cell}>Shared notes</th>
              {isAdmin && <th style={cell} aria-label="Actions" />}
            </tr>
          </thead>
          <tbody>
            {team.members.map((m) => {
              const lastAdmin = m.role === "admin" && adminCount === 1;
              return (
                <tr key={m.user_id}>
                  <td style={cell}>
                    {m.name}
                    {m.is_me ? " (you)" : ""}
                  </td>
                  <td style={cell}>{m.email ?? "—"}</td>
                  <td style={cell}>{m.role === "admin" ? "Admin" : "Member"}</td>
                  <td style={cell}>{m.shared_notes}</td>
                  {isAdmin && (
                    <td style={{ ...cell, textAlign: "right" }}>
                      {!lastAdmin && (
                        <button type="button" onClick={() => remove(m.user_id, m.name)} disabled={removing !== null}>
                          {removing === m.user_id ? "Removing..." : "Remove"}
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {error && <p style={{ color: "#b91c1c" }}>{error}</p>}
      {isAdmin && <InviteMember onChange={onChange} />}
    </div>
  );
}
