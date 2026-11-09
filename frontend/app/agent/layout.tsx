import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "AI agent",
  description: "Shop with words; the policy judge enforces your rules.",
};

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
