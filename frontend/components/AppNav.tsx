"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { OfflineBadge } from "@/components/Shell";

const LINKS = [
  { href: "/wallet", label: "Wallet", tab: "Wallet" },
  { href: "/merchant", label: "Sell", tab: "Sell" },
  { href: "/agent", label: "AI agent", tab: "Agent" },
  { href: "/dashboard", label: "Dashboard", tab: "Dashboard" },
] as const;

const ICONS: Record<string, React.ReactNode> = {
  "/wallet": <path d="M3 7a2 2 0 0 1 2-2h12v3M3 7v10a2 2 0 0 0 2 2h14a1 1 0 0 0 1-1v-9a1 1 0 0 0-1-1H5a2 2 0 0 1-2-2Zm13 6h2" />,
  "/merchant": <path d="M4 9l1.5-5h13L20 9M4 9v10h16V9M4 9h16M9 19v-5h6v5" />,
  "/agent": <path d="M5 6h14a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-7l-5 4v-4H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2Zm4 5h.01M12 11h.01M15 11h.01" />,
  "/dashboard": <path d="M4 20V10m6 10V4m6 16v-7m4 7H3" />,
};

function active(path: string, href: string): boolean {
  return path.replace(/\/$/, "") === href;
}

function Logo() {
  return (
    <Link href="/" className="flex items-center gap-2 text-lg font-bold tracking-tight text-text">
      <Image src="/logo-mark.png" alt="" width={28} height={28} className="h-7 w-7" priority />
      LeashPay
    </Link>
  );
}

/** Slim top bar everywhere; on phones the links move to a fixed bottom tab bar. The full-screen
 *  Show-to-Pay view is z-50, so it covers both bars. */
export default function AppNav() {
  const path = usePathname() ?? "/";
  return (
    <>
      <header className="sticky top-0 z-40 border-b border-line bg-ink/90 backdrop-blur">
        <div className="mx-auto flex h-14 w-full max-w-6xl items-center justify-between gap-4 px-5">
          <Logo />
          <nav aria-label="Main" className="hidden items-center gap-1 sm:flex">
            {LINKS.map((l) => (
              <Link
                key={l.href}
                href={l.href}
                aria-current={active(path, l.href) ? "page" : undefined}
                className={`rounded-full px-3.5 py-1.5 text-sm font-semibold ${active(path, l.href) ? "bg-accent/10 text-accent" : "text-muted hover:text-text"}`}
              >
                {l.label}
              </Link>
            ))}
          </nav>
          <div className="flex items-center gap-3">
            <OfflineBadge />
            <Link href="/setup" className="btn btn-primary btn-sm hidden sm:inline-flex">New allowance</Link>
          </div>
        </div>
      </header>

      <nav
        aria-label="Main"
        className="fixed inset-x-0 bottom-0 z-40 grid grid-cols-4 border-t border-line bg-ink/95 pb-[env(safe-area-inset-bottom)] backdrop-blur sm:hidden"
      >
        {LINKS.map((l) => (
          <Link
            key={l.href}
            href={l.href}
            aria-current={active(path, l.href) ? "page" : undefined}
            className={`flex flex-col items-center gap-0.5 py-2 text-[11px] font-semibold ${active(path, l.href) ? "text-accent" : "text-muted"}`}
          >
            <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              {ICONS[l.href]}
            </svg>
            {l.tab}
          </Link>
        ))}
      </nav>
    </>
  );
}
