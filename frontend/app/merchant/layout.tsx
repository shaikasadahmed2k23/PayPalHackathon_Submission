import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Sell",
  description: "Accept offline LeashPay payments and sync them later.",
};

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
