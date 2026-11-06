"use client";

import { useEffect, useState } from "react";

const GUARD_KEY = "leashpay_sw_reload_at";
const GUARD_MS = 30_000; // never reload twice within 30 s: no reload loops

/** When a new service worker takes over a page that an older one controlled, show a toast and reload once. */
export default function SwUpdater() {
  const [updated, setUpdated] = useState(false);

  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    const sw = navigator.serviceWorker;
    let hadController = !!sw.controller; // false on the very first visit: that install is not an update

    const onChange = () => {
      if (!hadController) {
        hadController = true; // first install claiming the page
        return;
      }
      const last = Number(sessionStorage.getItem(GUARD_KEY) ?? 0);
      if (Date.now() - last < GUARD_MS) return;
      sessionStorage.setItem(GUARD_KEY, String(Date.now()));
      setUpdated(true);
      setTimeout(() => window.location.reload(), 1200);
    };
    sw.addEventListener("controllerchange", onChange);

    // Look for a new build on load and whenever the tab comes back to the front.
    const check = () => void sw.getRegistration().then((r) => r?.update()).catch(() => undefined);
    const onVisible = () => document.visibilityState === "visible" && navigator.onLine && check();
    check();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      sw.removeEventListener("controllerchange", onChange);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  if (!updated) return null;
  return (
    <div role="status" className="fixed inset-x-0 bottom-4 z-50 mx-auto w-fit rounded-full bg-text px-5 py-3 text-sm font-semibold text-white shadow-lg">
      LeashPay updated — reloading
    </div>
  );
}
