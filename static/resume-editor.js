/* TaskFlow - resume editor.
   The form changes the data in `state`; after a short pause everything is saved (POST JSON)
   and the preview on the right (the real resume page in an iframe) is reloaded. */
(function () {
  'use strict';
  var root = document.getElementById('rz');
  if (!root) return;
  var DATA = JSON.parse(document.getElementById('rzData').textContent);
  var S = DATA.strings, SCHEMA = DATA.schema;
  var state = DATA.content;
  var settings = {};
  var statusEl = document.getElementById('rzStatus');
  var frame = document.getElementById('rzFrame');
  var csrf = document.querySelector('meta[name="csrf-token"]').content;
  var timer = null, seq = 0, lastOk = 0;

  function t(s) { return S[s] || s; }
  function el(tag, attrs, kids) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'text') e.textContent = attrs[k];
      else if (k === 'html') e.innerHTML = attrs[k];
      else if (k.slice(0, 2) === 'on') e.addEventListener(k.slice(2), attrs[k]);
      else e.setAttribute(k, attrs[k]);
    });
    (kids || []).forEach(function (c) { if (c) e.appendChild(c); });
    return e;
  }
  function icon(name) {
    var s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    s.setAttribute('class', 'i');
    var u = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    u.setAttribute('href', '#' + name);
    s.appendChild(u);
    return s;
  }
  function iconBtn(name, title, fn, extra) {
    var b = el('button', {type: 'button', 'class': 'icon-btn sm ' + (extra || ''), title: title, 'aria-label': title, onclick: fn});
    b.appendChild(icon(name));
    return b;
  }
  function move(arr, i, d) {
    var j = i + d;
    if (j < 0 || j >= arr.length) return false;
    var x = arr[i]; arr[i] = arr[j]; arr[j] = x;
    return true;
  }
  function uid() { return Math.random().toString(16).slice(2, 10); }

  // ------------------------------------------------------------ status and saving
  function setStatus(kind, text) {
    statusEl.className = 'rz-status ' + kind;
    statusEl.textContent = text;
  }
  function readSettings() {
    root.querySelectorAll('[data-set]').forEach(function (inp) {
      if (inp.type === 'radio') { if (inp.checked) settings[inp.dataset.set] = inp.value; }
      else settings[inp.dataset.set] = inp.value;
    });
  }
  function schedule(delay) {
    clearTimeout(timer);
    setStatus('busy', t('Saving…'));
    timer = setTimeout(save, delay === undefined ? 700 : delay);
  }
  function save() {
    readSettings();
    var my = ++seq;
    var payload = Object.assign({}, settings, {content: state});
    fetch(root.dataset.save, {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf, 'X-Requested-With': 'fetch'},
      body: JSON.stringify(payload)
    }).then(function (r) { return r.json().catch(function () { return {ok: false, error: t('Network problem. Please try again.')}; }); })
      .then(function (res) {
        if (my !== seq) return;                       // an older answer: a newer save is on its way
        if (!res.ok) { setStatus('err', t('Not saved:') + ' ' + res.error); return; }
        lastOk = my;
        setStatus('ok', '✓ ' + t('Saved') + ' ' + res.saved);
        if (res.slug) {
          root.dataset.preview = root.dataset.preview.replace(/\/r\/[^/?]+/, '/r/' + res.slug);
          root.dataset.public = '/r/' + res.slug;
          document.getElementById('rzOpen').href = '/r/' + res.slug;
          root.dataset.url = res.url;
        }
        reloadPreview();
      })
      .catch(function () { if (my === seq) setStatus('err', t('Not saved:') + ' ' + t('Network problem. Please try again.')); });
  }
  window.addEventListener('beforeunload', function (e) {
    if (seq !== lastOk && statusEl.classList.contains('busy')) { e.preventDefault(); e.returnValue = ''; }
  });

  // ------------------------------------------------------------ preview
  var scrollY = 0;
  function reloadPreview() {
    try { scrollY = frame.contentWindow.scrollY; } catch (e) { scrollY = 0; }
    frame.src = root.dataset.preview + (root.dataset.preview.indexOf('?') < 0 ? '?' : '&') + 't=' + Date.now();
  }
  frame.addEventListener('load', function () {
    try { frame.contentWindow.scrollTo(0, scrollY); } catch (e) { /* ignore */ }
    fitPreview();
  });
  function fitPreview() {
    var wrap = document.getElementById('rzFrameWrap');
    var w = wrap.clientWidth || 600;
    var scale = Math.min(1, w / 860);
    frame.style.transform = 'scale(' + scale + ')';
    frame.style.width = '860px';
    frame.style.height = Math.round((wrap.clientHeight || 900) / scale) + 'px';
  }
  window.addEventListener('resize', fitPreview);
  fitPreview();
  document.getElementById('rzPreview').addEventListener('click', function (e) {
    if (e.target === this) document.body.classList.remove('rz-show-preview');      // tap outside closes it (phones)
  });
  document.getElementById('rzShowPreview').addEventListener('click', function () {
    document.body.classList.toggle('rz-show-preview');
    fitPreview();
  });

  // ------------------------------------------------------------ settings and header fields
  root.addEventListener('input', function (e) {
    var x = e.target;
    if (x.dataset.c !== undefined) { state[x.dataset.c] = x.value; schedule(); }
    else if (x.dataset.set !== undefined) { schedule(x.dataset.set === 'slug' ? 1200 : 400); }
  });
  root.addEventListener('change', function (e) {
    if (e.target.dataset.set !== undefined && e.target.type !== 'text') schedule(200);
  });
  root.querySelectorAll('[data-accent]').forEach(function (b) {
    b.addEventListener('click', function () {
      root.querySelector('[data-set="accent"]').value = b.dataset.accent;
      schedule(200);
    });
  });
  document.getElementById('rzCopy').addEventListener('click', function () {
    var url = root.dataset.url || (location.origin + root.dataset.public);
    if (navigator.clipboard) navigator.clipboard.writeText(url);
    this.lastChild.textContent = ' ' + t('Copied!');
  });

  // ------------------------------------------------------------ links
  function renderLinks() {
    var box = document.getElementById('rzLinks');
    box.innerHTML = '';
    state.links = state.links || [];
    state.links.forEach(function (l, i) {
      var label = el('input', {value: l.label || '', maxlength: 40, placeholder: t('Label (e.g. GitHub)'),
        oninput: function () { l.label = this.value; schedule(); }});
      var url = el('input', {value: l.url || '', maxlength: 300, placeholder: 'https://',
        oninput: function () { l.url = this.value; schedule(); }});
      box.appendChild(el('div', {'class': 'rz-link'}, [label, url,
        iconBtn('i-trash', t('Remove entry'), function () { state.links.splice(i, 1); renderLinks(); schedule(); })]));
    });
    document.getElementById('rzAddLink').hidden = state.links.length >= 8;
  }
  document.getElementById('rzAddLink').addEventListener('click', function () {
    state.links.push({label: '', url: ''});
    renderLinks();
    var inputs = document.querySelectorAll('#rzLinks .rz-link:last-child input');
    if (inputs.length) inputs[0].focus();
  });

  // ------------------------------------------------------------ sections
  function fieldInput(item, f) {
    var key = f[0], label = f[1], kind = f[2], input;
    if (kind === 'lines') {
      input = el('textarea', {rows: 3, maxlength: 2500});
      input.value = item[key] || '';
    } else if (kind === 'level') {
      input = el('select');
      for (var n = 0; n <= 5; n++) {
        var o = el('option', {value: n, text: n === 0 ? '—' : '●'.repeat(n) + '○'.repeat(5 - n)});
        if (+item[key] === n) o.selected = true;
        input.appendChild(o);
      }
    } else {
      input = el('input', {maxlength: key === 'items' ? 400 : (kind === 'url' ? 300 : 200)});
      if (kind === 'url') input.placeholder = 'https://';
      input.value = item[key] || '';
    }
    var update = function () { item[key] = kind === 'level' ? +input.value : input.value; schedule(); };
    input.addEventListener('input', update);
    input.addEventListener('change', update);
    return el('label', {'class': 'rz-f rz-f-' + kind + ' rz-k-' + key}, [document.createTextNode(label), input]);
  }

  function renderSections() {
    var box = document.getElementById('rzSections');
    box.innerHTML = '';
    state.sections.forEach(function (s, si) {
      var sch = SCHEMA[s.type];
      var title = el('input', {'class': 'rz-sec-title', value: s.title, maxlength: 60, 'aria-label': t('Section title'),
        title: t('Default titles are translated into the language of the resume.'),
        oninput: function () { s.title = this.value; schedule(); }});
      var head = el('div', {'class': 'rz-sec-head'}, [
        el('span', {'class': 'rz-type', text: sch.title}), title,
        el('span', {'class': 'rz-tools'}, [
          iconBtn('i-up', t('Move up'), function () { if (move(state.sections, si, -1)) { renderSections(); schedule(300); } }),
          iconBtn('i-down', t('Move down'), function () { if (move(state.sections, si, 1)) { renderSections(); schedule(300); } }),
          iconBtn(s.visible ? 'i-eye' : 'i-eye-off', s.visible ? t('Hide section') : t('Show section'),
            function () { s.visible = !s.visible; renderSections(); schedule(200); }),
          iconBtn('i-trash', t('Remove section'), function () {
            if (!confirm(t('Remove this section?'))) return;
            state.sections.splice(si, 1); renderSections(); schedule(200);
          }, 'danger')])
      ]);
      var card = el('section', {'class': 'card rz-card rz-sec' + (s.visible ? '' : ' is-hidden'), 'data-id': s.id}, [head]);
      if (!s.visible) card.appendChild(el('p', {'class': 'muted small', text: t('Hidden')}));
      if (s.type === 'text') {
        var body = el('textarea', {rows: 4, maxlength: 3000, oninput: function () { s.body = this.value; schedule(); }});
        body.value = s.body || '';
        card.appendChild(el('label', {'class': 'rz-f'}, [document.createTextNode(t('Text')), body]));
      } else {
        s.items = s.items || [];
        var list = el('div', {'class': 'rz-items'});
        s.items.forEach(function (it, ii) {
          var fields = el('div', {'class': 'rz-fields rz-fields-' + s.type});
          sch.fields.forEach(function (f) { fields.appendChild(fieldInput(it, f)); });
          list.appendChild(el('div', {'class': 'rz-item'}, [
            el('div', {'class': 'rz-item-tools'}, [
              el('b', {text: '#' + (ii + 1)}),
              iconBtn('i-up', t('Move up'), function () { if (move(s.items, ii, -1)) { renderSections(); schedule(300); } }),
              iconBtn('i-down', t('Move down'), function () { if (move(s.items, ii, 1)) { renderSections(); schedule(300); } }),
              iconBtn('i-trash', t('Remove entry'), function () { s.items.splice(ii, 1); renderSections(); schedule(200); }, 'danger')]),
            fields]));
        });
        card.appendChild(list);
        if (s.items.length < 30) {
          var add = el('button', {type: 'button', 'class': 'btn btn-light sm', onclick: function () {
            var it = {};
            sch.fields.forEach(function (f) { it[f[0]] = f[2] === 'level' ? 0 : ''; });
            s.items.push(it);
            renderSections();
            var items = document.querySelectorAll('.rz-sec[data-id="' + s.id + '"] .rz-item');
            var first = items[items.length - 1].querySelector('input, textarea');
            if (first) first.focus();
          }});
          add.appendChild(icon('i-plus'));
          add.appendChild(document.createTextNode(' ' + t('Add entry')));
          card.appendChild(add);
        }
      }
      box.appendChild(card);
    });
  }
  root.querySelectorAll('[data-add-section]').forEach(function (b) {
    b.addEventListener('click', function () {
      if (state.sections.length >= 20) return;
      var kind = b.dataset.addSection;
      var s = {id: uid(), type: kind, title: SCHEMA[kind].default, visible: true, items: [], body: ''};
      if (kind !== 'text') {
        var it = {};
        SCHEMA[kind].fields.forEach(function (f) { it[f[0]] = f[2] === 'level' ? 0 : ''; });
        s.items.push(it);
      }
      state.sections.push(s);
      renderSections();
      var card = document.querySelector('.rz-sec[data-id="' + s.id + '"]');
      card.scrollIntoView({behavior: 'smooth', block: 'center'});
      var f = card.querySelector('.rz-item input, textarea');
      if (f) f.focus();
      schedule(300);
    });
  });

  // ------------------------------------------------------------ photo
  var photoBox = document.getElementById('rzPhoto');
  var removeBtn = document.getElementById('rzPhotoRemove');
  function sendPhoto(form) {
    form.append('csrf_token', csrf);
    setStatus('busy', t('Uploading…'));
    return fetch(root.dataset.photo, {method: 'POST', body: form, credentials: 'same-origin',
      headers: {'X-Requested-With': 'fetch', 'X-CSRF-Token': csrf}})
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (!res.ok) { setStatus('err', t('Not saved:') + ' ' + res.error); return false; }
        setStatus('ok', '✓ ' + t('Saved'));
        reloadPreview();
        return true;
      }).catch(function () { setStatus('err', t('Network problem. Please try again.')); return false; });
  }
  document.getElementById('rzPhotoInput').addEventListener('change', function () {
    var file = this.files[0];
    if (!file) return;
    var form = new FormData();
    form.append('photo', file);
    var input = this;
    sendPhoto(form).then(function (ok) {
      if (ok) {
        photoBox.innerHTML = '';
        photoBox.appendChild(el('img', {src: URL.createObjectURL(file), alt: ''}));
        removeBtn.hidden = false;
      }
      input.value = '';
    });
  });
  removeBtn.addEventListener('click', function () {
    var form = new FormData();
    form.append('remove', '1');
    sendPhoto(form).then(function (ok) {
      if (ok) { photoBox.innerHTML = ''; photoBox.appendChild(icon('i-users')); removeBtn.hidden = true; }
    });
  });

  renderLinks();
  renderSections();
  readSettings();
})();
