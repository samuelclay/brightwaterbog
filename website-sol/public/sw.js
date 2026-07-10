const VERSION = "bwb-shell-v1";
const SHELL = ["/", "/favicon.svg", "/manifest.webmanifest"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(VERSION).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== VERSION).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

const shouldCacheMedia = (url) =>
  url.pathname.startsWith("/media/") &&
  !url.pathname.endsWith(".mp4") &&
  !/-(?:1[3-9]\d{2}|[2-9]\d{3})\.(?:webp|avif)$/.test(url.pathname);

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;

  if (event.request.mode === "navigate") {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          const copy = response.clone();
          caches.open(VERSION).then((cache) => cache.put(event.request, copy));
          return response;
        })
        .catch(async () => (await caches.match(event.request)) || (await caches.match("/"))),
    );
    return;
  }

  if (["style", "script", "font"].includes(event.request.destination) || shouldCacheMedia(url)) {
    event.respondWith(
      caches.match(event.request).then((cached) => {
        const fresh = fetch(event.request)
          .then((response) => {
            if (response.ok) caches.open(VERSION).then((cache) => cache.put(event.request, response.clone()));
            return response;
          })
          .catch(() => cached);
        return cached || fresh;
      }),
    );
  }
});
