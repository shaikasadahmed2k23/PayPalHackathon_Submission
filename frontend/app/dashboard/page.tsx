/* eslint-disable react-hooks/set-state-in-effect -- loads data into state */
"use client";

import type { ColDef } from "ag-grid-community";
import { useCallback, useEffect, useMemo, useState } from "react";
import { DataGrid, Mono, Reasons, VerdictPill, moneyFmt, when } from "@/components/DataGrid";
import { ErrorNote, Shell } from "@/components/Shell";
import { api, money } from "@/lib/api";
import { currentWallet } from "@/lib/store";
import type { Merchant, PurchaseOut } from "@/lib/types";

interface AllowanceRow { allowance_id: number; amount: number; status: string; captured_total: number }
interface Summary {
  held: number; captured: number; refunded: number; returned_on_close: number;
  blocked_attempts: number; vouchers_settled: number; vouchers_rejected: number; status: string;
}
interface AuditRow { id: number; ts: string; actor: string; action: string; verdict: string | null; reasons: string[]; paypal_ref: string | null; payload_json: Record<string, unknown> }
interface VoucherRow { id: number; serial: number; merchant_id: number; amount: number; status: string; issued_at: string; scanned_at: string | null; capture_id: string | null; payout_batch_id: string | null; judge_reasons: string[] }
interface LedgerRow { id: number; created_at: string; merchant_name: string; category: string; channel: string; source: string; amount: number; refunded_amount: number; capture_id: string | null }
type PurchaseRow = PurchaseOut & { itemsText: string };

type Tab = "audit" | "purchases" | "vouchers" | "ledger";
const TABS: { id: Tab; label: string }[] = [
  { id: "audit", label: "Audit log" },
  { id: "purchases", label: "Purchases" },
  { id: "vouchers", label: "Vouchers" },
  { id: "ledger", label: "Ledger" },
];

const money2 = { valueFormatter: (p: { value: number }) => moneyFmt(p.value), type: "rightAligned", filter: "agNumberColumnFilter" } as const;
const pill = { cellRenderer: VerdictPill, width: 170 } as const;
const mono = { cellRenderer: Mono, width: 200 } as const;
const wrap = { cellRenderer: Reasons, cellClass: "reason-cell", wrapText: true, autoHeight: true, flex: 2, minWidth: 260 } as const;

// Agent chatter that is not a decision; hidden by the "Decisions only" toggle.
const CHATTER = new Set(["intent", "propose_cart"]);

function Kpi({ label, value, tone = "" }: { label: string; value: string; tone?: string }) {
  return (
    <div className="card !p-4">
      <p className="label">{label}</p>
      <p className={`num mt-1 text-3xl ${tone}`}>{value}</p>
    </div>
  );
}

