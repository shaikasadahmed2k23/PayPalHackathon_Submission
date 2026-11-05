import { defaultCache } from "@serwist/next/worker";
import { NetworkFirst, Serwist, type PrecacheEntry, type SerwistGlobalConfig } from "serwist";

declare global {
  interface WorkerGlobalScope extends SerwistGlobalConfig {
    __SW_MANIFEST: (PrecacheEntry | string)[] | undefined;
  }
}
declare const self: ServiceWorkerGlobalScope;

const serwist = new Serwist({
  precacheEntries: self.__SW_MANIFEST,
  // /merchant?dev=1 must hit the precached /merchant page.
  precacheOptions: { ignoreURLParametersMatching: [/^dev$/, /^utm_/] },
  skipWaiting: true,
  clientsClaim: true,
  navigationPreload: true,
  runtimeCaching: [
    {
      // Backend GETs (cross-origin): network first, last good answer when offline.
      matcher: ({ request, url }) => request.method === "GET" && url.origin !== self.location.origin,
      handler: new NetworkFirst({ cacheName: "leashpay-api", networkTimeoutSeconds: 4 }),
    },
    ...defaultCache,
  ],
});

serwist.addEventListeners();
