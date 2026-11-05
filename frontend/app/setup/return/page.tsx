/* eslint-disable react-hooks/set-state-in-effect -- these effects load data from IndexedDB / the API into state */
"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { ErrorNote, Shell } from "@/components/Shell";
import { api } from "@/lib/api";
import { generateDeviceKey } from "@/lib/keys";
import { getWallet, putWallet, setCurrentAllowance } from "@/lib/store";
import type { Certificate } from "@/lib/types";

interface AuthorizeOut {
  certificate: Certificate;
  signature: string;
  server_pubkey: string;
}

function Finish() {
  const router = useRouter();
  const params = useSearchParams();
  const started = useRef(false);
  const [error, setError] = useState("");
  const [step, setStep] = useState("Securing your allowance…");

  useEffect(() => {
    if (started.current) return; // authorize is not repeatable; React dev mode runs effects twice
    started.current = true;
    const id = Number(params.get("allowance_id"));
    if (!id) {
      setError("This page needs an allowance_id in the link.");
      return;
    }
    (async () => {
      try {
        if (await getWallet(id)) {
          await setCurrentAllowance(id); // page was refreshed after success
          router.replace("/wallet");
          return;
        }
        setStep("Creating this phone's secret key…");
        const device_pubkey_b64 = await generateDeviceKey(id); // private key stays in IndexedDB
        setStep("Confirming the hold with PayPal…");
        const out = await api<AuthorizeOut>(`/allowance/${id}/authorize`, { body: { device_pubkey_b64 } });
        await putWallet({
          allowance_id: id,
          certificate: out.certificate,
          cert_signature: out.signature,
          server_pubkey: out.server_pubkey,
          next_serial: 0,
          current: null,
        });
        await setCurrentAllowance(id);
        router.replace("/wallet");
      } catch (e) {
        setError(e instanceof Error ? e.message : "Something went wrong");
      }
    })();
  }, [params, router]);

  return (
    <Shell title="Almost there">
      {error ? (
        <>
          <ErrorNote>{error}</ErrorNote>
          <a href="/setup" className="btn">
            Back to setup
          </a>
        </>
      ) : (
        <div className="card flex items-center gap-4">
          <span className="h-3 w-3 animate-pulse rounded-full bg-accent" />
          <span>{step}</span>
        </div>
      )}
    </Shell>
  );
}

export default function SetupReturn() {
  return (
    <Suspense>
      <Finish />
    </Suspense>
  );
}
