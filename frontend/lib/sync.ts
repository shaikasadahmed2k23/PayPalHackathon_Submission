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
 * POST the queue to /vouchers/settle, one request per merchant. A request that succeeds has
 * processed every payment in it (settled or rejected), so those leave the queue; a failed
 * request leaves its payments queued for the next attempt.
 */
export async function syncQueue(): Promise<SyncResult[]> {
  if (running) return [];
  running = true;
  try {
    const all = await queued();
    const byMerchant = new Map<number, QueuedPayment[]>();
    for (const p of all) byMerchant.set(p.merchant_id, [...(byMerchant.get(p.merchant_id) ?? []), p]);

    const results: SyncResult[] = [];
    for (const [merchant_id, items] of byMerchant) {
      const payments = items.map(({ qid: _qid, ...rest }) => rest); // eslint-disable-line @typescript-eslint/no-unused-vars
      try {
        const report = await api<SettlementReport>("/vouchers/settle", { body: { merchant_id, payments } });
        await removeQueued(items.map((i) => i.qid!));
        results.push({ merchant_id, report });
      } catch (e) {
        results.push({ merchant_id, error: e instanceof Error ? e.message : "Sync failed" });
      }
    }
    return results;
  } finally {
    running = false;
  }
}
