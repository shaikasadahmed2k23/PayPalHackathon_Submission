// Compact TRANSPORT format for the QR code only ("LP1:" + short-key JSON).
// The signed objects are untouched: the merchant expands the QR back to the exact full
// certificate / voucher (same keys, values and strings) before verifying, so signatures
// stay over the full canonical JSON that the backend also sees.
//
//   LP1:{"c":{"a","p","m","x","ac","bc","e"},"k":<cert sig>,"v":{"s","t"},"d":<device sig>}
//   a=allowance_id p=device_pubkey m=per_tap_max x=max_vouchers ac/bc=allowed/blocked categories
//   e=expires_at (epoch s)  s=serial  t=issued_at (epoch s)  k/d=signatures (base64url, no padding)
// The voucher's allowance_id is not sent; it is the certificate's `a`.

import type { Certificate, QrPayload } from "./types";
import { utcStamp } from "./canonical";

export const QR_PREFIX = "LP1:";
const CERT_KEYS = ["allowance_id", "device_pubkey", "per_tap_max", "max_vouchers", "allowed_categories", "blocked_categories", "expires_at"];
const VOUCHER_KEYS = ["allowance_id", "serial", "issued_at"];

const toUrl = (b64: string): string => b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const fromUrl = (u: string): string => {
  const b = u.replace(/-/g, "+").replace(/_/g, "/");
  return b + "=".repeat((4 - (b.length % 4)) % 4);
};
const epoch = (stamp: string): number => {
  const t = Date.parse(stamp);
  if (Number.isNaN(t) || utcStamp(new Date(t)) !== stamp) throw new Error(`timestamp not in canonical form: ${stamp}`);
  return t / 1000;
};
const sameKeys = (o: object, keys: string[]) => {
  const k = Object.keys(o).sort().join();
  if (k !== [...keys].sort().join()) throw new Error("unexpected fields; cannot use the compact format");
};

export function encodeQr(q: QrPayload): string {
  sameKeys(q.certificate, CERT_KEYS);
  sameKeys(q.voucher, VOUCHER_KEYS);
  if (q.voucher.allowance_id !== q.certificate.allowance_id) throw new Error("voucher/certificate allowance mismatch");
  const c = q.certificate;
  return (
    QR_PREFIX +
    JSON.stringify({
      c: { a: c.allowance_id, p: toUrl(c.device_pubkey), m: c.per_tap_max, x: c.max_vouchers, ac: c.allowed_categories, bc: c.blocked_categories, e: epoch(c.expires_at) },
      k: toUrl(q.cert_signature),
      v: { s: q.voucher.serial, t: epoch(q.voucher.issued_at) },
      d: toUrl(q.device_signature),
    })
  );
}

interface Wire {
  c: { a: number; p: string; m: number; x: number; ac: string[]; bc: string[]; e: number };
  k: string;
  v: { s: number; t: number };
  d: string;
}

/** Expand "LP1:..." back to the exact full objects. Throws on anything malformed. */
export function decodeQr(text: string): QrPayload {
  if (!text.startsWith(QR_PREFIX)) throw new Error("not a compact LeashPay code");
  const w = JSON.parse(text.slice(QR_PREFIX.length)) as Wire;
  const certificate: Certificate = {
    allowance_id: w.c.a,
    device_pubkey: fromUrl(w.c.p),
    per_tap_max: w.c.m,
    max_vouchers: w.c.x,
    allowed_categories: w.c.ac,
    blocked_categories: w.c.bc,
    expires_at: utcStamp(new Date(w.c.e * 1000)),
  };
  return {
    certificate,
    cert_signature: fromUrl(w.k),
    voucher: { allowance_id: w.c.a, serial: w.v.s, issued_at: utcStamp(new Date(w.v.t * 1000)) },
    device_signature: fromUrl(w.d),
  };
}
