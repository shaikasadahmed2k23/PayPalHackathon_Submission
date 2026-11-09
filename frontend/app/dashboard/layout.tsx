import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Dashboard",
  description: "Audit log, vouchers and purchases.",
};

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
