// Service worker só para notificações: o Chrome no Android não aceita `new Notification()`.
// Sem cache nem interceptação de pedidos.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

// Web Push do servidor (push.py): payload {title, body, tag, url, critico, renotify?}. O iOS exige uma notificação por cada push,
// por isso um payload inválido mostra uma genérica. url «./#<id>» → data.task (o clique abaixo abre essa execução)
self.addEventListener("push", (e) => {
  let p = null;
  try { p = e.data?.json(); } catch {}
  if (!p || typeof p.title !== "string") p = { title: "Squad Office", body: "Abre o Office para ver o detalhe." };
  const url = typeof p.url === "string" ? p.url : "";
  let task;
  try { task = url.startsWith("./#") && url.length > 3 ? decodeURIComponent(url.slice(3)) : undefined; } catch {}
  e.waitUntil(self.registration.showNotification(p.title, { body: typeof p.body === "string" ? p.body : "",
    tag: typeof p.tag === "string" ? p.tag : undefined, data: { task }, icon: "icon-192.png", requireInteraction: p.critico === true,
    renotify: p.renotify === true && typeof p.tag === "string" && p.tag !== "" })); // renotify sem tag: o Chrome rejeita
});

// clique na notificação: foca o separador aberto (e mostra essa execução) ou abre um novo
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const task = e.notification.data?.task;
  e.waitUntil((async () => {
    const all = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const win = all.find((c) => new URL(c.url).origin === self.location.origin);
    if (win) {
      win.postMessage({ select: task });
      return win.focus();
    }
    return self.clients.openWindow(task ? `./#${encodeURIComponent(task)}` : "./");
  })());
});
