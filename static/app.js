// TaskFlow - small helpers for the pages (no frameworks needed)
(function () {
  const csrf = document.querySelector('meta[name=csrf-token]')?.content;

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
  document.querySelectorAll('.toast').forEach(t => {
    t.querySelector('.x').onclick = () => t.remove();
    setTimeout(() => t.remove(), 4500);
  });
  function toast(text, type) {
    let box = document.querySelector('.toasts');
    if (!box) { box = document.createElement('div'); box.className = 'toasts'; document.body.appendChild(box); }
    const t = document.createElement('div');
    t.className = 'toast ' + type; t.textContent = text;
    box.appendChild(t); setTimeout(() => t.remove(), 3500);
  }

  // ----- confirm before dangerous actions
  document.querySelectorAll('form[data-confirm]').forEach(f =>
    f.addEventListener('submit', e => { if (!confirm(f.dataset.confirm)) e.preventDefault(); }));

  // ----- show / hide password
  document.querySelectorAll('.show-pw').forEach(b => b.addEventListener('click', () => {
    const input = b.previousElementSibling;
    input.type = input.type === 'password' ? 'text' : 'password';
    b.textContent = input.type === 'password' ? 'Show' : 'Hide';
  }));

  // ----- fill demo accounts on the login page
  document.querySelectorAll('.fill-demo').forEach(b => b.addEventListener('click', () => {
    document.querySelector('input[name=email]').value = b.dataset.email;
    document.querySelector('input[name=password]').value = b.dataset.pw;
  }));

  // ----- dialogs (new / edit project, add team member)
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
      if (action.value === 'delete' && !confirm('Delete the selected tasks? This cannot be undone.')) e.preventDefault();
    });
  }

  // ----- edit own comment in place
  document.querySelectorAll('.c-edit-btn').forEach(b => b.addEventListener('click', () => {
    const bubble = b.closest('.bubble'), form = bubble.querySelector('.c-edit');
    form.hidden = false; bubble.querySelector(':scope > p').hidden = true; form.querySelector('textarea').focus();
    form.querySelector('.c-cancel').onclick = () => { form.hidden = true; bubble.querySelector(':scope > p').hidden = false; };
  }));

  // ----- checklist: tick without reloading the page
  document.querySelectorAll('.chk-toggle').forEach(f => f.addEventListener('submit', async e => {
    e.preventDefault();
    const r = await fetch(f.action, { method: 'POST', body: new FormData(f), headers: { 'X-Requested-With': 'fetch' } });
    if (!r.ok) { f.submit(); return; }
    const d = await r.json(), li = f.closest('li'), box = f.querySelector('.box');
    li.classList.toggle('done');
    box.innerHTML = li.classList.contains('done') ? '<svg class="i"><use href="#i-tick"/></svg>' : '';
    const h = document.getElementById('checklist'), bar = document.querySelector('.chk-bar i');
    if (h) h.textContent = `Checklist · ${d.done}/${d.total}`;
    if (bar) bar.style.width = (d.total ? d.done / d.total * 100 : 0) + '%';
  }));

  document.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', () => b.closest('dialog').close()));

  // ----- Kanban drag and drop
  const board = document.querySelector('.board');
  if (!board) return;
  let dragged = null, from = null;

  board.addEventListener('dragstart', e => {
    const card = e.target.closest('.kcard');
    if (!card || card.draggable === false || card.getAttribute('draggable') === 'false') return;
    dragged = card; from = card.parentElement;
    card.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', card.dataset.id);
  });
  board.addEventListener('dragend', () => {
    dragged?.classList.remove('dragging');
    board.querySelectorAll('.col').forEach(c => c.classList.remove('drag-over'));
  });
  board.querySelectorAll('.col').forEach(col => {
    col.addEventListener('dragover', e => {
      if (!dragged) return;
      e.preventDefault();
      col.classList.add('drag-over');
      const body = col.querySelector('.col-body');
      const after = [...body.querySelectorAll('.kcard:not(.dragging)')]
        .find(c => e.clientY < c.getBoundingClientRect().top + c.offsetHeight / 2);
      body.insertBefore(dragged, after || body.querySelector('.col-empty'));
    });
    col.addEventListener('dragleave', e => { if (!col.contains(e.relatedTarget)) col.classList.remove('drag-over'); });
    col.addEventListener('drop', async e => {
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
        if (!r.ok || !data.ok) throw new Error(data.error || 'Could not move the task.');
        toast(`Moved to “${data.label}”`, 'success');
        card.querySelector('.due')?.classList.toggle('overdue', false);
      } catch (err) {
        oldBody.insertBefore(card, oldBody.querySelector('.col-empty'));
        updateCounts();
        toast(err.message, 'error');
      }
    });
  });
  function updateCounts() {
    board.querySelectorAll('.col').forEach(c => c.querySelector('.count').textContent = c.querySelectorAll('.kcard').length);
  }
})();
