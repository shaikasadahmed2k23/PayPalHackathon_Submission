"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { ErrorNote, Shell } from "@/components/Shell";
import { api, money } from "@/lib/api";
import { useOnline } from "@/lib/hooks";
import { pillClass, statusLabel } from "@/lib/status";
import { currentWallet } from "@/lib/store";
import type { AgentReply, PurchaseOut } from "@/lib/types";

type Msg = { role: "user"; text: string } | { role: "agent"; reply: AgentReply };

const CHIPS = [
  "Snacks for movie night under $8",
  "Grab a 6-pack of beer",
  "Book me a ride home",
  "Return the popcorn",
  "How much have I spent?",
];

/** The leash moment: what the deterministic Policy Judge decided, in one line. */
function JudgeLine({ p }: { p: PurchaseOut }) {
  const blocked = p.status === "BLOCKED";
  const pending = p.status === "PENDING_APPROVAL";
  const tone = blocked ? "border-bad text-bad" : pending ? "border-warn text-warn" : "border-ok text-ok";
  const [first, ...rest] = p.reasons;
  const icon = blocked ? "✕" : pending ? "⚠" : "✓";
  const headline = blocked || pending ? `${icon} ${first ?? (blocked ? "A rule failed" : "Above your approval limit")}` : `${icon} All rules passed`;
  const more = blocked || pending ? rest : [];
  return (
    <div className={`mt-3 rounded-xl border-l-4 bg-white/70 px-3 py-2 ${tone}`}>
      <span className="label !text-[10px]">Policy Judge</span>
      <p className="text-sm font-bold">{headline}</p>
      {more.map((r) => (
        <p key={r} className="text-xs font-normal opacity-90">
          {r}
        </p>
      ))}
    </div>
  );
}

function PurchaseCard({
  p,
  busy,
  onDecide,
}: {
  p: PurchaseOut;
  busy: boolean;
  onDecide: (p: PurchaseOut, a: "approve" | "deny") => void;
}) {
  const border = p.status === "BLOCKED" ? "border-bad/40" : p.status === "PENDING_APPROVAL" ? "border-warn/40" : "";
  return (
    <div className={`card !p-4 ${border}`}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-bold">{p.merchant}</p>
          <p className="text-sm text-muted">{p.items.map((i) => (i.qty > 1 ? `${i.qty}× ${i.name}` : i.name)).join(", ")}</p>
        </div>
        <div className="text-right">
          <p className="num text-2xl">{money(p.amount)}</p>
          <span className={pillClass(p.status)}>{statusLabel(p.status)}</span>
        </div>
      </div>
      <JudgeLine p={p} />
      {p.status === "PENDING_APPROVAL" && p.approval_id !== null && (
        <div className="mt-3 flex gap-2">
          <button className="btn btn-primary btn-sm flex-1" disabled={busy} onClick={() => onDecide(p, "approve")}>
            Approve
          </button>
          <button className="btn btn-sm flex-1" disabled={busy} onClick={() => onDecide(p, "deny")}>
            Deny
          </button>
        </div>
      )}
    </div>
  );
}

