import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Wallet",
  description: "Your allowance, Show-to-Pay code and activity.",
};

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
