/* eslint-disable react-hooks/set-state-in-effect -- these effects load data from IndexedDB / the API into state */
"use client";

import { QRCodeSVG } from "qrcode.react";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ErrorNote, Shell } from "@/components/Shell";
import { api, money } from "@/lib/api";
import { useOnline } from "@/lib/hooks";
import { currentWallet, getCache, setCache, type WalletRecord } from "@/lib/store";
import type { ActivityItem, AllowanceInfo, Approval } from "@/lib/types";
import { ensureCurrentCode, hasMoreCodes, highestIssued, issueNextCode, qrPayload } from "@/lib/wallet";

interface Snapshot {
  info: AllowanceInfo;
  approvals: Approval[];
  activity: ActivityItem[];
  at: string;
}

const STATUS_TONE: Record<string, string> = {
  CAPTURED: "chip-ok", SETTLED: "chip-ok", SETTLED_PAYOUT_PENDING: "chip-ok", REFUNDED: "chip-warn",
  PARTIALLY_REFUNDED: "chip-warn", BLOCKED: "chip-bad", REJECTED: "chip-bad", DENIED: "chip-bad", PENDING: "chip-warn",
};

export default function Wallet() {
  const router = useRouter();
  const online = useOnline();
  const [wallet, setWallet] = useState<WalletRecord | null>(null);
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [showQr, setShowQr] = useState(false);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);

  const reportIssued = useCallback(async (w: WalletRecord) => {
    try {
      await api(`/allowance/${w.allowance_id}/issued`, { body: { highest_serial: highestIssued(w) } });
    } catch {
      /* best effort; retried on the next refresh */
    }
  }, []);

  const refresh = useCallback(async (w: WalletRecord) => {
    const id = w.allowance_id;
    if (!navigator.onLine) return;
    try {
      const [info, pending, activity] = await Promise.all([
        api<AllowanceInfo>(`/allowance/${id}`),
        api<Approval[]>("/approvals?status=PENDING"),
        api<ActivityItem[]>(`/allowance/${id}/activity`),
      ]);
      const next: Snapshot = {
        info, approvals: pending.filter((a) => a.allowance_id === id), activity, at: new Date().toISOString(),
      };
      setSnap(next);
      setError("");
      await setCache(`snapshot:${id}`, next);
      await reportIssued((await currentWallet()) ?? w);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't refresh");
    }
  }, [reportIssued]);

  // First load: wallet from IndexedDB (works offline), then last snapshot, then live data.
  useEffect(() => {
    (async () => {
      const w = await currentWallet();
      if (!w) {
        router.replace("/setup");
        return;
      }
      setWallet(w);
      setSnap((await getCache<Snapshot>(`snapshot:${w.allowance_id}`)) ?? null);
    })();
  }, [router]);

  // Back online -> refresh. While online -> poll gently so approvals show up.
  useEffect(() => {
    if (!wallet || !online) return;
    refresh(wallet);
    const t = setInterval(() => refresh(wallet), 15000);
    return () => clearInterval(t);
  }, [wallet, online, refresh]);

  async function openQr() {
    if (!wallet) return;
    try {
      const w = await ensureCurrentCode(wallet.allowance_id);
      setWallet(w);
      setShowQr(true);
      if (navigator.onLine) reportIssued(w);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't create a code");
    }
  }

  async function nextCode() {
    if (!wallet) return;
    try {
      const w = await issueNextCode(wallet.allowance_id);
      setWallet(w);
      if (navigator.onLine) reportIssued(w);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't create a code");
    }
  }

  async function decide(a: Approval, action: "approve" | "deny") {
    setBusyId(a.id);
    try {
      await api(`/approvals/${a.id}/${action}`, { method: "POST", body: {} });
      if (wallet) await refresh(wallet);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't update that approval");
    } finally {
      setBusyId(null);
    }
  }

  if (!wallet) return <Shell title="Wallet"><div className="card text-muted">Loading…</div></Shell>;

  const cert = wallet.certificate;
  const offlineOn = cert.max_vouchers > 0;
  const info = snap?.info;
  const payload = qrPayload(wallet);

  return (
    <Shell title="Wallet">
      <section className="card">
        <p className="label">Left to spend</p>
        <p className="num mt-1 text-[3.6rem] leading-none text-accent">{info ? money(info.remaining) : "—"}</p>
        <div className="mt-4 flex items-end justify-between text-sm">
          <div>
            <p className="label">Spent today</p>
            <p className="num text-xl">{info ? money(info.spent_today) : "—"}</p>
          </div>
          <div className="text-right">
            <p className="label">Allowance</p>
            <p className="num text-xl">{info ? money(info.amount) : "—"}</p>
          </div>
          <span className={`chip ${info?.status === "ACTIVE" ? "chip-ok" : ""}`}>{info?.status ?? "…"}</span>
        </div>
        {snap && !online && (
          <p className="mt-3 text-xs text-muted">Offline · numbers as of {new Date(snap.at).toLocaleTimeString()}</p>
        )}
      </section>

      <button className="btn btn-primary !min-h-20 !text-xl" disabled={!offlineOn} onClick={openQr}>
        {offlineOn ? "Show to Pay" : "Offline codes are off for this allowance"}
      </button>

      <ErrorNote>{error}</ErrorNote>

      {snap && snap.approvals.length > 0 && (
        <section className="space-y-3">
          <p className="label">Needs your OK</p>
          {snap.approvals.map((a) => (
            <div key={a.id} className="card border-warn/40">
              <div className="flex items-baseline justify-between">
                <span className="font-semibold">{a.merchant_name}</span>
                <span className="num text-2xl">{money(a.amount)}</span>
              </div>
              <p className="text-sm text-muted">{a.category} · {a.channel}</p>
              <div className="mt-3 flex gap-2">
                <button className="btn btn-primary btn-sm flex-1" disabled={!online || busyId === a.id} onClick={() => decide(a, "approve")}>Approve</button>
                <button className="btn btn-sm flex-1" disabled={!online || busyId === a.id} onClick={() => decide(a, "deny")}>Deny</button>
              </div>
            </div>
          ))}
        </section>
      )}

      <section className="space-y-2">
        <p className="label">Activity</p>
        {!snap || snap.activity.length === 0 ? (
          <p className="text-muted">Nothing yet. Spend something and it shows up here.</p>
        ) : (
          snap.activity.map((it) => (
            <div key={`${it.kind}-${it.id}`} className="card !p-4">
              <div className="flex items-baseline justify-between gap-3">
                <span className="font-semibold">
                  {it.kind === "refund" ? "↩ " : it.channel === "offline" ? "◌ " : ""}
                  {it.title}
                </span>
                <span className={`num text-lg ${it.kind === "refund" ? "text-ok" : ""}`}>
                  {it.kind === "refund" ? "+" : ""}
                  {money(it.amount)}
                </span>
              </div>
              <div className="mt-1 flex items-center justify-between gap-3 text-sm text-muted">
                <span className="truncate">{it.detail}</span>
                <span className={`chip ${STATUS_TONE[it.status] ?? ""}`}>{it.status.replaceAll("_", " ").toLowerCase()}</span>
              </div>
              {it.reasons.length > 0 && ["BLOCKED", "REJECTED"].includes(it.status) && (
                <p className="mt-2 text-sm text-bad">{it.reasons.join("; ")}</p>
              )}
              <p className="mt-1 text-xs text-muted">{new Date(it.ts).toLocaleString()}</p>
            </div>
          ))
        )}
      </section>

      {showQr && payload && (
        <div className="fixed inset-0 z-50 flex flex-col items-center justify-between overflow-y-auto bg-ink px-6 pb-8 pt-6">
          <div className="flex w-full max-w-md items-center justify-between">
            <span className="chip">Code {wallet.current!.voucher.serial + 1} of {cert.max_vouchers}</span>
            {!online && <span className="chip chip-warn">OFFLINE</span>}
            <button className="btn btn-sm" onClick={() => setShowQr(false)}>Close</button>
          </div>

          <div className="w-full max-w-sm rounded-3xl bg-white p-4">
            <QRCodeSVG value={JSON.stringify(payload)} level="L" marginSize={1} style={{ width: "100%", height: "auto" }} />
          </div>

          <div className="w-full max-w-sm text-center">
            <p className="label">Up to</p>
            <p className="num text-5xl text-accent">{money(cert.per_tap_max)}</p>
            <p className="mt-1 text-sm text-muted">per tap · show this to the seller</p>
            <button className="btn mt-5 w-full" disabled={!hasMoreCodes(wallet)} onClick={nextCode}>
              {hasMoreCodes(wallet) ? "Next code" : "No more codes on this allowance"}
            </button>
            <p className="mt-2 text-xs text-muted">The code stays put until you tap Next, so it can&apos;t be used up by accident.</p>
          </div>
        </div>
      )}
    </Shell>
  );
}
