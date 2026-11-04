// TaskFlow - small helpers for the pages (no frameworks needed)
(function () {
  const csrf = document.querySelector('meta[name=csrf-token]')?.content;
  const TF = window.TF = window.TF || {};
  TF.t = s => (TF.strings && TF.strings[s]) || s;
  const t = TF.t;
  let ignoreTaskEventsUntil = 0;

  // ----- QR codes (uses the small qrcode-generator library when the page loads it)
  TF.qr = (box, cell) => {
    if (!box) return;
    try {
      const q = qrcode(0, 'M'); q.addData(box.dataset.uri); q.make();
      box.innerHTML = q.createSvgTag(cell || 4, 8);
    } catch (e) { box.textContent = ''; }
  };

  // ----- mobile sidebar
  const side = document.getElementById('sidebar'), overlay = document.getElementById('overlay');
  document.getElementById('menuBtn')?.addEventListener('click', () => { side.classList.add('open'); overlay.classList.add('show'); });
  overlay?.addEventListener('click', () => { side.classList.remove('open'); overlay.classList.remove('show'); });

  // ----- "/" focuses the search box
  document.addEventListener('keydown', e => {
    if (e.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) {
      const s = document.querySelector('.search input');
      if (s) { e.preventDefault(); s.focus(); }
    }
  });

  // ----- toasts close by themselves
  document.querySelectorAll('.toast').forEach(el => {
    el.querySelector('.x').onclick = () => el.remove();
    setTimeout(() => el.remove(), 5000);
  });
  function toast(text, type, action) {
    let box = document.getElementById('liveToasts') || document.querySelector('.toasts');
    if (!box) { box = document.createElement('div'); box.className = 'toasts'; document.body.appendChild(box); }
    const el = document.createElement('div');
    el.className = 'toast ' + (type || 'info');
    el.appendChild(document.createTextNode(text));
    if (action) {
      const b = document.createElement('button');
      b.className = 'toast-act'; b.type = 'button'; b.textContent = action.label; b.onclick = action.run;
      el.appendChild(b);
    }
    const x = document.createElement('button'); x.className = 'x'; x.textContent = '×'; x.onclick = () => el.remove();
    el.appendChild(x);
    box.appendChild(el);
    setTimeout(() => el.remove(), action ? 12000 : 4500);
  }
  TF.toast = toast;

  // ----- confirm before dangerous actions
  document.querySelectorAll('form[data-confirm]').forEach(f =>
    f.addEventListener('submit', e => { if (!confirm(f.dataset.confirm)) e.preventDefault(); }));

  // ----- show / hide password
  document.querySelectorAll('.show-pw').forEach(b => b.addEventListener('click', () => {
    const input = b.previousElementSibling;
    input.type = input.type === 'password' ? 'text' : 'password';
  }));

  // ----- fill demo accounts on the login page
  document.querySelectorAll('.fill-demo').forEach(b => b.addEventListener('click', () => {
    document.querySelector('input[name=email]').value = b.dataset.email;
    document.querySelector('input[name=password]').value = b.dataset.pw;
  }));

  // ----- copy buttons
  function copied(btn) {
    const old = btn.innerHTML;
    btn.textContent = t('Copied!');
    setTimeout(() => { btn.innerHTML = old; }, 1600);
  }
  document.querySelectorAll('[data-copy]').forEach(b => b.addEventListener('click', () => {
    const input = document.querySelector(b.dataset.copy);
    input.select();
    (navigator.clipboard ? navigator.clipboard.writeText(input.value) : Promise.resolve(document.execCommand('copy')))
      .then(() => copied(b));
  }));
  document.querySelectorAll('[data-copy-text]').forEach(b => b.addEventListener('click', () => {
    navigator.clipboard?.writeText(b.dataset.copyText).then(() => copied(b));
  }));

  // ----- dialogs (new / edit project, add team member, new resume)
  document.querySelectorAll('[data-open]').forEach(btn => btn.addEventListener('click', () => {
    const dlg = document.getElementById(btn.dataset.open);
    const form = dlg.querySelector('form');
    if (btn.dataset.id) {
      form.id.value = btn.dataset.id;
      form.name.value = btn.dataset.name;
      form.description.value = btn.dataset.description;
      const c = form.querySelector(`input[name=color][value="${btn.dataset.color}"]`);
      if (c) c.checked = true;
    } else if ('reset' in btn.dataset) {
      form.reset(); if (form.id) form.id.value = '';
    }
    dlg.showModal();
  }));
  document.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', () => b.closest('dialog').close()));
  // ----- delete project dialog
  document.querySelectorAll('[data-delete-id]').forEach(btn => btn.addEventListener('click', () => {
    const dlg = document.getElementById('deleteProject'), form = dlg.querySelector('form');
    form.action = form.dataset.base + btn.dataset.deleteId + '/delete';
    dlg.querySelector('.dp-name').textContent = btn.dataset.deleteName;
    dlg.querySelector('.dp-total').textContent = btn.dataset.deleteTotal;
  }));

  // ----- bulk actions on the task list (admin)
  const bulk = document.getElementById('bulkForm');
  if (bulk) {
    const checks = [...document.querySelectorAll('.row-check')], all = document.getElementById('checkAll');
    const action = document.getElementById('bulkAction');
    const refresh = () => {
      const n = checks.filter(c => c.checked).length;
      document.getElementById('bulkCount').textContent = n;
      bulk.hidden = n === 0;
      all.checked = n > 0 && n === checks.length; all.indeterminate = n > 0 && n < checks.length;
      checks.forEach(c => c.closest('tr').classList.toggle('selected', c.checked));
    };
    checks.forEach(c => c.addEventListener('change', refresh));
    all.addEventListener('change', () => { checks.forEach(c => c.checked = all.checked); refresh(); });
    document.getElementById('bulkClear').onclick = () => { checks.forEach(c => c.checked = false); refresh(); };
    action.addEventListener('change', () => bulk.querySelectorAll('select[data-for]').forEach(s => {
      s.hidden = s.dataset.for !== action.value; s.disabled = s.hidden;
    }));
    action.dispatchEvent(new Event('change'));
    bulk.addEventListener('submit', e => {
      if (action.value === 'delete' && !confirm(t('Are you sure?'))) e.preventDefault();
    });
  }

  // ----- edit own comment in place
  document.querySelectorAll('.c-edit-btn').forEach(b => b.addEventListener('click', () => {
    const bubble = b.closest('.bubble'), form = bubble.querySelector('.c-edit');
    form.hidden = false; bubble.querySelector(':scope > p').hidden = true; form.querySelector('textarea').focus();
    form.querySelector('.c-cancel').onclick = () => { form.hidden = true; bubble.querySelector(':scope > p').hidden = false; };
  }));

  // ----- @mention buttons under the comment box
  document.querySelectorAll('.mention').forEach(b => b.addEventListener('click', () => {
    const ta = document.querySelector('.comment-form textarea');
    ta.value = (ta.value && !ta.value.endsWith(' ') ? ta.value + ' ' : ta.value) + b.dataset.name + ' ';
    ta.focus();
  }));

  // ----- checklist: tick without reloading the page
  document.querySelectorAll('.chk-toggle').forEach(f => f.addEventListener('submit', async e => {
    e.preventDefault();
    const r = await fetch(f.action, { method: 'POST', body: new FormData(f), headers: { 'X-Requested-With': 'fetch' } });
    if (!r.ok) { f.submit(); return; }
    const d = await r.json(), li = f.closest('li'), box = f.querySelector('.box');
    li.classList.toggle('done');
    box.innerHTML = li.classList.contains('done') ? '<svg class="i"><use href="#i-tick"/></svg>' : '';
    const bar = document.querySelector('.chk-bar i');
    if (bar) bar.style.width = (d.total ? d.done / d.total * 100 : 0) + '%';
    ignoreTaskEventsUntil = Date.now() + 3000;
  }));

  // ----- files: drop on the box or choose -> upload at once
  const drop = document.getElementById('dropzone');
  if (drop) {
    const input = drop.querySelector('input[type=file]');
    input.addEventListener('change', () => { if (input.files.length) { drop.classList.add('busy'); drop.submit(); } });
    ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
    ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, () => drop.classList.remove('over')));
    drop.addEventListener('drop', e => {
      e.preventDefault();
      if (!e.dataTransfer.files.length) return;
      input.files = e.dataTransfer.files;
      drop.classList.add('busy');
      drop.submit();
    });
  }

  // ----- running timer clocks
  const serverOffset = TF.now ? Date.now() - Date.parse(TF.now.replace(' ', 'T')) : 0;
  const clocks = [...document.querySelectorAll('.clock[data-since]')];
  if (clocks.length) {
    const tick = () => clocks.forEach(c => {
      const s = Math.max(0, Math.floor((Date.now() - serverOffset - Date.parse(c.dataset.since.replace(' ', 'T'))) / 1000));
      const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, sec = s % 60;
      c.textContent = (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(sec).padStart(2, '0');
    });
    tick(); setInterval(tick, 1000);
  }

  // ----- Kanban drag and drop (events are delegated, so the board can be refreshed live)
  const board = document.getElementById('board');
  let dragged = null, from = null;
  function updateCounts() {
    board.querySelectorAll('.col').forEach(c => c.querySelector('.count').textContent = c.querySelectorAll('.kcard').length);
  }
  if (board) {
    board.addEventListener('dragstart', e => {
      const card = e.target.closest('.kcard');
      if (!card || card.getAttribute('draggable') === 'false') return;
      dragged = card; from = card.parentElement;
      card.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', card.dataset.id);
    });
    board.addEventListener('dragend', () => {
      dragged?.classList.remove('dragging');
      board.querySelectorAll('.col').forEach(c => c.classList.remove('drag-over'));
    });
    board.addEventListener('dragover', e => {
      const col = e.target.closest('.col');
      if (!dragged || !col) return;
      e.preventDefault();
      board.querySelectorAll('.col').forEach(c => c.classList.toggle('drag-over', c === col));
      const body = col.querySelector('.col-body');
      const after = [...body.querySelectorAll('.kcard:not(.dragging)')]
        .find(c => e.clientY < c.getBoundingClientRect().top + c.offsetHeight / 2);
      body.insertBefore(dragged, after || body.querySelector('.col-empty'));
    });
    board.addEventListener('drop', async e => {
      const col = e.target.closest('.col');
      if (!col) return;
      e.preventDefault();
      col.classList.remove('drag-over');
      if (!dragged) return;
      const card = dragged, oldBody = from, status = col.dataset.status;
      dragged = null;
      if (oldBody === card.parentElement && oldBody.closest('.col').dataset.status === status) return;
      updateCounts();
      try {
        const r = await fetch(`/api/tasks/${card.dataset.id}/status`, {
          method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
          body: JSON.stringify({ status })
        });
        const data = await r.json();
        if (!r.ok || !data.ok) throw new Error(data.error || t('Could not move the task.'));
        toast('✓ ' + data.label, 'success');
      } catch (err) {
        oldBody.insertBefore(card, oldBody.querySelector('.col-empty'));
        updateCounts();
        toast(err.message || t('Could not move the task.'), 'error');
      }
    });
  }

  // ----- live updates: WebSocket, or asking the server every few seconds when WebSocket is not possible
  const body = document.body, liveUrl = body.dataset.live;
  if (!liveUrl) return;
  const dot = document.getElementById('liveDot');
  const taskBox = document.querySelector('[data-live-task]');
  let last = null, pollTimer = null, socket = null, boardTimer = null;

  async function refreshBoard() {
    if (!board || dragged) { clearTimeout(boardTimer); boardTimer = setTimeout(refreshBoard, 1500); return; }
    try {
      const html = await (await fetch(location.href, { headers: { 'X-Requested-With': 'fetch' } })).text();
      const fresh = new DOMParser().parseFromString(html, 'text/html').getElementById('board');
      if (fresh && !dragged) { board.innerHTML = fresh.innerHTML; }
    } catch (e) { /* the next event tries again */ }
  }
  function setBell(n) {
    const b = document.getElementById('bellCount');
    if (!b || n === null || n === undefined) return;
    b.textContent = n; b.hidden = !n;
  }
  function handle(ev) {
    if (ev.id) last = Math.max(last || 0, ev.id);
    if (ev.kind === 'notification') {
      setBell(ev.unseen);
      toast('🔔 ' + ev.text, 'info', ev.task ? { label: '→', run: () => { location.href = '/tasks/' + ev.task; } } : null);
      return;
    }
    if (ev.me) return;
    if (board) { clearTimeout(boardTimer); boardTimer = setTimeout(refreshBoard, 400); return; }
    if (taskBox && ev.task && String(ev.task) === taskBox.dataset.liveTask) {
      if (Date.now() > ignoreTaskEventsUntil) toast(ev.text + ' — ' + t('This task was changed by someone else.'), 'info',
        { label: t('Refresh'), run: () => location.reload() });
      return;
    }
    toast(ev.text, 'info');
  }
  function setDot(state) { if (dot) dot.className = 'live-dot ' + state; }

  async function poll() {
    try {
      const r = await fetch(liveUrl + (last === null ? '' : '?after=' + last), { headers: { 'X-Requested-With': 'fetch' } });
      if (r.ok) {
        const d = await r.json();
        if (last === null) last = d.last;
        else { d.events.forEach(handle); last = d.last; setBell(d.unseen); }
        setDot('poll');
      }
    } catch (e) { setDot('off'); }
    pollTimer = setTimeout(poll, document.hidden ? 20000 : 6000);
  }
  function startPolling() { if (!pollTimer) poll(); }
  function stopPolling() { clearTimeout(pollTimer); pollTimer = null; }

  function connect() {
    if (body.dataset.ws !== '1' || !('WebSocket' in window)) { startPolling(); return; }
    try {
      socket = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws');
    } catch (e) { startPolling(); return; }
    let opened = false;
    socket.onopen = () => { opened = true; stopPolling(); setDot('on'); };
    socket.onmessage = m => {
      const d = JSON.parse(m.data);
      if (d.hello) { if (last === null) last = d.last; return; }
      if (d.ping) return;
      handle(d);
    };
    socket.onclose = () => {
      setDot('off');
      startPolling();                       // keep updates coming, and try WebSocket again a bit later
      setTimeout(() => { if (!socket || socket.readyState === WebSocket.CLOSED) connect(); }, opened ? 5000 : 30000);
    };
  }
  connect();
})();
