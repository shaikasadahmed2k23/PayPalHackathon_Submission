import withSerwistInit from "@serwist/next";
import type { NextConfig } from "next";

const withSerwist = withSerwistInit({
  swSrc: "app/sw.ts",
  swDest: "public/sw.js",
  // We reload once ourselves when a new worker takes over (components/SwUpdater.tsx).
  reloadOnOnline: false,
  // The service worker only runs in production builds (`npm run build`, then serve `out/`).
  disable: process.env.NODE_ENV === "development",
});

// Static export: everything is client-side (IndexedDB + API calls), so Render can host it as a free
// static site. trailingSlash makes /wallet -> /wallet/index.html.
const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
};

export default withSerwist(nextConfig);
