import { api } from "./api";
import { queued, removeQueued, type QueuedPayment } from "./store";
import type { SettlementReport } from "./types";

export interface SyncResult {
  merchant_id: number;
  report?: SettlementReport;
  error?: string;
}

let running = false;

/**
 * POST the queue to /vouchers/settle, one request per merchant. Never two at once: an in-tab flag
 * plus a Web Lock so a second tab/window on the same browser can't send the same queue again.
 * The queue is re-read inside the lock, and a merchant's payments leave it as soon as that
 * request's report comes back (a failed request leaves them queued for the next attempt).
 * The server is idempotent too, so a lost response is safe to retry.
 */
export async function syncQueue(): Promise<SyncResult[]> {
  if (running) return [];
  running = true;
  try {
    if (typeof navigator !== "undefined" && navigator.locks) {
      const out = await navigator.locks.request("leashpay-sync", { ifAvailable: true }, async (lock) => (lock ? doSync() : []));
      return out ?? [];
    }
    return await doSync();
  } finally {
    running = false;
  }
}

async function doSync(): Promise<SyncResult[]> {
  const all = await queued();
  const byMerchant = new Map<number, QueuedPayment[]>();
  for (const p of all) byMerchant.set(p.merchant_id, [...(byMerchant.get(p.merchant_id) ?? []), p]);

  const results: SyncResult[] = [];
  for (const [merchant_id, items] of byMerchant) {
    const payments = items.map(({ qid: _qid, ...rest }) => rest); // eslint-disable-line @typescript-eslint/no-unused-vars
    try {
      const report = await api<SettlementReport>("/vouchers/settle", { body: { merchant_id, payments } });
      await removeQueued(items.map((i) => i.qid!)); // confirmed: settled, already settled, or rejected
      results.push({ merchant_id, report });
    } catch (e) {
      results.push({ merchant_id, error: e instanceof Error ? e.message : "Sync failed" });
    }
  }
  return results;
}