export default function AgentPage() {
  const online = useOnline();
  const [allowanceId, setAllowanceId] = useState<number | null | undefined>(undefined);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [live, setLive] = useState<Record<number, PurchaseOut>>({});
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const convo = useRef<string | undefined>(undefined);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    currentWallet()
      .then((w) => setAllowanceId(w ? w.allowance_id : null))
      .catch(() => setAllowanceId(null));
  }, []);

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth" });
  }, [msgs, sending]);

  const send = useCallback(
    async (text: string) => {
      if (!text.trim() || !allowanceId || sending) return;
      setMsgs((m) => [...m, { role: "user", text }]);
      setInput("");
      setSending(true);
      setError("");
      try {
        const reply = await api<AgentReply>("/agent/chat", {
          body: { allowance_id: allowanceId, message: text, conversation_id: convo.current },
        });
        convo.current = reply.conversation_id;
        setMsgs((m) => [...m, { role: "agent", reply }]);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Something went wrong");
      } finally {
        setSending(false);
      }
    },
    [allowanceId, sending],
  );

  async function decide(p: PurchaseOut, action: "approve" | "deny") {
    if (p.approval_id === null || !allowanceId) return;
    setBusyId(p.id);
    setError("");
    try {
      await api(`/approvals/${p.approval_id}/${action}`, { method: "POST", body: {}, owner: allowanceId });
      const fresh = await api<PurchaseOut[]>(`/purchases?allowance_id=${allowanceId}`);
      setLive((l) => ({ ...l, ...Object.fromEntries(fresh.map((x) => [x.id, x])) }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't update that approval");
    } finally {
      setBusyId(null);
    }
  }

  if (allowanceId === undefined)
    return (
      <Shell title="Agent">
        <div className="card text-muted">Loading…</div>
      </Shell>
    );
  if (allowanceId === null)
    return (
      <Shell title="Agent">
        <div className="card space-y-3">
          <p>The agent spends from your allowance. Set one up first.</p>
          <Link href="/setup" className="btn btn-primary">
            Set up an allowance
          </Link>
        </div>
      </Shell>
    );

  const chips = (
    <>
      {CHIPS.map((c) => (
        <button
          key={c}
          className="chip shrink-0 border border-line !px-3 !py-2 !text-sm hover:text-text"
          disabled={!online || sending}
          onClick={() => send(c)}
        >
          {c}
        </button>
      ))}
    </>
  );

  return (
    <Shell title="Agent">
      <div>
        <h1 className="text-3xl font-extrabold tracking-tight">Ask the agent.</h1>
        <p className="mt-1 text-sm text-muted">It shops the demo store. The Policy Judge approves every cent.</p>
      </div>

      <div className="flex flex-col gap-4">
        {msgs.length === 0 && <div className="flex flex-wrap gap-2">{chips}</div>}

        {msgs.map((m, i) =>
          m.role === "user" ? (
            <div key={i} className="ml-10 self-end rounded-3xl rounded-br-md bg-accent px-4 py-3 font-medium text-white">
              {m.text}
            </div>
          ) : (
            <div key={i} className="mr-6 space-y-3">
              <div className="rounded-3xl rounded-bl-md border border-line bg-panel px-4 py-3">{m.reply.reply}</div>
              {m.reply.purchases.map((p) => (
                <PurchaseCard key={p.id} p={live[p.id] ?? p} busy={busyId === p.id} onDecide={decide} />
              ))}
              {m.reply.refunds.map((r) => (
                <div key={r.refund_id} className="card !p-4 border-info/30">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="font-bold">↩ Refund</p>
                      <p className="text-sm text-muted">{r.items.join(", ")}</p>
                    </div>
                    <div className="text-right">
                      <p className="num text-2xl text-info">+{money(r.amount)}</p>
                      <span className={pillClass("REFUNDED")}>refunded</span>
                    </div>
                  </div>
                </div>
              ))}
              {m.reply.skipped
                .filter((s) => !m.reply.purchases.some((p) => s.item.startsWith(`${p.merchant} (`)))
                .map((s) => (
                  <div key={s.item} className="rounded-2xl border border-line bg-panel2 px-4 py-3 text-sm">
                    <span className="font-semibold">Left out: {s.item}</span>
                    <p className="text-muted">{s.reasons.join("; ")}</p>
                  </div>
                ))}
            </div>
          ),
        )}
        {sending && <div className="mr-6 w-fit rounded-3xl border border-line bg-panel px-4 py-3 text-muted">Thinking…</div>}
        <div ref={end} />
      </div>

      <ErrorNote>{error}</ErrorNote>

      {msgs.length > 0 && <div className="flex gap-2 overflow-x-auto pb-1">{chips}</div>}

      <form
        className="sticky bottom-4 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
      >
        <input
          className="field"
          placeholder={online ? "Ask for something…" : "Offline: the agent needs internet"}
          disabled={!online || sending}
          value={input}
          onChange={(e) => setInput(e.target.value)}
        />
        <button className="btn btn-primary" disabled={!online || sending || !input.trim()}>
          Send
        </button>
      </form>
    </Shell>
  );
}
