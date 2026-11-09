"use client";

import { useOnline, useWaking } from "@/lib/hooks";

export function OfflineBadge() {
  const online = useOnline();
  if (online) return null;
  return (
    <span className="rounded-full border border-warn/50 bg-warn/10 px-3 py-1 text-xs font-bold tracking-widest text-warn">
      OFFLINE
    </span>
  );
}

export function WakingBanner() {
  const waking = useWaking();
  if (!waking) return null;
  return (
    <div role="status" className="rounded-2xl border border-info/30 bg-info/10 px-4 py-3 text-sm text-info">
      <p className="font-semibold">Waking up the server…</p>
      <p>Free tier: it sleeps when idle and takes ~30 s to start. Retrying automatically.</p>
    </div>
  );
}

export function Shell({ children, wide = false }: { title?: string; children: React.ReactNode; back?: boolean; wide?: boolean }) {
  return (
    <main className={`mx-auto flex min-h-[calc(100dvh-3.5rem)] w-full flex-col gap-5 px-5 pb-16 pt-5 ${wide ? "max-w-6xl" : "max-w-md"}`}>
      <WakingBanner />
      {children}
    </main>
  );
}

export function ErrorNote({ children }: { children: React.ReactNode }) {
  if (!children) return null;
  return <p className="rounded-2xl border border-bad/40 bg-bad/10 px-4 py-3 text-sm text-bad">{children}</p>;
}
