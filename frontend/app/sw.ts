import { defaultCache } from "@serwist/next/worker";
import { NetworkFirst, Serwist, type PrecacheEntry, type RouteHandlerCallbackOptions, type SerwistGlobalConfig } from "serwist";

declare global {
  interface WorkerGlobalScope extends SerwistGlobalConfig {
    __SW_MANIFEST: (PrecacheEntry | string)[] | undefined;
  }
}
declare const self: ServiceWorkerGlobalScope;

// HTML is NOT precached (precache is cache-first and would serve a stale build). Pages are fetched
// network-first and kept in this cache; the list below is warmed on install so airplane mode works
// after one online visit. Hashed JS/CSS chunks come from the precache manifest (immutable).
const PAGES_CACHE = "leashpay-pages";
const OFFLINE_PAGES = ["/", "/wallet/", "/merchant/", "/setup/", "/setup/return/", "/agent/", "/dashboard/"];

const pages = new NetworkFirst({
  cacheName: PAGES_CACHE,
  networkTimeoutSeconds: 3, // online but flaky: fall back to the cached page after 3 s
  matchOptions: { ignoreSearch: true }, // /merchant/?dev=1 and /setup/return/?allowance_id=.. share one page
});

/** Static hosting serves /wallet/index.html: always ask for the slash form so the cache key matches. */
function navigate({ request, event }: RouteHandlerCallbackOptions): Promise<Response> {
  const url = new URL(request.url);
  if (!url.pathname.endsWith("/")) url.pathname += "/";
  return pages.handle({ request: new Request(url.toString()), event: event as ExtendableEvent });
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(PAGES_CACHE).then((cache) =>
      Promise.all(OFFLINE_PAGES.map(async (path) => {
        try {
          await cache.put(path, await fetch(path, { cache: "reload" })); // fresh from the network, never the HTTP cache
        } catch {
          /* offline during install: the page is cached on its first online visit instead */
        }
      })),
    ),
  );
});

const serwist = new Serwist({
  precacheEntries: self.__SW_MANIFEST,
  skipWaiting: true,
  clientsClaim: true,
  navigationPreload: false,
  runtimeCaching: [
    {
      matcher: ({ request, url }) => request.mode === "navigate" && url.origin === self.location.origin,
      handler: navigate,
    },
    {
      // Backend GETs (cross-origin): network first, last good answer when offline.
      matcher: ({ request, url }) => request.method === "GET" && url.origin !== self.location.origin,
      handler: new NetworkFirst({ cacheName: "leashpay-api", networkTimeoutSeconds: 4 }),
    },
    ...defaultCache,
  ],
});

serwist.addEventListeners();
