// TaskFlow 4 - dark mode, quick-action box (Ctrl+K), keyboard shortcuts, automation form
(function () {
  const csrf = document.querySelector('meta[name=csrf-token]')?.content;
  const body = document.body;

  // ----- theme: auto (follows the device) -> light -> dark
  const themeBtn = document.getElementById('themeBtn');
  themeBtn?.addEventListener('click', async () => {
    const order = ['auto', 'light', 'dark'];
    const next = order[(order.indexOf(body.dataset.theme || 'auto') + 1) % order.length];
    body.dataset.theme = next;
    const tr = s => (window.TF && TF.t ? TF.t(s) : s);
    themeBtn.title = tr({ auto: 'Theme: like the device', light: 'Theme: light', dark: 'Theme: dark' }[next]);
    try {
      await fetch(themeBtn.dataset.url, { method: 'POST', credentials: 'same-origin',
        headers: { 'X-CSRF-Token': csrf, 'Content-Type': 'application/x-www-form-urlencoded' },
        body: 'theme=' + next + '&csrf_token=' + encodeURIComponent(csrf) });
    } catch (e) { /* the choice still works on this page */ }
  });

  // ----- quick-action box
  const dlg = document.getElementById('palette');
  const input = document.getElementById('paletteInput');
  const list = document.getElementById('paletteList');
  let items = [], active = 0;
  function collect() {
    const seen = new Set(), out = [];
    const links = [...document.querySelectorAll('.sidebar .nav a, .sidebar .ws-menu a')]
      .concat([...(document.getElementById('paletteExtra')?.content.querySelectorAll('a') || [])]);
    links.forEach(a => {
      const text = a.textContent.replace(/\s+/g, ' ').trim(), href = a.getAttribute('href');
      if (!text || !href || seen.has(href)) return;
      seen.add(href); out.push({ text, href });
    });
    return out;
  }
  function draw() {
    const q = input.value.trim().toLowerCase();
    const shown = items.filter(i => !q || i.text.toLowerCase().includes(q)).slice(0, 12);
    if (q) shown.push({ text: '🔎 ' + input.value.trim(), href: '/search?q=' + encodeURIComponent(input.value.trim()) });
    active = Math.min(active, shown.length - 1);
    list.innerHTML = '';
    shown.forEach((i, n) => {
      const li = document.createElement('li');
      li.setAttribute('role', 'option');
      li.className = n === active ? 'on' : '';
      li.textContent = i.text;
      li.addEventListener('click', () => { location.href = i.href; });
      list.appendChild(li);
    });
    list.shown = shown;
  }
  function openPalette() {
    if (!dlg || dlg.open) return;
    items = collect(); active = 0; input.value = '';
    draw();
    dlg.showModal ? dlg.showModal() : dlg.setAttribute('open', '');
    input.focus();
  }
  document.getElementById('paletteBtn')?.addEventListener('click', openPalette);
  input?.addEventListener('input', () => { active = 0; draw(); });
  input?.addEventListener('keydown', e => {
    const shown = list.shown || [];
    if (e.key === 'ArrowDown') { active = Math.min(shown.length - 1, active + 1); draw(); e.preventDefault(); }
    else if (e.key === 'ArrowUp') { active = Math.max(0, active - 1); draw(); e.preventDefault(); }
    else if (e.key === 'Enter' && shown[active]) { location.href = shown[active].href; e.preventDefault(); }
  });
  dlg?.addEventListener('click', e => { if (e.target === dlg) dlg.close(); });

  // ----- keyboard shortcuts (not while typing)
  let lastG = 0;
  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openPalette(); return; }
    const el = e.target;
    if (e.ctrlKey || e.metaKey || e.altKey || el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || dlg?.open) return;
    const go = path => { if (document.querySelector('.sidebar')) location.href = path; };
    if (e.key === 'g') { lastG = Date.now(); return; }
    if (Date.now() - lastG < 1200) {
      const to = { b: '/board', t: '/tasks', d: '/', c: '/calendar', r: '/reports', s: '/sprints' }[e.key];
      lastG = 0;
      if (to) { e.preventDefault(); go(to); }
      return;
    }
    if (e.key === 'n') { e.preventDefault(); go('/tasks/new'); }
    else if (e.key === '?') { e.preventDefault(); openPalette(); }
  });

  // ----- automation rule form: show only the box that the chosen action needs
  const rule = document.getElementById('ruleForm');
  if (rule) {
    const trig = rule.querySelector('[name=trigger_kind]'), act = rule.querySelector('[name=action_kind]');
    const hidden = document.getElementById('actionValue');
    const sync = () => {
      rule.querySelectorAll('[data-show-when]').forEach(l => { l.hidden = l.dataset.showWhen !== trig.value; });
      rule.querySelectorAll('[data-pick]').forEach(l => { l.hidden = !l.dataset.pick.split(' ').includes(act.value); });
    };
    trig.addEventListener('change', sync); act.addEventListener('change', sync); sync();
    rule.addEventListener('submit', () => {
      const box = [...rule.querySelectorAll('[data-for]')].find(x => x.dataset.for.split(' ').includes(act.value));
      hidden.value = box ? box.value : '';
    });
  }
})();
