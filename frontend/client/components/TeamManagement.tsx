import { useState } from "react";
import type { FormEvent } from "react";
import { X } from "lucide-react";
import { teamApi, type TeamView } from "../lib/api/team";
import { initials } from "../lib/format";
import { memberColors } from "../lib/memberColors";
import { cn } from "../lib/utils";
import { Avatar, AvatarFallback } from "./ui/avatar";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "./ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/tooltip";

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong. Please try again.");

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
    <form onSubmit={submit} className="flex flex-col gap-3">
      <p className="text-sm text-muted-foreground">You're not in a team yet. Create one — you'll be its admin.</p>
      <label htmlFor="team-name" className="text-xs text-muted-foreground">
        Team name
      </label>
      <div className="flex gap-2">
        <Input id="team-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={100} required />
        <Button type="submit" disabled={busy || !name.trim()}>
          {busy ? "Creating…" : "Create"}
        </Button>
      </div>
      {error && <p className="text-sm text-status-failed">{error}</p>}
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
    <form onSubmit={submit} className="mt-4 flex flex-col gap-2 border-t border-border pt-4">
      <label htmlFor="invite-email" className="text-xs text-muted-foreground">
        Add a member — the email they signed in to Neurix with
      </label>
      <div className="flex gap-2">
        <Input
          id="invite-email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="name@company.com"
          required
          className="min-w-0"
        />
        <Button type="submit" disabled={busy || !email.trim()}>
          {busy ? "Adding…" : "Add"}
        </Button>
      </div>
      {message && (
        <p className={message.ok ? "text-sm text-emerald-400" : "text-sm text-status-failed"}>{message.text}</p>
      )}
    </form>
  );
}

/**
 * The Team panel. No team: a create form. Member: a read-only member list.
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
  // Same member order as the team graph, so an avatar matches its graph color.
  const colorOf = memberColors(team.members);

  const remove = async (userId: string, name: string) => {
    if (!window.confirm(`Remove ${name} from ${team.org?.name}? Notes they already shared stay in the team space.`))
      return;
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
    <div className="flex min-h-0 flex-col">
      <div className="mb-3">
        <h2 className="truncate text-base font-semibold" title={team.org.name}>
          {team.org.name}
        </h2>
        <p className="text-xs text-subtle-foreground">
          {team.members.length} member{team.members.length === 1 ? "" : "s"} · you're{" "}
          {isAdmin ? "an admin" : "a member"}
          {!isAdmin && " (only an admin can change the team)"}
        </p>
      </div>
      <Table className="table-fixed">
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="h-8 px-0 text-xs font-normal text-subtle-foreground">Member</TableHead>
            <TableHead className="h-8 w-[66px] px-1 text-xs font-normal text-subtle-foreground">Role</TableHead>
            <TableHead
              className="h-8 w-[38px] px-1 text-right text-xs font-normal text-subtle-foreground"
              title="Notes they shared to the team"
            >
              Notes
            </TableHead>
            {isAdmin && (
              <TableHead className="h-8 w-7 px-0">
                <span className="sr-only">Actions</span>
              </TableHead>
            )}
          </TableRow>
        </TableHeader>
        <TableBody>
          {team.members.map((m) => {
            const lastAdmin = m.role === "admin" && adminCount === 1;
            return (
              <TableRow key={m.user_id} className="hover:bg-white/[0.03]">
                <TableCell className="px-0 py-2.5">
                  <div className="flex min-w-0 items-center gap-2">
                    <Avatar className="h-7 w-7 flex-none">
                      <AvatarFallback
                        className="text-[11px] font-semibold text-white"
                        style={{ background: colorOf(m.user_id) }}
                      >
                        {initials(m.name)}
                      </AvatarFallback>
                    </Avatar>
                    <div className="min-w-0">
                      <p className="truncate text-sm" title={m.name}>
                        {m.name}
                        {m.is_me && <span className="text-subtle-foreground"> (you)</span>}
                      </p>
                      {m.email && (
                        <p className="truncate text-xs text-subtle-foreground" title={m.email}>
                          {m.email}
                        </p>
                      )}
                    </div>
                  </div>
                </TableCell>
                <TableCell className="px-1 py-2.5">
                  <Badge
                    variant="secondary"
                    className={cn(
                      "px-2 font-medium",
                      m.role === "admin"
                        ? "bg-primary/20 text-violet-200 hover:bg-primary/20"
                        : "text-muted-foreground hover:bg-secondary",
                    )}
                  >
                    {m.role === "admin" ? "Admin" : "Member"}
                  </Badge>
                </TableCell>
                <TableCell className="px-1 py-2.5 text-right tabular-nums text-muted-foreground">
                  {m.shared_notes}
                </TableCell>
                {isAdmin && (
                  <TableCell className="px-0 py-2.5 text-right">
                    {!lastAdmin && (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7 text-subtle-foreground hover:text-foreground"
                            onClick={() => remove(m.user_id, m.name)}
                            disabled={removing !== null}
                            aria-label={`Remove ${m.name} from the team`}
                          >
                            <X />
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent className="text-xs">
                          {removing === m.user_id ? "Removing…" : "Remove from team"}
                        </TooltipContent>
                      </Tooltip>
                    )}
                  </TableCell>
                )}
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      {error && <p className="mt-2 text-sm text-status-failed">{error}</p>}
      {isAdmin && <InviteMember onChange={onChange} />}
    </div>
  );
}
