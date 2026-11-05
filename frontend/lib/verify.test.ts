import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { fromB64, signObject, toB64 } from "./keys";
import * as ed from "@noble/ed25519";
import type { Certificate, QrPayload } from "./types";
import { parseAmount, parseQr, verifyOffline } from "./verify";

const fx = JSON.parse(readFileSync(resolve(__dirname, "../../shared/signed_fixture.json"), "utf-8"));
const NOW = new Date("2026-11-05T13:00:00Z"); // after the voucher was issued, before the cert expires (2026-11-08)

const good: QrPayload = {
  certificate: fx.certificate, cert_signature: fx.cert_signature, voucher: fx.voucher, device_signature: fx.device_signature,
};
const food = { category: "food" };

const run = (over: Partial<Parameters<typeof verifyOffline>[0]> = {}) =>
  verifyOffline({ qr: good, amount: "3.50", merchant: food, serverPubkey: fx.server_pubkey_b64, queuedSerials: new Set(), now: NOW, ...over });

describe("verifyOffline mirrors backend voucher_verify.py", () => {
  it("accepts a valid payment", async () => {
    expect(await run()).toEqual([]);
    expect(await run({ amount: "5" })).toEqual([]); // exactly the per-tap limit
  });

  it("rejects a certificate not signed by the server key", async () => {
    const other = toB64(await ed.getPublicKeyAsync(new Uint8Array(32).fill(9)));
    expect(await run({ serverPubkey: other })).toContain("Certificate signature is not valid (it was not issued by LeashPay)");
  });

  it("rejects a tampered certificate (e.g. raised per-tap limit)", async () => {
    const cert: Certificate = { ...fx.certificate, per_tap_max: 500 };
    const r = await run({ qr: { ...good, certificate: cert } });
    expect(r).toContain("Certificate signature is not valid (it was not issued by LeashPay)");
  });

  it("rejects a voucher signed by the wrong device", async () => {
    const forged = await signObject(new Uint8Array(32).fill(7), fx.voucher);
    expect(await run({ qr: { ...good, device_signature: forged } })).toContain("Voucher signature is not valid for this buyer's device");
  });

  it("serial must be below max_vouchers (fixture allows 6)", async () => {
    const secret = fromB64(fx.device_seed_b64);
    const voucher = { ...fx.voucher, serial: 6 };
    const r = await run({ qr: { ...good, voucher, device_signature: await signObject(secret, voucher) } });
    expect(r).toEqual(["Voucher #6 is beyond the 6 vouchers this allowance allows"]);
  });

  it("amount: zero, sub-cent, over the limit", async () => {
    expect(await run({ amount: "0" })).toEqual(["Amount must be greater than zero"]);
    expect(await run({ amount: "" })).toEqual(["Amount must be greater than zero"]);
    expect(await run({ amount: "1.005" })).toEqual(["Amount must be in whole cents"]);
    expect(await run({ amount: "5.01" })).toEqual(["$5.01 is over the $5.00 per-tap limit"]);
  });

  it("expiry: not usable after the allowance expired", async () => {
    expect(await run({ now: new Date("2026-11-09T00:00:00Z") })).toEqual(["This allowance has expired"]);
  });

  it("categories: blocked and not-allowed", async () => {
    expect(await run({ merchant: { category: "alcohol" } })).toEqual(["Alcohol purchases are blocked by the buyer's rules"]);
    expect(await run({ merchant: { category: "electronics" } })).toEqual(["Electronics is not in the buyer's allowed categories"]);
  });

  it("serial already in the local queue", async () => {
    expect(await run({ queuedSerials: new Set([2]) })).toEqual(["This code was already scanned on this phone"]);
  });

  it("collects every failure, like the backend", async () => {
    const r = await run({ amount: "9", merchant: { category: "alcohol" }, queuedSerials: new Set([2]) });
    expect(r).toHaveLength(3);
  });
});

describe("parseAmount / parseQr", () => {
  it("parses whole cents", () => {
    expect(parseAmount("3")).toEqual({ cents: 300 });
    expect(parseAmount("3.5")).toEqual({ cents: 350 });
    expect(parseAmount("0.07")).toEqual({ cents: 7 });
    expect(parseAmount("3.500")).toEqual({ cents: 350 });
  });

  it("rejects things that are not a LeashPay code", () => {
    expect(() => parseQr("hello")).toThrow("That isn't a LeashPay code");
    expect(() => parseQr('{"a":1}')).toThrow("That isn't a LeashPay code");
    expect(parseQr(JSON.stringify(good)).voucher.serial).toBe(2);
  });
});
