import * as ed from "@noble/ed25519";
import { canonicalBytes } from "./canonical";
import { getDeviceKey, putDeviceKey } from "./store";

// Async API (SubtleCrypto SHA-512): needs a secure context, i.e. https or http://localhost.

export const toB64 = (b: Uint8Array): string => {
  let s = "";
  b.forEach((x) => (s += String.fromCharCode(x)));
  return btoa(s);
};
export const fromB64 = (s: string): Uint8Array => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

/** Create a device keypair. The private key goes to IndexedDB only; the public key is returned to send. */
export async function generateDeviceKey(allowanceId: number): Promise<string> {
  const secret = ed.utils.randomSecretKey();
  const pub = await ed.getPublicKeyAsync(secret);
  await putDeviceKey(allowanceId, secret);
  return toB64(pub);
}

/** Sign any object over its canonical JSON (the voucher). Returns base64. */
export async function signObject(secret: Uint8Array, obj: unknown): Promise<string> {
  return toB64(await ed.signAsync(canonicalBytes(obj), secret));
}

export async function signWithDeviceKey(allowanceId: number, obj: unknown): Promise<string> {
  const secret = await getDeviceKey(allowanceId);
  if (!secret) throw new Error("This device has no key for that allowance");
  return signObject(secret, obj);
}

export async function verifyObject(pubkeyB64: string, obj: unknown, sigB64: string): Promise<boolean> {
  try {
    return await ed.verifyAsync(fromB64(sigB64), canonicalBytes(obj), fromB64(pubkeyB64));
  } catch {
    return false; // malformed base64 / wrong lengths
  }
}