export default function Dashboard() {
  const [allowances, setAllowances] = useState<AllowanceRow[]>([]);
  const [aid, setAid] = useState<number | null>(null);
  const [tab, setTab] = useState<Tab>("audit");
  const [filter, setFilter] = useState("");
  const [decisionsOnly, setDecisionsOnly] = useState(true);
  const [error, setError] = useState("");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [audit, setAudit] = useState<AuditRow[]>([]);
  const [purchases, setPurchases] = useState<PurchaseRow[]>([]);
  const [vouchers, setVouchers] = useState<VoucherRow[]>([]);
  const [ledger, setLedger] = useState<LedgerRow[]>([]);
  const [merchants, setMerchants] = useState<Record<number, string>>({});

  const loadList = useCallback(async () => {
    try {
      const [list, w, ms] = await Promise.all([
        api<AllowanceRow[]>("/allowance"), currentWallet().catch(() => undefined), api<Merchant[]>("/merchants"),
      ]);
      setAllowances(list);
      setMerchants(Object.fromEntries(ms.map((m) => [m.id, m.name])));
      setAid((cur) => cur ?? (w && list.some((a) => a.allowance_id === w.allowance_id) ? w.allowance_id : (list[0]?.allowance_id ?? null)));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't load");
    }
  }, []);

  const load = useCallback(async () => {
    if (aid === null) return;
    try {
      const [s, a, p, v, l] = await Promise.all([
        api<Summary>(`/allowance/${aid}/summary`),
        api<AuditRow[]>(`/audit?allowance_id=${aid}&limit=500`),
        api<PurchaseOut[]>(`/purchases?allowance_id=${aid}`),
        api<VoucherRow[]>(`/vouchers?allowance_id=${aid}`),
        api<LedgerRow[]>(`/ledger?allowance_id=${aid}`),
      ]);
      setSummary(s);
      setAudit(a);
      setPurchases(p.map((x) => ({ ...x, itemsText: x.items.map((i) => (i.qty > 1 ? `${i.qty}× ${i.name}` : i.name)).join(", ") })));
      setVouchers(v);
      setLedger(l);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't load");
    }
  }, [aid]);

  useEffect(() => {
    loadList();
  }, [loadList]);

  useEffect(() => {
    load();
  }, [load]);

  // An allowance closed in the wallet shows as CLOSED as soon as you come back to this tab.
  useEffect(() => {
    const refresh = () => {
      if (document.visibilityState === "visible") {
        loadList();
        load();
      }
    };
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [loadList, load]);

  const shownAudit = useMemo(() => (decisionsOnly ? audit.filter((r) => !CHATTER.has(r.action)) : audit), [audit, decisionsOnly]);

  const cols = useMemo(() => {
    const audit_: ColDef<AuditRow>[] = [
      { field: "id", headerName: "#", width: 80, sort: "desc" },
      { field: "ts", headerName: "Time", valueFormatter: (p) => when(p.value), width: 180 },
      { field: "actor", width: 150 },
      { field: "action", width: 170 },
      { field: "verdict", ...pill },
      { field: "reasons", ...wrap },
      { field: "paypal_ref", headerName: "PayPal ref", ...mono },
      { field: "payload_json", headerName: "Details", valueFormatter: (p) => JSON.stringify(p.value), flex: 1, minWidth: 200 },
    ];
    const purchases_: ColDef<PurchaseRow>[] = [
      { field: "id", headerName: "#", width: 80, sort: "desc" },
      { field: "merchant", width: 150 },
      { field: "itemsText", headerName: "Items", flex: 1, minWidth: 200 },
      { field: "amount", ...money2, width: 120 },
      { field: "status", ...pill },
      { field: "reasons", headerName: "Judge reasons", ...wrap },
      { field: "capture_id", headerName: "Capture", ...mono },
    ];
    const vouchers_: ColDef<VoucherRow>[] = [
      { field: "id", headerName: "#", width: 80, sort: "desc" },
      { field: "scanned_at", headerName: "Scanned", valueFormatter: (p) => when(p.value), width: 180 },
      { field: "serial", headerName: "Code #", valueFormatter: (p) => String(p.value + 1), width: 100 },
      { field: "merchant_id", headerName: "Merchant", valueFormatter: (p) => merchants[p.value] ?? String(p.value), width: 150 },
      { field: "amount", ...money2, width: 110 },
      { field: "status", ...pill },
      { field: "judge_reasons", headerName: "Reasons", ...wrap },
      { field: "capture_id", headerName: "Capture", ...mono },
      { field: "payout_batch_id", headerName: "Payout batch", ...mono },
    ];
    const ledger_: ColDef<LedgerRow>[] = [
      { field: "id", headerName: "#", width: 80, sort: "desc" },
      { field: "created_at", headerName: "Time", valueFormatter: (p) => when(p.value), width: 180 },
      { field: "merchant_name", headerName: "Merchant", width: 160 },
      { field: "category", width: 130 },
      { field: "channel", width: 110 },
      { field: "source", width: 110 },
      { field: "amount", ...money2, width: 110 },
      { field: "refunded_amount", headerName: "Refunded", ...money2, width: 120 },
      { field: "capture_id", headerName: "Capture", ...mono },
    ];
    return { audit_, purchases_, vouchers_, ledger_ };
  }, [merchants]);

  return (
    <Shell title="Dashboard" wide>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-3xl font-extrabold tracking-tight">Every decision, on record.</h1>
          <p className="mt-1 text-sm text-muted">Audit trail, purchases, offline vouchers and the ledger.</p>
        </div>
        <div className="flex items-center gap-2">
          <select className="field !w-auto !py-2" value={aid ?? ""} onChange={(e) => setAid(Number(e.target.value))}>
            {allowances.length === 0 && <option value="">No allowances yet</option>}
            {allowances.map((a) => (
              <option key={a.allowance_id} value={a.allowance_id}>
                Allowance #{a.allowance_id} · {money(a.amount)} · {a.status}
              </option>
            ))}
          </select>
          <button className="btn btn-sm" onClick={() => { loadList(); load(); }}>Refresh</button>
        </div>
      </div>

      <ErrorNote>{error}</ErrorNote>

      {summary && (
        <section className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <Kpi label="Held" value={money(summary.held)} />
          <Kpi label="Captured" value={money(summary.captured)} tone="text-accent" />
          <Kpi label="Refunded" value={money(summary.refunded)} tone="text-warn" />
          <Kpi label="Returned on close" value={summary.status === "CLOSED" ? money(summary.returned_on_close) : "—"} tone="text-ok" />
          <Kpi label="Blocked attempts" value={String(summary.blocked_attempts)} tone="text-bad" />
          <Kpi label="Offline settled / rejected" value={`${summary.vouchers_settled} / ${summary.vouchers_rejected}`} />
        </section>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex gap-1 rounded-2xl border border-line bg-panel p-1">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`rounded-xl px-4 py-2 text-sm font-semibold ${tab === t.id ? "bg-accent text-white" : "text-muted hover:text-text"}`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-4">
          {tab === "audit" && (
            <label className="flex cursor-pointer items-center gap-2 text-sm font-semibold">
              <input type="checkbox" className="h-4 w-4 accent-[#0070e0]" checked={decisionsOnly} onChange={(e) => setDecisionsOnly(e.target.checked)} />
              Decisions only
            </label>
          )}
          <input className="field !w-64 !py-2" placeholder="Search everything…" value={filter} onChange={(e) => setFilter(e.target.value)} />
        </div>
      </div>

      {tab === "audit" && <DataGrid rows={shownAudit} columns={cols.audit_} quickFilter={filter} />}
      {tab === "purchases" && <DataGrid rows={purchases} columns={cols.purchases_} quickFilter={filter} />}
      {tab === "vouchers" && <DataGrid rows={vouchers} columns={cols.vouchers_} quickFilter={filter} />}
      {tab === "ledger" && <DataGrid rows={ledger} columns={cols.ledger_} quickFilter={filter} />}
    </Shell>
  );
}
