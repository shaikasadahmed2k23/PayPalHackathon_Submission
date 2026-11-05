// Local (offline) verification on the merchant phone. Mirrors backend
// app/services/voucher_verify.py check for check, plus the local-queue duplicate check and a
// "not expired right now" check that only a live scanner can make.

import { decodeQr, QR_PREFIX } from "./compact";
import { verifyObject } from "./keys";
import type { Certificate, QrPayload, Merchant, Voucher } from "./types";

export interface VerifyInput {
  qr: QrPayload;
  amount: string; // exactly what the merchant typed, e.g. "3.50"
  merchant: Pick<Merchant, "category">;
  serverPubkey: string;
  queuedSerials: Set<number>; // serials already in this phone's queue for the same allowance
  now?: Date;
}

const usd = (cents: number) => `$${(cents / 100).toFixed(2)}`;
const label = (c: string) => c.charAt(0).toUpperCase() + c.slice(1);
const ts = (s: unknown): number => {
  const t = typeof s === "string" ? Date.parse(s) : NaN;
  if (Number.isNaN(t)) throw new Error("bad timestamp");
  return t;
};

/** Typed amount -> integer cents, or a reason. Whole cents only, like the backend. */
export function parseAmount(text: string): { cents: number } | { error: string } {
  const t = text.trim();
  if (!/^\d+(\.\d+)?$/.test(t)) return { error: "Amount must be greater than zero" };
  const [whole, frac = ""] = t.split(".");
  if (frac.length > 2 && /[1-9]/.test(frac.slice(2))) return { error: "Amount must be in whole cents" };
  const cents = Number(whole) * 100 + Number((frac + "00").slice(0, 2));
  return cents > 0 ? { cents } : { error: "Amount must be greater than zero" };
}

/** Plain-English failure reasons; an empty list means the payment is valid. */
export async function verifyOffline(i: VerifyInput): Promise<string[]> {
  const { qr, merchant } = i;
  const now = (i.now ?? new Date()).getTime();
  const reasons: string[] = [];
  const cert = qr.certificate as Certificate;
  const voucher = qr.voucher as Voucher;

  if (!(await verifyObject(i.serverPubkey, cert, qr.cert_signature)))
    reasons.push("Certificate signature is not valid (it was not issued by LeashPay)");
  if (typeof cert?.device_pubkey !== "string" || !(await verifyObject(cert.device_pubkey, voucher, qr.device_signature)))
    reasons.push("Voucher signature is not valid for this buyer's device");

  let perTapCents: number;
  let blocked: string[];
  let allowed: string[];
  try {
    const { serial, issued_at } = voucher;
    if (voucher.allowance_id !== cert.allowance_id) reasons.push("Voucher does not belong to the allowance on the certificate");
    if (!Number.isInteger(serial) || serial < 0) reasons.push("Voucher serial number is invalid");
    else if (serial >= cert.max_vouchers)
      reasons.push(`Voucher #${serial} is beyond the ${cert.max_vouchers} vouchers this allowance allows`);
    const expires = ts(cert.expires_at);
    if (ts(issued_at) > expires) reasons.push("Voucher was issued after the allowance expired");
    else if (now > expires) reasons.push("This allowance has expired"); // merchant-side only; the server allows late SYNC
    if (!Array.isArray(cert.allowed_categories) || !Array.isArray(cert.blocked_categories)) throw new Error("category lists");
    allowed = cert.allowed_categories;
    blocked = cert.blocked_categories;
    perTapCents = Math.round(Number(cert.per_tap_max) * 100);
    if (!Number.isFinite(perTapCents)) throw new Error("per_tap_max");
  } catch (e) {
    reasons.push(`Payment is malformed (${e instanceof Error ? e.message : "error"})`);
    return reasons;
  }

  const amount = parseAmount(i.amount);
  if ("error" in amount) reasons.push(amount.error);
  else if (amount.cents > perTapCents) reasons.push(`${usd(amount.cents)} is over the ${usd(perTapCents)} per-tap limit`);

  if (blocked.includes(merchant.category)) reasons.push(`${label(merchant.category)} purchases are blocked by the buyer's rules`);
  else if (allowed.length > 0 && !allowed.includes(merchant.category))
    reasons.push(`${label(merchant.category)} is not in the buyer's allowed categories`);

  if (i.queuedSerials.has(voucher.serial)) reasons.push("This code was already scanned on this phone");
  return reasons;
}

/** Parse what the scanner / paste box gave us: the compact "LP1:" format (or, for dev, the full JSON). */
export function parseQr(text: string): QrPayload {
  const t = text.trim();
  try {
    if (t.startsWith(QR_PREFIX)) return decodeQr(t);
    const q = JSON.parse(t) as Partial<QrPayload> | null;
    if (q && typeof q === "object" && q.certificate && q.voucher && typeof q.cert_signature === "string" && typeof q.device_signature === "string")
      return q as QrPayload;
  } catch {
    /* fall through */
  }
  throw new Error("That isn't a LeashPay code");
}
