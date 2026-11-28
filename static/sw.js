// TaskFlow service worker: Web Push notifications, and (version 4) a page to show when there is no internet.
const CACHE = 'taskflow-v4';
const OFFLINE = '/offline';
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll([OFFLINE, '/static/style.css', '/static/img/logo.png'])).catch(() => {})
    .then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => e.waitUntil(
  caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())
));

// Pages always come from the network (they are private and change all the time). Only when the network is
// down the offline page is shown. Nothing personal is ever stored in the cache.
self.addEventListener('fetch', e => {
  if (e.request.mode !== 'navigate' || e.request.method !== 'GET') return;
  e.respondWith(fetch(e.request).catch(() => caches.match(OFFLINE)));
});

self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = { title: 'TaskFlow', body: e.data ? e.data.text() : '' }; }
  e.waitUntil(self.registration.showNotification(d.title || 'TaskFlow', {
    body: d.body || '', icon: '/brand/logo.png', badge: '/static/img/icon-192.png',
    data: { url: d.url || '/' }, tag: d.url || 'taskflow'
  }));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = new URL((e.notification.data && e.notification.data.url) || '/', self.location.origin).href;
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
    for (const c of list) { if (c.url === url && 'focus' in c) return c.focus(); }
    return self.clients.openWindow(url);
  }));
});
