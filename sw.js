/* Vibe — service worker: keeps the app itself (page, Tailwind, fonts, artwork)
   on the phone so Downloaded songs can be opened with no internet.
   Audio / API requests are never touched here. */
const VER = 'vibe-shell-v1';
const IMG = 'vibe-img-v1';
const TAILWIND = 'https://cdn.tailwindcss.com/';

self.addEventListener('install', e => {
  e.waitUntil((async () => {
    const c = await caches.open(VER);
    await Promise.all([
      c.add(new Request('./index.html', { cache: 'reload' })).catch(() => {}),
      c.add(new Request('./', { cache: 'reload' })).catch(() => {}),
      fetch(TAILWIND, { mode: 'no-cors' }).then(r => c.put(TAILWIND, r)).catch(() => {})
    ]);
    self.skipWaiting();
  })());
});

self.addEventListener('activate', e => {
  e.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter(k => k !== VER && k !== IMG).map(k => caches.delete(k)));
    await self.clients.claim();
  })());
});

const okToStore = r => r && (r.ok || r.type === 'opaque');

async function trim(name, max){
  const c = await caches.open(name);
  const keys = await c.keys();
  for (let i = 0; i < keys.length - max; i++) await c.delete(keys[i]);
}

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);

  // The page itself: network first (so updates arrive), cached copy when offline / slow.
  if (req.mode === 'navigate'){
    e.respondWith((async () => {
      const cache = await caches.open(VER);
      try {
        const net = await Promise.race([
          fetch(req),
          new Promise((_, rej) => setTimeout(() => rej(new Error('slow')), 4500))
        ]);
        if (okToStore(net)) cache.put('./index.html', net.clone());
        return net;
      } catch(err){
        return (await cache.match('./index.html')) || (await cache.match('./')) || Response.error();
      }
    })());
    return;
  }

  // Artwork: cache first, fill the cache as pictures are seen.
  if (req.destination === 'image'){
    e.respondWith((async () => {
      const cache = await caches.open(IMG);
      const hit = await cache.match(req);
      if (hit) return hit;
      try {
        const net = await fetch(req);
        if (okToStore(net)){ cache.put(req, net.clone()); trim(IMG, 300); }
        return net;
      } catch(err){ return Response.error(); }
    })());
    return;
  }

  // App files: same-origin files, Tailwind, Google Fonts — stale-while-revalidate.
  const isShell = url.origin === self.location.origin
    || url.hostname === 'cdn.tailwindcss.com'
    || url.hostname === 'fonts.googleapis.com'
    || url.hostname === 'fonts.gstatic.com';
  if (!isShell) return;
  e.respondWith((async () => {
    const cache = await caches.open(VER);
    const hit = await cache.match(req);
    const net = fetch(req).then(r => { if (okToStore(r)) cache.put(req, r.clone()); return r; }).catch(() => null);
    return hit || (await net) || Response.error();
  })());
});
