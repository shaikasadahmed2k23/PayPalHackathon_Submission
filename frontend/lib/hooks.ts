"use client";

import { useSyncExternalStore } from "react";
import { getWaking, subscribeWaking } from "@/lib/api";

function subscribe(cb: () => void): () => void {
  window.addEventListener("online", cb);
  window.addEventListener("offline", cb);
  return () => {
    window.removeEventListener("online", cb);
    window.removeEventListener("offline", cb);
  };
}

/** navigator.onLine, reactive. Server render assumes online to avoid a flash of OFFLINE. */
export function useOnline(): boolean {
  return useSyncExternalStore(subscribe, () => navigator.onLine, () => true);
}

/** True while API calls are waiting on a sleeping (free-tier) server. */
export function useWaking(): boolean {
  return useSyncExternalStore(subscribeWaking, getWaking, () => false);
}
