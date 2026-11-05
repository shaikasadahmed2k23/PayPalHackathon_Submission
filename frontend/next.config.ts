import withSerwistInit from "@serwist/next";
import type { NextConfig } from "next";

// Pages that must open in airplane mode after one online visit.
const OFFLINE_PAGES = ["/", "/wallet", "/merchant", "/setup"];
const revision = String(Date.now());

const withSerwist = withSerwistInit({
  swSrc: "app/sw.ts",
  swDest: "public/sw.js",
  additionalPrecacheEntries: OFFLINE_PAGES.map((url) => ({ url, revision })),
  // The service worker only runs in production builds (`npm run build && npm start`).
  disable: process.env.NODE_ENV === "development",
});

const nextConfig: NextConfig = {};

export default withSerwist(nextConfig);
