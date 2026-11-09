import type { Metadata } from "next";

export const metadata: Metadata = {
  title: { absolute: "Connecting PayPal · LeashPay" },
  description: "Finishing your PayPal approval.",
};

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
