"use client";

import { useCallback, useEffect, useState } from "react";
import { ErrorNote } from "@/components/Shell";
import { API_URL, api, money } from "@/lib/api";
import type { AgentKeyCreated, AgentKeyInfo } from "@/lib/types";

/** Claude Desktop config for the stdio MCP server (Windows example paths; the user edits the two paths). */
export function claudeConfig(key: string): string {
  return JSON.stringify(
    {
      mcpServers: {
        leashpay: {
          command: "C:\\Path\\To\\python.exe",
          args: ["C:\\Path\\To\\LeashPay\\mcp_server\\server.py"],
          env: { LEASHPAY_API_URL: API_URL, LEASHPAY_AGENT_KEY: key },
        },
      },
    },
    null,
    2,
  );
}

const utc = (v: string) => new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(v) ? v : v + "Z").toLocaleString();

function CopyButton({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      className="btn btn-sm"
      onClick={async () => {
        await navigator.clipboard.writeText(text);
        setDone(true);
        setTimeout(() => setDone(false), 1500);
      }}
    >
      {done ? "Copied" : label}
    </button>
  );
}

export default function AgentsSection({ allowanceId, online }: { allowanceId: number; online: boolean }) {
  const [keys, setKeys] = useState<AgentKeyInfo[]>([]);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [cap, setCap] = useState("");
  const [created, setCreated] = useState<AgentKeyCreated | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setKeys(await api<AgentKeyInfo[]>(`/allowance/${allowanceId}/agent-keys`));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't load agents");
    }
  }, [allowanceId]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- loads the list from the API into state
    if (online) load();
  }, [online, load]);

  async function create() {
    setBusy(true);
    setError("");
    try {
      const capNum = cap.trim() ? Number(cap) : null;
      if (capNum !== null && !(capNum > 0)) throw new Error("The cap must be a positive amount");
      const made = await api<AgentKeyCreated>(`/allowance/${allowanceId}/agent-keys`, {
        body: { name: name.trim(), per_purchase_cap: capNum },
      });
      setCreated(made);
      setAdding(false);
      setName("");
      setCap("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't create the key");
    } finally {
      setBusy(false);
    }
  }

  async function revoke(k: AgentKeyInfo) {
    setError("");
    try {
      await api(`/agent-keys/${k.id}`, { method: "DELETE" });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't revoke that agent");
    }
  }

  return (
    <section className="card space-y-3">
      <div>
        <p className="label">AI agents on your leash</p>
        <p className="mt-1 text-sm text-muted">
          Let Claude or any MCP agent shop with this allowance. It can buy and ask for refunds, but the Policy Judge checks every cent,
          and it can never approve its own purchases or change your rules.
        </p>
      </div>

      {keys.map((k) => (
        <div key={k.id} className="flex items-center justify-between gap-3 rounded-2xl border border-line bg-white/60 px-4 py-3">
          <div className="min-w-0">
            <p className="truncate font-semibold">
              {k.name} {k.revoked_at && <span className="pill pill-bad ml-1">revoked</span>}
            </p>
            <p className="text-xs text-muted">
              <span className="font-mono">{k.key_prefix}…</span> · {k.per_purchase_cap ? `cap ${money(k.per_purchase_cap)}` : "no extra cap"} ·{" "}
              {k.last_used_at ? `used ${utc(k.last_used_at)}` : "never used"}
            </p>
          </div>
          {!k.revoked_at && (
            <button className="btn btn-danger btn-sm" disabled={!online} onClick={() => revoke(k)}>
              Revoke
            </button>
          )}
        </div>
      ))}

      <ErrorNote>{error}</ErrorNote>

      {created && (
        <div className="space-y-3 rounded-2xl border border-warn/40 bg-warn/10 p-4">
          <p className="text-sm font-semibold text-warn">Copy this key now. It is shown only once.</p>
          <div className="flex items-center gap-2">
            <code className="min-w-0 flex-1 break-all rounded-xl bg-white px-3 py-2 font-mono text-xs">{created.key}</code>
            <CopyButton text={created.key} label="Copy key" />
          </div>
          <div>
            <div className="mb-1 flex items-center justify-between">
              <p className="label">Claude Desktop config</p>
              <CopyButton text={claudeConfig(created.key)} label="Copy config" />
            </div>
            <pre className="max-h-56 overflow-auto rounded-xl bg-white p-3 text-[11px] leading-snug">{claudeConfig(created.key)}</pre>
            <p className="mt-1 text-xs text-muted">
              Paste into <span className="font-mono">%APPDATA%\Claude\claude_desktop_config.json</span> and fix the two paths. Steps in docs/MCP.md.
            </p>
          </div>
          <button className="btn btn-primary btn-sm w-full" onClick={() => setCreated(null)}>
            I&apos;ve saved it
          </button>
        </div>
      )}

      {adding ? (
        <div className="space-y-2">
          <input className="field" placeholder="Agent name, e.g. Claude Desktop" value={name} onChange={(e) => setName(e.target.value)} maxLength={60} />
          <input className="field" inputMode="decimal" placeholder="Per-purchase cap in USD (optional)" value={cap} onChange={(e) => setCap(e.target.value)} />
          <div className="flex gap-2">
            <button className="btn btn-sm flex-1" onClick={() => setAdding(false)}>
              Cancel
            </button>
            <button className="btn btn-primary btn-sm flex-1" disabled={busy || !name.trim()} onClick={create}>
              Create key
            </button>
          </div>
        </div>
      ) : (
        !created && (
          <button className="btn btn-sm w-full" disabled={!online} onClick={() => setAdding(true)}>
            Connect an agent
          </button>
        )
      )}
    </section>
  );
}
