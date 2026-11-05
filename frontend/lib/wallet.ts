import { utcStamp } from "./canonical";
import { signWithDeviceKey } from "./keys";
import { getWallet, putWallet, type WalletRecord } from "./store";
import type { QrPayload } from "./types";

/** The JSON that goes into the QR code. */
export function qrPayload(w: WalletRecord): QrPayload | null {
  if (!w.current) return null;
  return {
    certificate: w.certificate,
    cert_signature: w.cert_signature,
    voucher: w.current.voucher,
    device_signature: w.current.device_signature,
  };
}

/** Serials are consumed only by issuing: 0..max_vouchers-1, each exactly once. */
export const hasMoreCodes = (w: WalletRecord): boolean => w.next_serial < w.certificate.max_vouchers;

/** Highest serial this wallet has ever shown (-1 = none). */
export const highestIssued = (w: WalletRecord): number => w.next_serial - 1;

/** Sign and persist the next voucher (works fully offline). Returns the updated record. */
export async function issueNextCode(allowanceId: number): Promise<WalletRecord> {
  const w = await getWallet(allowanceId);
  if (!w) throw new Error("No wallet on this device");
  if (!hasMoreCodes(w)) throw new Error("No offline codes left on this allowance");
  const voucher = { allowance_id: allowanceId, serial: w.next_serial, issued_at: utcStamp() };
  const device_signature = await signWithDeviceKey(allowanceId, voucher);
  const next: WalletRecord = { ...w, next_serial: w.next_serial + 1, current: { voucher, device_signature } };
  await putWallet(next);
  return next;
}

/** Show-to-Pay: reuse the current code if there is one, else issue the first. */
export async function ensureCurrentCode(allowanceId: number): Promise<WalletRecord> {
  const w = await getWallet(allowanceId);
  if (!w) throw new Error("No wallet on this device");
  return w.current ? w : issueNextCode(allowanceId);
}
