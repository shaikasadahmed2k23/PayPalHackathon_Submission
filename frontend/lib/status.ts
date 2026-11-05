// One place decides which colour a status gets, so every screen agrees:
// green = pass/captured/settled, amber = needs approval, red = blocked/rejected, blue-grey = refunded.
const OK = ["CAPTURED", "SETTLED", "SETTLED_PAYOUT_PENDING", "COMPLETED", "PASS", "APPROVED", "ACTIVE"];
const WARN = ["PENDING_APPROVAL", "NEEDS_APPROVAL", "PENDING"];
const BAD = ["BLOCKED", "REJECTED", "FAIL", "DENIED"];
const INFO = ["REFUNDED", "PARTIALLY_REFUNDED"];

export function pillClass(status: string): string {
  const s = status.toUpperCase();
  const tone = OK.includes(s) ? "pill-ok" : WARN.includes(s) ? "pill-warn" : BAD.includes(s) ? "pill-bad" : INFO.includes(s) ? "pill-info" : "pill-neutral";
  return `pill ${tone}`;
}

export const statusLabel = (status: string): string => status.replaceAll("_", " ").toLowerCase();
