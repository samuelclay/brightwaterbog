// Service worker: lets the installed app open offline.
//
// - The page and all recipe data are precached on install (every recipe's text works
//   offline). They're fetched network-first, so a deploy shows up on the next launch
//   whenever there's a connection; after 4 seconds without an answer the cached copy wins.
// - Photos are cached as they're viewed and kept across deploys (files never change in
//   place). Google Fonts are cached the same way.
//
// Add any new data file the page loads to SHELL_FILES, and bump VERSION when this file's
// caching logic changes.
const VERSION = 1;
const SHELL = `shell-v${VERSION}`;
const PHOTOS = 'photos-v1';
const FONTS = 'fonts-v1';
const SHELL_FILES = [
  '/',
  'data/recipes.js', 'data/nyt_recipes.js', 'data/nyt_sam_imported_recipes.js',
  'data/nyt_brittany_recipes.js', 'data/nyt_brittany_imported_recipes.js',
  'data/smitten_kitchen_recipes.js', 'data/cookie_and_kate_recipes.js', 'data/diet.js',
  'assets/nyt-collections.js', 'assets/tag-groups.js',
  'assets/favicons/2-carrot-on-aubergine.svg', 'assets/app-icons/icon-192.png', 'manifest.webmanifest',
];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(SHELL)
    .then(cache => cache.addAll(SHELL_FILES.map(url => new Request(url, {cache: 'reload'}))))
    .then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
  event.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(key => key.startsWith('shell-') && key !== SHELL).map(key => caches.delete(key))))
    .then(() => self.clients.claim()));
});

// Network first, refreshing the cache. The cached copy answers when the network fails,
// returns an error status, or stalls; with nothing cached, the network's answer stands.
function networkFirst(request, key) {
  return caches.open(SHELL).then(cache => {
    const network = fetch(request).then(response => {
      if (!response.ok) throw response;
      cache.put(key, response.clone());
      return response;
    });
    const fallback = failure => cache.match(key).then(cached =>
      cached || (failure instanceof Response ? failure : Promise.reject(failure)));
    const stalled = new Promise(resolve => setTimeout(resolve, 4000)).then(() => cache.match(key));
    return Promise.race([network.catch(fallback), stalled.then(cached => cached || network.catch(fallback))]);
  });
}

function cacheFirst(request, name) {
  return caches.open(name).then(cache => cache.match(request).then(cached => cached ||
    fetch(request).then(response => {
      if (response.ok || response.type === 'opaque') cache.put(request, response.clone());
      return response;
    })));
}

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin === location.origin) {
    // Every page (/, /r/<id>-<slug>) is the same single-page app.
    if (request.mode === 'navigate') event.respondWith(networkFirst(request, '/'));
    else if (url.pathname.startsWith('/images/')) event.respondWith(cacheFirst(request, PHOTOS));
    else event.respondWith(networkFirst(request, url.pathname.slice(1)));
  } else if (url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com') {
    event.respondWith(cacheFirst(request, FONTS));
  }
});
