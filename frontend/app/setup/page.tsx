"use client";

import { useState } from "react";
import { ErrorNote, Shell } from "@/components/Shell";
import { api, money } from "@/lib/api";
import type { ParsedPolicy } from "@/lib/types";

const EXAMPLE =
  "Food and rides only, max $20 per purchase and $60 a day. Ask me before anything over $10. No alcohol, no gadgets. Offline taps up to $5.";

export default function Setup() {
  const [text, setText] = useState("");
  const [parsed, setParsed] = useState<ParsedPolicy | null>(null);
  const [amount, setAmount] = useState("50");
  const [busy, setBusy] = useState<"parse" | "create" | null>(null);
  const [error, setError] = useState("");

  async function understand() {
    setBusy("parse");
    setError("");
    try {
      setParsed(await api<ParsedPolicy>("/policy/parse", { body: { text } }));
    } catch (e) {
      setParsed(null);
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setBusy(null);
    }
  }

  async function create() {
    if (!parsed) return;
    setBusy("create");
    setError("");
    try {
      const { id } = await api<{ id: number }>("/policy", { body: { raw_text: text, policy: parsed.policy } });
      const res = await api<{ allowance_id: number; approve_url: string }>("/allowance", {
        body: { policy_id: id, amount: Number(amount) },
      });
      window.location.href = res.approve_url; // PayPal, then back to /setup/return
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
      setBusy(null);
    }
  }

  const amountOk = Number(amount) > 0;

  return (
    <Shell title="Setup">
      <div>
        <h1 className="text-3xl font-extrabold tracking-tight">Set the rules.</h1>
        <p className="mt-1 text-muted">Say it like you&apos;d say it to a friend.</p>
      </div>

      <textarea
        className="field min-h-36 resize-none leading-relaxed"
        placeholder={EXAMPLE}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setParsed(null);
        }}
      />
      <button className="btn btn-primary" disabled={!text.trim() || busy !== null} onClick={understand}>
        {busy === "parse" ? "Reading your rules…" : "Understand my rules"}
      </button>

      <ErrorNote>{error}</ErrorNote>

      {parsed && (
        <>
          <section className="card">
            <p className="label mb-3">Here&apos;s what I understood</p>
            <ul className="space-y-2">
              {parsed.summary.map((line) => (
                <li key={line} className="flex gap-3">
                  <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
                  <span>{line}</span>
                </li>
              ))}
            </ul>
            {parsed.ambiguities.length > 0 && (
              <div className="mt-4 rounded-2xl border border-warn/40 bg-warn/10 p-4 text-sm">
                <p className="mb-1 font-bold text-warn">I assumed…</p>
                <ul className="list-disc space-y-1 pl-5 text-text/90">
                  {parsed.ambiguities.map((a) => (
                    <li key={a}>{a}</li>
                  ))}
                </ul>
              </div>
            )}
          </section>

          <section className="card space-y-3">
            <label className="label" htmlFor="amount">
              How much to put on hold (USD)
            </label>
            <div className="flex items-center gap-2">
              <span className="num text-4xl text-muted">$</span>
              <input
                id="amount"
                className="field num !text-3xl"
                inputMode="decimal"
                value={amount}
                onChange={(e) => setAmount(e.target.value.replace(/[^\d.]/g, ""))}
              />
            </div>
            <p className="text-sm text-muted">PayPal holds this; nothing is taken until a purchase passes your rules.</p>
            <button className="btn btn-primary w-full" disabled={!amountOk || busy !== null} onClick={create}>
              {busy === "create" ? "Opening PayPal…" : `Create allowance${amountOk ? ` · ${money(Number(amount))}` : ""}`}
            </button>
          </section>
        </>
      )}
    </Shell>
  );
}
