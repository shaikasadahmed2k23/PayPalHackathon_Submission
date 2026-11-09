"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { currentWallet } from "@/lib/store";

function Card({ title, sub, onClick, href, primary = false }: { title: string; sub: string; onClick?: () => void; href?: string; primary?: boolean }) {
  const cls = `btn !min-h-24 !justify-between !rounded-3xl !px-7 text-left ${primary ? "btn-primary" : ""}`;
  const body = (
    <>
      <span>
        <span className="block text-2xl">{title}</span>
        <span className={`block text-sm font-medium ${primary ? "opacity-70" : "text-muted"}`}>{sub}</span>
      </span>
      <span className="text-3xl">→</span>
    </>
  );
  return href ? (
    <Link href={href} className={cls}>
      {body}
    </Link>
  ) : (
    <button onClick={onClick} className={cls}>
      {body}
    </button>
  );
}

export default function Home() {
  const router = useRouter();

  async function paying() {
    const w = await currentWallet().catch(() => undefined);
    router.push(w ? "/wallet" : "/setup");
  }

  return (
    <main className="mx-auto flex min-h-[calc(100dvh-3.5rem)] w-full max-w-md flex-col justify-between px-6 pb-10 pt-6">
      <section className="py-8">
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
        <Card primary title="Pay" sub="Show my code · set up an allowance" onClick={paying} />
        <Card title="Sell" sub="Scan a code · take a payment" href="/merchant" />
        <Card title="Ask the agent" sub="Shop with words, rules enforced" href="/agent" />
        <Link href="/dashboard" className="mt-3 text-center text-sm text-muted underline-offset-4 hover:underline">
          Dashboard · audit log &amp; ledger
        </Link>
      </section>
    </main>
  );
}
