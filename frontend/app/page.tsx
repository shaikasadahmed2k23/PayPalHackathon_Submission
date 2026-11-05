"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { OfflineBadge } from "@/components/Shell";
import { currentWallet } from "@/lib/store";

export default function Home() {
  const router = useRouter();

  async function paying() {
    const w = await currentWallet().catch(() => undefined);
    router.push(w ? "/wallet" : "/setup");
  }

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-md flex-col justify-between px-6 pb-10 pt-6">
      <header className="flex items-center justify-between">
        <span className="flex items-center gap-2 text-lg font-bold tracking-tight">
          <span className="inline-block h-3 w-3 rounded-full bg-accent" />
          LeashPay
        </span>
        <OfflineBadge />
      </header>

      <section className="py-10">
        <h1 className="text-[3.4rem] font-extrabold leading-[0.95] tracking-tighter">
          Your money,
          <br />
          <span className="text-accent">on a leash.</span>
        </h1>
        <p className="mt-5 max-w-xs text-muted">
          Write your spending rules in plain English. An AI agent enforces them, online and even with zero internet.
        </p>
      </section>

      <section className="flex flex-col gap-3">
        <button onClick={paying} className="btn btn-primary !min-h-24 !justify-between !rounded-3xl !px-7 text-left">
          <span>
            <span className="block text-2xl">I&apos;m paying</span>
            <span className="block text-sm font-medium opacity-70">Show my code · set up an allowance</span>
          </span>
          <span className="text-3xl">→</span>
        </button>
        <Link href="/merchant" className="btn !min-h-24 !justify-between !rounded-3xl !px-7">
          <span>
            <span className="block text-2xl">I&apos;m selling</span>
            <span className="block text-sm font-medium text-muted">Scan a code · take a payment</span>
          </span>
          <span className="text-3xl">→</span>
        </Link>
        <div className="mt-3 flex justify-center gap-6 text-sm text-muted">
          <Link href="/agent" className="underline-offset-4 hover:underline">
            Shopping agent
          </Link>
          <Link href="/dashboard" className="underline-offset-4 hover:underline">
            Dashboard
          </Link>
        </div>
      </section>
    </main>
  );
}
