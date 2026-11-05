import { openDB, type DBSchema, type IDBPDatabase } from "idb";
import type { Certificate, OfflinePayment, QrPayload } from "./types";

export interface WalletRecord {
  allowance_id: number;
  certificate: Certificate;
  cert_signature: string;
  server_pubkey: string;
  /** Serial the NEXT freshly issued code will get. Serials are never reused. */
  next_serial: number;
  /** The code currently on screen: stays identical until the user taps "Next code". */
  current: Pick<QrPayload, "voucher" | "device_signature"> | null;
}

export type QueuedPayment = OfflinePayment & { qid?: number };

interface LeashDB extends DBSchema {
  device: { key: number; value: Uint8Array }; // allowance_id -> private key (never leaves the device)
  wallet: { key: number; value: WalletRecord };
  queue: { key: number; value: QueuedPayment };
  cache: { key: string; value: unknown };
}

let dbPromise: Promise<IDBPDatabase<LeashDB>> | null = null;

function db(): Promise<IDBPDatabase<LeashDB>> {
  dbPromise ??= openDB<LeashDB>("leashpay", 1, {
    upgrade(d) {
      d.createObjectStore("device");
      d.createObjectStore("wallet", { keyPath: "allowance_id" });
      d.createObjectStore("queue", { keyPath: "qid", autoIncrement: true });
      d.createObjectStore("cache");
    },
  });
  return dbPromise;
}

// ---- device key
export const putDeviceKey = async (id: number, key: Uint8Array) => void (await (await db()).put("device", key, id));
export const getDeviceKey = async (id: number) => (await db()).get("device", id);

// ---- wallet (buyer)
export const putWallet = async (w: WalletRecord) => void (await (await db()).put("wallet", w));
export const getWallet = async (id: number) => (await db()).get("wallet", id);
export async function currentWallet(): Promise<WalletRecord | undefined> {
  const id = await getCache<number>("current_allowance_id");
  return id === undefined ? undefined : getWallet(id);
}
export const setCurrentAllowance = (id: number) => setCache("current_allowance_id", id);

// ---- merchant queue
export const enqueue = async (p: OfflinePayment) => void (await (await db()).add("queue", p));
export const queued = async (): Promise<QueuedPayment[]> => (await db()).getAll("queue");
export async function removeQueued(qids: number[]) {
  const tx = (await db()).transaction("queue", "readwrite");
  await Promise.all(qids.map((q) => tx.store.delete(q)));
  await tx.done;
}

// ---- cache (merchants, server pubkey, last wallet snapshot, ...)
export const setCache = async (key: string, value: unknown) => void (await (await db()).put("cache", value, key));
export async function getCache<T>(key: string): Promise<T | undefined> {
  return (await (await db()).get("cache", key)) as T | undefined;
}
