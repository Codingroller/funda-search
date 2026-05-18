// IndexedDB helpers for persisting the badge count across SW restarts
function _openDB() {
  return new Promise(function (resolve, reject) {
    var req = indexedDB.open('funda-badge', 1);
    req.onupgradeneeded = function () { req.result.createObjectStore('kv'); };
    req.onsuccess = function () { resolve(req.result); };
    req.onerror = function () { reject(req.error); };
  });
}
function _getCount(db) {
  return new Promise(function (resolve) {
    var req = db.transaction('kv', 'readonly').objectStore('kv').get('count');
    req.onsuccess = function () { resolve(req.result || 0); };
    req.onerror = function () { resolve(0); };
  });
}
function _setCount(db, n) {
  return new Promise(function (resolve) {
    var tx = db.transaction('kv', 'readwrite');
    tx.objectStore('kv').put(n, 'count');
    tx.oncomplete = resolve;
    tx.onerror = resolve;
  });
}

function _setBadge(n) {
  if ('setAppBadge' in navigator) {
    return navigator.setAppBadge(n).catch(function () {});
  }
  return Promise.resolve();
}
function _clearBadge(db) {
  return _setCount(db, 0).then(function () {
    if ('clearAppBadge' in navigator) {
      return navigator.clearAppBadge().catch(function () {});
    }
  });
}

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

  event.waitUntil(
    _openDB()
      .then(function (db) {
        return _getCount(db).then(function (current) {
          var total = current + (d.count || 1);
          // Chain both DB write AND badge update before showing the notification.
          // All three must complete inside event.waitUntil — iOS terminates the
          // SW as soon as showNotification resolves if anything is fire-and-forget.
          return _setCount(db, total)
            .then(function () { return _setBadge(total); })
            .then(function () { return self.registration.showNotification(title, opts); });
        });
      })
      .catch(function () {
        return self.registration.showNotification(title, opts);
      })
  );
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

// Page sends this message when it loads to clear the badge
self.addEventListener('message', function (event) {
  if (event.data && event.data.type === 'CLEAR_BADGE') {
    event.waitUntil(
      _openDB().then(function (db) { return _clearBadge(db); }).catch(function () {})
    );
  }
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
