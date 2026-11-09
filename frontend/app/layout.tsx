import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import AppNav from "@/components/AppNav";
import SwUpdater from "@/components/SwUpdater";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: { default: "LeashPay — Your money, on a leash", template: "%s · LeashPay" },
  description: "Set spending rules in plain English; an AI agent enforces them on a PayPal hold, online and offline.",
  manifest: "/manifest.webmanifest",
  appleWebApp: { capable: true, title: "LeashPay", statusBarStyle: "black-translucent" },
  // favicon.ico comes from app/favicon.ico (16/32/48); these are the larger sizes. ?v= busts caches of the old icons.
  icons: {
    icon: [
      { url: "/icon-192.png?v=2", sizes: "192x192", type: "image/png" },
      { url: "/icon-512.png?v=2", sizes: "512x512", type: "image/png" },
    ],
    apple: "/apple-touch-icon.png?v=2",
  },
};

export const viewport: Viewport = {
  themeColor: "#f5f7fa",
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body>
        <AppNav />
        {/* room for the fixed bottom tab bar on phones */}
        <div className="pb-[calc(4rem+env(safe-area-inset-bottom))] sm:pb-0">{children}</div>
        <SwUpdater />
      </body>
    </html>
  );
}
