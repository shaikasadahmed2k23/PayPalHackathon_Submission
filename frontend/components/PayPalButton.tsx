"use client";

/**
 * Gold "Approve with PayPal" button in PayPal's standard button style (gold pill, official wordmark).
 * The wordmark is PayPal's own hosted asset, unmodified; LeashPay's own UI never uses it elsewhere.
 */
export function PayPalButton({ label = "Approve with", busy, disabled, onClick }: { label?: string; busy?: boolean; disabled?: boolean; onClick: () => void }) {
  return (
    <button type="button" className="pp-btn" disabled={disabled || busy} onClick={onClick} aria-label={`${label} PayPal`}>
      {busy ? (
        <span>Opening PayPal…</span>
      ) : (
        <>
          <span>{label}</span>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="https://www.paypalobjects.com/paypal-ui/logos/svg/paypal-color.svg" alt="PayPal" height={24} style={{ height: 24, width: "auto" }} />
        </>
      )}
    </button>
  );
}
