import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Set rules",
  description: "Write your spending rules in plain English and hold money on PayPal.",
};

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
