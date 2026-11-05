import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { canonicalBytes } from "./canonical";
import { decodeQr, encodeQr, QR_PREFIX } from "./compact";
import type { QrPayload } from "./types";
import { parseQr, verifyOffline } from "./verify";

const fx = JSON.parse(readFileSync(resolve(__dirname, "../../shared/signed_fixture.json"), "utf-8"));
const full: QrPayload = {
  certificate: fx.certificate, cert_signature: fx.cert_signature, voucher: fx.voucher, device_signature: fx.device_signature,
};

describe("compact QR transport", () => {
  it("round-trips to the exact signed objects (canonical bytes identical to the shared fixture)", () => {
    const back = decodeQr(encodeQr(full));
    expect(canonicalBytes(back.certificate)).toEqual(canonicalBytes(fx.certificate));
    expect(canonicalBytes(back.voucher)).toEqual(canonicalBytes(fx.voucher));
    expect(back.cert_signature).toBe(fx.cert_signature);
    expect(back.device_signature).toBe(fx.device_signature);
    expect(back.certificate.device_pubkey).toBe(fx.certificate.device_pubkey);
  });

  it("is versioned, uses base64url and is much smaller than the full JSON", () => {
    const text = encodeQr(full);
    const before = JSON.stringify(full).length;
    console.log(`QR payload length: before ${before} chars, after ${text.length} chars`);
    expect(text.startsWith(QR_PREFIX)).toBe(true);
    expect(text).not.toMatch(/[+/=]/); // url-safe base64, no padding
    expect(text.length).toBeLessThan(before * 0.75);
  });

  it("the merchant verifies a scanned compact code against the ORIGINAL signatures", async () => {
    const qr = parseQr(encodeQr(full));
    const reasons = await verifyOffline({
      qr, amount: "3.50", merchant: { category: "food" }, serverPubkey: fx.server_pubkey_b64,
      queuedSerials: new Set(), now: new Date("2026-11-05T13:00:00Z"),
    });
    expect(reasons).toEqual([]);
  });

  it("a tampered compact code fails verification", async () => {
    const wire = JSON.parse(encodeQr(full).slice(QR_PREFIX.length));
    wire.c.m = 500; // raise the per-tap limit
    const qr = parseQr(QR_PREFIX + JSON.stringify(wire));
    const reasons = await verifyOffline({
      qr, amount: "9", merchant: { category: "food" }, serverPubkey: fx.server_pubkey_b64,
      queuedSerials: new Set(), now: new Date("2026-11-05T13:00:00Z"),
    });
    expect(reasons).toContain("Certificate signature is not valid (it was not issued by LeashPay)");
  });

  it("still accepts the legacy full-JSON code (dev paste) and rejects junk", () => {
    expect(parseQr(JSON.stringify(full)).voucher.serial).toBe(2);
    expect(() => parseQr("LP1:{nope")).toThrow("That isn't a LeashPay code");
    expect(() => parseQr("hello")).toThrow("That isn't a LeashPay code");
  });
});
