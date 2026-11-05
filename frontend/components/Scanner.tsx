"use client";

import { useEffect, useRef, useState } from "react";

/** Camera QR scanner. Calls onScan once with the decoded text, then stops the camera. */
export default function Scanner({ onScan, onCancel }: { onScan: (text: string) => void; onCancel: () => void }) {
  const [error, setError] = useState("");
  const done = useRef(false);
  const cb = useRef(onScan);
  useEffect(() => {
    cb.current = onScan; // the camera must not restart when the parent re-renders
  });

  useEffect(() => {
    let scanner: import("html5-qrcode").Html5Qrcode | null = null;
    let cancelled = false;
    (async () => {
      try {
        const { Html5Qrcode } = await import("html5-qrcode");
        scanner = new Html5Qrcode("qr-reader");
        await scanner.start(
          { facingMode: "environment" },
          { fps: 10, qrbox: (w, h) => ({ width: Math.min(w, h) * 0.8, height: Math.min(w, h) * 0.8 }) },
          (text) => {
            if (done.current) return;
            done.current = true;
            cb.current(text);
          },
          () => {},
        );
        if (cancelled) await scanner.stop().catch(() => {});
      } catch (e) {
        setError(
          e instanceof Error && e.message
            ? e.message
            : "Couldn't open the camera. Allow camera access (needs https or localhost).",
        );
      }
    })();
    return () => {
      cancelled = true;
      if (scanner?.isScanning) scanner.stop().catch(() => {});
    };
  }, []);

  return (
    <div className="space-y-3">
      <div id="qr-reader" className="overflow-hidden rounded-3xl border border-line bg-black" />
      {error && <p className="rounded-2xl border border-bad/40 bg-bad/10 px-4 py-3 text-sm text-bad">{error}</p>}
      <button className="btn w-full" onClick={onCancel}>Cancel</button>
    </div>
  );
}
