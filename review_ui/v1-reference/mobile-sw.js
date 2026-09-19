const CACHE_NAME = 'tw-national-exam-mobile-review-v4';
const SHELL = [
  '/mobile/',
  '/mobile/manifest.webmanifest',
  '/mobile/icon.png'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.pathname.startsWith('/api/')) return;

  if (request.mode === 'navigate' && url.pathname.startsWith('/mobile')) {
    event.respondWith(
      fetch(request)
        .then(response => {
          const copy = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put('/mobile/', copy));
          return response;
        })
        .catch(() => caches.match('/mobile/'))
    );
    return;
  }

  if (url.pathname.startsWith('/mobile/')) {
    event.respondWith(
      caches.match(request).then(cached => cached || fetch(request))
    );
  }
});
