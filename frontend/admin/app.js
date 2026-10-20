/* Endura Human · base compartida del panel.
   Sesión, menú lateral, llamadas a la API, formato de cifras, modales, avisos y confirmaciones.
   Cada página declara <body data-page="..."> y llama a Endura.start(fn) con su propia lógica. */
(function () {
  const ICONS = {
    inventory: '<path d="M3 7.5 12 3l9 4.5v9L12 21l-9-4.5z"/><path d="M3 7.5 12 12l9-4.5M12 12v9"/>',
    sales: '<path d="M3 12V4a1 1 0 0 1 1-1h8l9 9-9 9z"/><circle cx="7.5" cy="7.5" r="1.5"/>',
    giveaways: '<path d="M12 21s-7-4.3-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 11c0 5.7-7 10-7 10z"/>',
    purchases: '<path d="M3 4h2l2.4 11.2a1 1 0 0 0 1 .8H18a1 1 0 0 0 1-.8L21 8H6.2"/><circle cx="9" cy="20" r="1.3"/><circle cx="17" cy="20" r="1.3"/>',
    expenses: '<rect x="3" y="6" width="18" height="13" rx="2"/><path d="M3 10h18M7 15h4"/>',
    results: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    partners: '<circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6"/><path d="M16 4.5a3 3 0 0 1 0 6M21 20c0-2.6-1.6-4.8-4-5.6"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  };
  const NAV = [
    { group: 'Operación', items: [
      ['inventory', 'Inventario', '/admin/'],
      ['sales', 'Ventas', '/admin/ventas.html'],
      ['giveaways', 'Patrocinios', '/admin/patrocinios.html'],
      ['purchases', 'Compras', '/admin/compras.html'],
    ] },
    { group: 'Negocio', items: [
      ['expenses', 'Gastos', '/admin/gastos.html'],
      ['results', 'Resultados', '/admin/resultados.html'],
      ['partners', 'Socios', '/admin/socios.html'],
      ['settings', 'Ajustes', '/admin/ajustes.html', 'admin'],
    ] },
  ];

  const $ = id => document.getElementById(id);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const moneyFmt = new Intl.NumberFormat('es-CO', { style: 'currency', currency: 'COP', maximumFractionDigits: 0 });
  const money = v => moneyFmt.format(Math.round(Number(v || 0)));
  const fx = (v, cur) => cur && cur !== 'COP'
    ? new Intl.NumberFormat('es-CO', { style: 'currency', currency: cur, maximumFractionDigits: 2 }).format(Number(v || 0))
    : money(v);
  const int = v => new Intl.NumberFormat('es-CO', { maximumFractionDigits: 0 }).format(Number(v || 0));
  const pct = v => (v === null || v === undefined) ? '—' : `${Number(v).toLocaleString('es-CO', { maximumFractionDigits: 1 })}%`;
  const pad = n => String(n).padStart(2, '0');
  const today = () => { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
  const thisMonth = () => today().slice(0, 7);
  const parseDate = s => { const [y, m, d] = String(s).slice(0, 10).split('-'); return new Date(+y, +m - 1, +(d || 1)); };
  const fmtDate = (s, opts) => s ? parseDate(s).toLocaleDateString('es-CO', opts || { day: 'numeric', month: 'short', year: 'numeric' }) : '—';
  const fmtShort = s => s ? parseDate(s).toLocaleDateString('es-CO', { day: 'numeric', month: 'short' }) : '—';
  const fmtMonth = s => s ? parseDate(s).toLocaleDateString('es-CO', { month: 'short', year: 'numeric' }) : '—';
  const fmtMonthLong = s => { const t = parseDate(s + '-01').toLocaleDateString('es-CO', { month: 'long', year: 'numeric' }); return t.charAt(0).toUpperCase() + t.slice(1); };
  /* Un vencimiento impreso como mes/año vale hasta el último día de ese mes. */
  const monthEnd = v => { if (!v) return null; const [y, m] = v.split('-').map(Number); return `${y}-${pad(m)}-${pad(new Date(y, m, 0).getDate())}`; };
  const daysUntil = s => Math.round((parseDate(s) - parseDate(today())) / 86400000);
  const newRef = () => { try { return crypto.randomUUID(); } catch (_) { return 'r-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10); } };

  async function api(path, opt = {}) {
    const h = new Headers(opt.headers || {});
    if (opt.body && !(opt.body instanceof FormData)) h.set('Content-Type', 'application/json');
    const r = await fetch('/api' + path, { ...opt, headers: h, credentials: 'same-origin' });
    let d = {};
    try { d = await r.json(); } catch (_) { /* respuesta sin cuerpo */ }
    if (r.status === 401 && !opt.noRedirect) { location.href = '/admin/login.html?next=' + encodeURIComponent(location.pathname + location.search); throw Error('Sesión vencida'); }
    if (!r.ok) {
      let m = d.detail;
      if (Array.isArray(m)) m = 'Revisa los datos: ' + m.map(x => (x.loc || []).slice(-1)[0]).filter(Boolean).join(', ');
      throw Error(m || 'No se pudo completar la operación');
    }
    return d;
  }
  const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body ?? {}) });
  const patch = (path, body) => api(path, { method: 'PATCH', body: JSON.stringify(body) });
  const put = (path, body) => api(path, { method: 'PUT', body: JSON.stringify(body) });
  const del = path => api(path, { method: 'DELETE' });

  let toastTimer;
  function toast(msg) {
    let t = $('toast');
    if (!t) { t = document.createElement('div'); t.id = 'toast'; t.className = 'toast hidden'; t.setAttribute('role', 'status'); document.body.appendChild(t); }
    t.textContent = msg; t.classList.remove('hidden');
    clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.add('hidden'), 4500);
  }

  function openModal(id, focus) { $(id).classList.remove('hidden'); if (focus) setTimeout(() => $(focus)?.focus(), 30); }
  function closeModal(id) { $(id).classList.add('hidden'); }
  document.addEventListener('click', e => { const b = e.target.closest('[data-close]'); if (b) closeModal(b.dataset.close); });
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    const open = [...document.querySelectorAll('.modalbg:not(.hidden)')];
    if (open.length) open[open.length - 1].classList.add('hidden');
  });

  /* Confirmación propia, sin alert()/confirm() del navegador. Con withReason pide un motivo. */
  function confirmBox({ title, text, ok = 'Confirmar', danger = false, withReason = false, placeholder = '' }) {
    return new Promise(resolve => {
      let bg = $('confirmBg');
      if (!bg) {
        bg = document.createElement('div'); bg.id = 'confirmBg'; bg.className = 'modalbg hidden';
        bg.innerHTML = `<section class="modal narrow" role="dialog" aria-modal="true" aria-labelledby="cfTitle"><div class="modalhead"><div><div class="eyebrow">Confirmar</div><h2 id="cfTitle"></h2></div></div>
          <form id="cfForm" class="modalbody" novalidate><p id="cfText" class="desc"></p><div id="cfReasonWrap" class="field" style="margin-top:14px"><label for="cfReason">Motivo *</label><input id="cfReason" maxlength="500"></div>
          <div id="cfError" class="error"></div><div class="modalactions"><button type="button" class="quiet" id="cfCancel">Cancelar</button><button type="submit" id="cfOk" class="primary"></button></div></form></section>`;
        document.body.appendChild(bg);
      }
      $('cfTitle').textContent = title; $('cfText').textContent = text || '';
      $('cfOk').textContent = ok; $('cfOk').className = danger ? 'danger-btn' : 'primary';
      $('cfReasonWrap').classList.toggle('hidden', !withReason); $('cfReason').value = ''; $('cfReason').placeholder = placeholder; $('cfError').textContent = '';
      const done = v => { bg.classList.add('hidden'); $('cfForm').onsubmit = null; $('cfCancel').onclick = null; resolve(v); };
      $('cfCancel').onclick = () => done(false);
      $('cfForm').onsubmit = e => {
        e.preventDefault();
        if (withReason) { const r = $('cfReason').value.trim(); if (r.length < 3) { $('cfError').textContent = 'Escribe el motivo.'; return; } done(r); } else done(true);
      };
      bg.classList.remove('hidden'); setTimeout(() => (withReason ? $('cfReason') : $('cfOk')).focus(), 30);
    });
  }

  function renderShell(me) {
    const page = document.body.dataset.page;
    const links = NAV.map(g => {
      const items = g.items.filter(i => i[3] !== 'admin' || me.role === 'admin');
      if (!items.length) return '';
      return `<div><div class="navlabel">${g.group}</div><nav class="nav">${items.map(([key, label, href]) =>
        `<a class="navlink${key === page ? ' active' : ''}" href="${href}" title="${label}"${key === page ? ' aria-current="page"' : ''}><span class="ico"><svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[key]}</svg></span><span class="navtext">${label}</span></a>`).join('')}</nav></div>`;
    }).join('');
    const aside = document.createElement('aside');
    aside.className = 'side';
    aside.innerHTML = `<a class="brand" href="/admin/" aria-label="Endura Human · inicio"><img class="brand-full" src="/assets/endura-logo.png" alt="Endura Human"><img class="brand-icon" src="/assets/endura-icon.png" alt=""></a>
      <div class="tagline">Suplementación de resistencia</div>
      <div class="navwrap">${links}</div>
      <div class="bottom"><div class="user"><div class="avatar">${esc((me.username[0] || 'E').toUpperCase())}</div><div><div class="uname">${esc(me.username)}</div><div class="role">${me.role === 'admin' ? 'Administrador' : 'Equipo'}</div></div></div><button id="logoutBtn" class="logout" type="button">Cerrar sesión</button></div>`;
    document.querySelector('.app').prepend(aside);
    $('logoutBtn').addEventListener('click', async () => { try { await api('/auth/logout', { method: 'POST' }); } finally { location.href = '/admin/login.html'; } });
  }

  let catalogsCache = null;
  async function catalogs() { if (!catalogsCache) catalogsCache = await api('/catalogs'); return catalogsCache; }

  async function start(fn) {
    try {
      const me = await api('/auth/me');
      Endura.me = me;
      renderShell(me);
      document.body.classList.remove('booting');
      await fn(me);
    } catch (e) {
      document.body.classList.remove('booting');
      if (e.message !== 'Sesión vencida') toast(e.message);
    }
  }

  /* Opciones de <select> a partir de un objeto {valor: etiqueta}. */
  const options = (obj, selected, first) => (first ? `<option value="">${esc(first)}</option>` : '') +
    Object.entries(obj).map(([k, v]) => `<option value="${esc(k)}"${String(k) === String(selected ?? '') ? ' selected' : ''}>${esc(v)}</option>`).join('');

  /* Estado de vencimiento de una fecha (mes de vencimiento). */
  function expiryPill(dateStr, warnDays = 90) {
    if (!dateStr) return '<span class="muted">Sin fecha</span>';
    const d = daysUntil(dateStr);
    const label = fmtMonth(dateStr);
    if (d < 0) return `<span class="pill bad">Vencido · ${label}</span>`;
    if (d <= warnDays) return `<span class="pill warn">${label} · ${d} días</span>`;
    return `<span class="num">${label}</span>`;
  }

  const debounce = (fn, ms = 250) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

  window.Endura = { $, esc, money, fx, int, pct, today, thisMonth, fmtDate, fmtShort, fmtMonth, fmtMonthLong, daysUntil, monthEnd, newRef,
    api, post, patch, put, del, toast, openModal, closeModal, confirm: confirmBox, start, catalogs, options, expiryPill, debounce, me: null };
})();
