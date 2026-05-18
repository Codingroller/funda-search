self.addEventListener('push', function (event) {
  var d = {};
  try { d = event.data ? event.data.json() : {}; } catch (_) {}
  var title = d.title || 'Funda Search';
  var opts = {
    body: d.body || '',
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/badge-72.png',
    image: d.image || undefined,
    data: { url: d.url || '/' },
    tag: d.tag || undefined,
    renotify: !!d.tag,
  };
  event.waitUntil(self.registration.showNotification(title, opts));
});

self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  var target = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (wins) {
      for (var i = 0; i < wins.length; i++) {
        var w = wins[i];
        if ('focus' in w) {
          try { w.navigate(target); } catch (_) {}
          return w.focus();
        }
      }
      return clients.openWindow(target);
    })
  );
});

self.addEventListener('pushsubscriptionchange', function (event) {
  event.waitUntil(
    fetch('/push/vapid-public-key')
      .then(function (r) { return r.text(); })
      .then(function (keyText) {
        var pad = '='.repeat((4 - keyText.length % 4) % 4);
        var raw = atob((keyText.trim() + pad).replace(/-/g, '+').replace(/_/g, '/'));
        var key = new Uint8Array(raw.length);
        for (var i = 0; i < raw.length; i++) key[i] = raw.charCodeAt(i);
        return self.registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: key,
        });
      })
      .then(function (sub) {
        var j = sub.toJSON();
        return fetch('/push/subscribe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ endpoint: j.endpoint, p256dh: j.keys.p256dh, auth: j.keys.auth }),
        });
      })
  );
});
