/* eslint-disable react-hooks/set-state-in-effect -- these effects load data from IndexedDB / the API into state */
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ErrorNote, Shell } from "@/components/Shell";
import Scanner from "@/components/Scanner";
import { api, money } from "@/lib/api";
import { pillClass, statusLabel } from "@/lib/status";
import { utcStamp } from "@/lib/canonical";
import { useOnline } from "@/lib/hooks";
import { enqueue, getCache, queued, setCache } from "@/lib/store";
import { syncQueue, type SyncResult } from "@/lib/sync";
import type { Merchant, QrPayload, SettlementReport } from "@/lib/types";
import { parseAmount, parseQr, verifyOffline } from "@/lib/verify";

type Mode = "idle" | "scanning" | "amount" | "result";
interface Result { ok: boolean; title: string; reasons: string[] }
interface Card { merchant: string; report?: SettlementReport; error?: string }

const RISK_TONE = { low: "pill pill-ok", medium: "pill pill-warn", high: "pill pill-bad", unknown: "pill pill-neutral" } as const;

export default function MerchantPage() {
  const online = useOnline();
  const [merchants, setMerchants] = useState<Merchant[]>([]);
  const [merchantId, setMerchantId] = useState<number | null>(null);
  const [serverPubkey, setServerPubkey] = useState("");
  const [mode, setMode] = useState<Mode>("idle");
  const [qr, setQr] = useState<QrPayload | null>(null);
  const [amount, setAmount] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  const [count, setCount] = useState(0);
  const [cards, setCards] = useState<Card[]>([]);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState("");
  const [dev, setDev] = useState(false);
  const [pasted, setPasted] = useState("");
  const names = useRef<Record<number, string>>({});

  const refreshCount = useCallback(async () => setCount((await queued()).length), []);

  // Cached data first (works offline), then refresh the cache when online.
  useEffect(() => {
    setDev(new URLSearchParams(window.location.search).get("dev") === "1");
    (async () => {
      const [m, k, id] = await Promise.all([
        getCache<Merchant[]>("merchants"), getCache<string>("server_pubkey"), getCache<number>("merchant_id"),
      ]);
      if (m) setMerchants(m);
      if (k) setServerPubkey(k);
      if (id) setMerchantId(id);
      refreshCount();
    })();
  }, [refreshCount]);

  useEffect(() => {
    if (!online) return;
    (async () => {
      try {
        const [m, k] = await Promise.all([
          api<Merchant[]>("/merchants"), api<{ pubkey_b64: string }>("/crypto/server-pubkey"),
        ]);
        setMerchants(m);
        setServerPubkey(k.pubkey_b64);
        await Promise.all([setCache("merchants", m), setCache("server_pubkey", k.pubkey_b64)]);
        if (merchantId === null && m.length) setMerchantId(m[0].id);
      } catch {
        /* keep the cached copies */
      }
    })();
  }, [online]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    names.current = Object.fromEntries(merchants.map((m) => [m.id, m.name]));
  }, [merchants]);

  const sync = useCallback(async () => {
    setSyncing(true);
    setError("");
    try {
      const results: SyncResult[] = await syncQueue();
      const fresh: Card[] = results.map((r) => ({
        merchant: names.current[r.merchant_id] ?? `Merchant ${r.merchant_id}`, report: r.report, error: r.error,
      }));
      if (fresh.length) setCards((c) => [...fresh, ...c]);
    } finally {
      setSyncing(false);
      refreshCount();
    }
  }, [refreshCount]);

  // Coming back online with payments waiting -> sync automatically.
  useEffect(() => {
    if (!online) return;
    (async () => {
      if ((await queued()).length > 0) sync();
    })();
  }, [online, sync]);

  const merchant = merchants.find((m) => m.id === merchantId) ?? null;

  function onScanned(text: string) {
    try {
      setQr(parseQr(text));
      setAmount("");
      setError("");
      setMode("amount");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't read that code");
      setMode("idle");
    }
  }

  async function charge() {
    if (!qr || !merchant) return;
    if (!serverPubkey) {
      setResult({ ok: false, title: "Can't verify yet", reasons: ["Open this page once while online so the server key is saved."] });
      setMode("result");
      return;
    }
    const inQueue = (await queued()).filter((p) => p.voucher?.allowance_id === qr.voucher?.allowance_id);
    const reasons = await verifyOffline({
      qr, amount, merchant, serverPubkey, queuedSerials: new Set(inQueue.map((p) => p.voucher.serial)),
    });
    const cents = parseAmount(amount);
    if (reasons.length === 0 && "cents" in cents) {
      const value = cents.cents / 100;
      await enqueue({ ...qr, amount: value, merchant_id: merchant.id, scanned_at: utcStamp() });
      setResult({ ok: true, title: `Paid ${money(value)} — will settle when online`, reasons: [] });
      refreshCount();
    } else {
      setResult({ ok: false, title: "Not accepted", reasons });
    }
    setMode("result");
  }

  function reset() {
    setQr(null);
    setAmount("");
    setResult(null);
    setPasted("");
    setMode("idle");
  }

  return (
    <Shell title="Seller">
      <div className="flex items-end justify-between">
        <h1 className="text-3xl font-extrabold tracking-tight">Take a payment.</h1>
      </div>

      <section className="card space-y-2">
        <label className="label" htmlFor="m">I&apos;m selling as</label>
        <select
          id="m"
          className="field"
          value={merchantId ?? ""}
          onChange={(e) => {
            const id = Number(e.target.value);
            setMerchantId(id);
            setCache("merchant_id", id);
          }}
        >
          {merchants.length === 0 && <option value="">Open once while online to load merchants</option>}
          {merchants.map((m) => (
            <option key={m.id} value={m.id}>{m.name} · {m.category}</option>
          ))}
        </select>
      </section>

      {mode === "idle" && (
        <>
          <button className="btn btn-primary !min-h-20 !text-xl" disabled={!merchant} onClick={() => { setError(""); setMode("scanning"); }}>
            Scan buyer&apos;s code
          </button>
          {dev && (
            <section className="card space-y-3">
              <p className="label">Dev: paste the QR JSON</p>
              <textarea className="field min-h-28 font-mono text-xs" value={pasted} onChange={(e) => setPasted(e.target.value)} placeholder='{"certificate":…}' />
              <button className="btn w-full" disabled={!pasted.trim() || !merchant} onClick={() => onScanned(pasted)}>Use pasted code</button>
            </section>
          )}
        </>
      )}

      {mode === "scanning" && <Scanner onScan={onScanned} onCancel={reset} />}

      {mode === "amount" && qr && (
        <section className="card space-y-3">
          <p className="label">Code #{qr.voucher.serial + 1} · limit {money(Number(qr.certificate.per_tap_max))} per tap</p>
          <div className="flex items-center gap-2">
            <span className="num text-4xl text-muted">$</span>
            <input
              className="field num !text-4xl"
              inputMode="decimal"
              autoFocus
              placeholder="0.00"
              value={amount}
              onChange={(e) => setAmount(e.target.value.replace(/[^\d.]/g, ""))}
            />
          </div>
          <button className="btn btn-primary w-full" disabled={!amount} onClick={charge}>Charge</button>
          <button className="btn w-full" onClick={reset}>Cancel</button>
        </section>
      )}

      {mode === "result" && result && (
        <section
          className={`card flex flex-col items-center gap-3 py-8 text-center ${result.ok ? "border-ok/50 bg-ok/10" : "border-bad/50 bg-bad/10"}`}
        >
          <span className={`text-7xl font-black leading-none ${result.ok ? "text-ok" : "text-bad"}`}>{result.ok ? "✓" : "✗"}</span>
          <p className="text-2xl font-extrabold">{result.title}</p>
          {result.reasons.map((r) => (
            <p key={r} className="text-bad">{r}</p>
          ))}
          <button className="btn btn-primary mt-2" onClick={reset}>Next payment</button>
        </section>
      )}

      <ErrorNote>{error}</ErrorNote>

      <section className="card flex items-center justify-between">
        <div>
          <p className="num text-4xl">{count}</p>
          <p className="text-sm text-muted">{count === 1 ? "payment waiting to sync" : "payments waiting to sync"}</p>
        </div>
        <button className="btn btn-sm" disabled={!online || syncing || count === 0} onClick={sync}>
          {syncing ? "Syncing…" : "Sync now"}
        </button>
      </section>

      {cards.map((c, i) => (
        <section key={i} className="card space-y-3">
          <div className="flex items-center justify-between">
            <p className="font-bold">{c.merchant}</p>
            <button className="text-sm text-muted" onClick={() => setCards((x) => x.filter((_, j) => j !== i))}>Dismiss</button>
          </div>
          {c.error && <ErrorNote>{c.error} — payments stay queued.</ErrorNote>}
          {c.report && (
            <>
              <div className="flex gap-6">
                <div><p className="num text-3xl text-ok">{c.report.settled}</p><p className="label">settled</p></div>
                {c.report.already_settled > 0 && <div><p className="num text-3xl text-ok">{c.report.already_settled}</p><p className="label">already settled</p></div>}
                <div><p className="num text-3xl text-bad">{c.report.rejected}</p><p className="label">rejected</p></div>
                <div className="ml-auto text-right"><p className="num text-3xl">{money(c.report.total_settled_amount)}</p><p className="label">paid out</p></div>
              </div>
              <p className="text-sm text-muted">{c.report.summary}</p>
              {c.report.items.map((it, k) => (
                <div key={k} className="rounded-2xl bg-panel2 p-3">
                  <div className="flex items-center justify-between">
                    <span>Code #{it.serial === null ? "?" : it.serial + 1} · {money(it.amount)}</span>
                    <span className={pillClass(it.status)}>{it.already ? "already settled" : statusLabel(it.status)}</span>
                  </div>
                  {it.reasons.map((r) => <p key={r} className={`mt-1 text-sm ${it.already ? "text-ok" : "text-bad"}`}>{r}</p>)}
                </div>
              ))}
              <div className="rounded-2xl border border-line p-3">
                <div className="flex items-center justify-between">
                  <span className="label">AI review (advisory)</span>
                  <span className={RISK_TONE[c.report.ai_review.risk]}>{c.report.ai_review.risk} risk</span>
                </div>
                {c.report.ai_review.findings.map((f) => <p key={f} className="mt-1 text-sm">• {f}</p>)}
              </div>
            </>
          )}
        </section>
      ))}
    </Shell>
  );
}
