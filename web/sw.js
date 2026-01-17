const CACHE_NAME = "openradio-v1";
const PRECACHE = [
  "/",
  "/index.html",
  "/styles.css",
  "/app.js",
  "/manifest.webmanifest",
  "/icon.svg"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE))
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
          return null;
        })
      )
    )
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.url.includes("/hls/")) {
    return;
  }

  event.respondWith(
    caches.match(request).then((cached) => cached || fetch(request))
  );
});
