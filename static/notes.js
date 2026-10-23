/* Notes on the Tasks page (docs/NOTES-DESIGN.md).

   The owner's saved notes: a list with a search box on one side and an editor on the other (one column
   on a phone). The Notes tab starts hidden and appears once GET /api/notes answers, so nothing broken
   shows before the dashboard restart that brings the routes.

   The top half is plain functions, so Node can test them. The bottom half draws the panel. A note's
   title and text go in with textContent or .value, never as HTML. */
(function (root) {
  'use strict';

  var LIMIT = 200;
  var SEARCH_DELAY = 250;
  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  // ── plain functions ────────────────────────────────────────────────────

  function rowTitle(n) { return (n && n.title) || 'Untitled'; }

  // "Today 14:05", "Yesterday 09:30", "12 Oct", "12 Oct 2025" (local time, by calendar day).
  function dateLabel(iso, now) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    var n = now instanceof Date ? now : new Date(now == null ? Date.now() : now);
    var day = function (x) { return new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime(); };
    var gap = Math.round((day(n) - day(d)) / 86400000);
    var hm = ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2);
    if (gap === 0) return 'Today ' + hm;
    if (gap === 1) return 'Yesterday ' + hm;
    var s = d.getDate() + ' ' + MONTHS[d.getMonth()];
    return d.getFullYear() === n.getFullYear() ? s : s + ' ' + d.getFullYear();
  }

  function listUrl(q, limit) {
    var u = '/api/notes?limit=' + (limit || LIMIT);
    q = String(q || '').trim();
    return q ? u + '&q=' + encodeURIComponent(q) : u;
  }

  // The editor differs from what is saved.
  function isDirty(saved, title, body) {
    return String((saved && saved.title) || '') !== String(title || '') || String((saved && saved.body) || '') !== String(body || '');
  }

  function sourceLabel(src) { return src === 'chat' ? 'Chat' : ''; }

  function countLabel(total, matched, q) {
    var one = function (n) { return n + (n === 1 ? ' note' : ' notes'); };
    if (!total) return '';
    return String(q || '').trim() ? matched + ' of ' + one(total) : one(total);
  }

  function emptyText(q) {
    return String(q || '').trim()
      ? 'No note has all of those words.'
      : 'No notes yet. Press New note, or save a chat reply with the bookmark button under it.';
  }

  function errorText(e) {
    var m = e && e.message;
    return typeof m === 'string' && m ? m : 'Something went wrong. Try again.';
  }

  var core = { rowTitle: rowTitle, dateLabel: dateLabel, listUrl: listUrl, isDirty: isDirty, sourceLabel: sourceLabel,
    countLabel: countLabel, emptyText: emptyText, errorText: errorText };

  if (typeof module !== 'undefined' && module.exports) module.exports = core;
  if (typeof document === 'undefined') return;

  // ── the panel ──────────────────────────────────────────────────────────

  var S = { ready: null, detecting: null, notes: [], total: 0, matched: 0, q: '', timer: null, lastLoad: 0,
    loading: null, loadingQ: null, saved: { id: null, title: '', body: '' }, busy: false };

  function $(id) { return document.getElementById(id); }
  function toast(msg) { if (root.CodecShell && root.CodecShell.toast) root.CodecShell.toast(msg); }

  function csrf(h) {
    var m = document.cookie.match(/(?:^|;\s*)codec_csrf=([^;]+)/);
    if (m) h['x-csrf-token'] = m[1];
    return h;
  }

  function api(method, url, data) {
    var o = { method: method, headers: csrf(data === undefined ? {} : { 'Content-Type': 'application/json' }), credentials: 'same-origin' };
    if (data !== undefined) o.body = JSON.stringify(data);
    return fetch(url, o).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) {
          var why = d && (d.error || d.detail);
          var e = new Error(typeof why === 'string' && why ? why : 'HTTP ' + r.status);
          e.status = r.status;
          throw e;
        }
        return d;
      });
    });
  }

  // The tab shows only when the routes answer. A failed try is not remembered, so the next visit tries again.
  function detect() {
    if (S.ready) return Promise.resolve(true);
    if (S.detecting) return S.detecting;
    S.detecting = api('GET', listUrl('', 1)).then(function () {
      S.ready = true;
      var t = $('tab-notes');
      if (t) t.hidden = false;
      return true;
    }, function () { return false; }).then(function (ok) { S.detecting = null; return ok; });
    return S.detecting;
  }

  function setStatus(text) { var s = $('notesStatus'); if (s) s.textContent = text || ''; }

  function dirty() {
    var t = $('notesTitle'), b = $('notesBody');
    return !!(t && b && !$('notesEdit').hidden && isDirty(S.saved, t.value.trim(), b.value));
  }

  // A phone raises the keyboard the moment a text box takes focus: only a new note should do that.
  function coarsePointer() { return !!(root.matchMedia && root.matchMedia('(pointer: coarse)').matches); }

  function ask(opts) {
    if (root.CodecShell && root.CodecShell.ask) return root.CodecShell.ask(opts);
    return Promise.resolve(root.confirm(opts.message));
  }

  // Leaving the editor with unsaved words asks first.
  function canLeave() {
    if (!dirty()) return Promise.resolve(true);
    return ask({ title: 'Discard your changes?', message: 'This note has changes that are not saved.', confirm: 'Discard', danger: true });
  }

  function row(n) {
    var li = document.createElement('li');
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'notes-row';
    b.dataset.id = n.id;
    if (n.id === S.saved.id) b.setAttribute('aria-current', 'true');
    var t = document.createElement('span'); t.className = 'notes-row-t'; t.textContent = rowTitle(n);
    var p = document.createElement('span'); p.className = 'notes-row-p'; p.textContent = n.preview || '';
    var m = document.createElement('span'); m.className = 'notes-row-m';
    var d = document.createElement('span'); d.textContent = dateLabel(n.updated);
    m.appendChild(d);
    if (sourceLabel(n.source)) {
      var s = document.createElement('span'); s.className = 'notes-row-src'; s.textContent = sourceLabel(n.source);
      m.appendChild(s);
    }
    b.appendChild(t); b.appendChild(p); b.appendChild(m);
    b.addEventListener('click', function () { openNote(n.id); });
    li.appendChild(b);
    return li;
  }

  function render() {
    var ul = $('notesItems');
    ul.textContent = '';
    S.notes.forEach(function (n) { ul.appendChild(row(n)); });
    var empty = $('notesEmpty');
    empty.hidden = S.notes.length > 0;
    empty.textContent = emptyText(S.q);
    $('notesCount').textContent = countLabel(S.total, S.matched, S.q);
  }

  // The same list asked for twice at once (the tab opens and the hash handler echoes it) is fetched once.
  // A save or a delete passes fresh = true: the list must come from after the change.
  function load(fresh) {
    var q = S.q;
    if (S.loading && S.loadingQ === q && !fresh) return S.loading;
    var mine = api('GET', listUrl(q)).then(function (d) {
      if (q !== S.q) return;                         // a newer search is already on its way
      S.notes = d.notes || []; S.total = d.total || 0; S.matched = d.matched || 0;
      S.lastLoad = Date.now();
      render();
    }, function (e) { setStatus(errorText(e)); }).then(function () { if (S.loading === mine) S.loading = null; });
    S.loading = mine;
    S.loadingQ = q;
    return mine;
  }

  // Called by the Tasks page when the Notes tab opens (and again by its hash handler: the guard skips that echo).
  function show() {
    return detect().then(function (ok) {
      $('notesOff').hidden = ok;
      $('notesWrap').hidden = !ok;
      if (ok && Date.now() - S.lastLoad > 1500) return load();
    });
  }

  function showEditor(on) {
    $('notesEdit').hidden = !on;
    $('notesHint').hidden = on;
    $('notesWrap').classList.toggle('editing', on);
  }

  function fill(note) {
    S.saved = { id: note.id || null, title: note.title || '', body: note.body || '' };
    $('notesTitle').value = S.saved.title;
    $('notesBody').value = S.saved.body;
    $('notesDelete').disabled = !S.saved.id;
    setStatus('');
    showEditor(true);
    render();
  }

  function openNote(id) {
    canLeave().then(function (ok) {
      if (!ok) return;
      api('GET', '/api/notes/' + encodeURIComponent(id)).then(function (n) {
        fill(n);
        if (!coarsePointer()) $('notesBody').focus();
      }, function (e) { toast(errorText(e)); load(true); });
    });
  }

  function newNote() {
    canLeave().then(function (ok) {
      if (!ok) return;
      fill({ id: null, title: '', body: '' });
      $('notesBody').focus();
    });
  }

  function save() {
    if (S.busy) return;
    var title = $('notesTitle').value.trim(), body = $('notesBody').value;
    if (!body.trim()) { setStatus('Write something first.'); return; }
    S.busy = true;
    $('notesSave').disabled = true;
    setStatus('Saving…');
    var call = S.saved.id ? api('PUT', '/api/notes/' + S.saved.id, { title: title, body: body })
                          : api('POST', '/api/notes', { title: title, body: body, source: 'manual' });
    call.then(function (n) {
      S.saved = { id: n.id, title: n.title, body: n.body };
      $('notesTitle').value = n.title;
      $('notesDelete').disabled = false;
      setStatus('Saved');
      S.lastLoad = 0;
      return load(true);
    }, function (e) { setStatus(errorText(e)); })
      .then(function () { S.busy = false; $('notesSave').disabled = false; });
  }

  function copy() {
    var b = $('notesBody');
    var text = b.value;
    if (!text) return;
    var done = function () { toast('Copied'); };
    var fallback = function () { b.select(); try { document.execCommand('copy'); done(); } catch (e) { toast('Could not copy'); } };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, fallback);
    else fallback();
  }

  function remove() {
    var id = S.saved.id;
    if (!id) return;
    ask({ title: 'Delete this note?', message: 'It moves to the trash folder on your Mac.', confirm: 'Delete', danger: true })
      .then(function (ok) {
        if (!ok) return;
        api('DELETE', '/api/notes/' + id).then(function () {
          S.saved = { id: null, title: '', body: '' };
          showEditor(false);
          S.lastLoad = 0;
          toast('Note deleted');
          return load(true);
        }, function (e) { toast(errorText(e)); });
      });
  }

  function back() {
    canLeave().then(function (ok) {
      if (!ok) return;
      S.saved = { id: null, title: '', body: '' };
      showEditor(false);
      render();
    });
  }

  function onEdit() { setStatus(dirty() ? 'Unsaved changes' : ''); }

  function init() {
    if (!$('panel-notes')) return;
    $('notesNew').addEventListener('click', newNote);
    $('notesSave').addEventListener('click', save);
    $('notesCopy').addEventListener('click', copy);
    $('notesDelete').addEventListener('click', remove);
    $('notesBack').addEventListener('click', back);
    $('notesTitle').addEventListener('input', onEdit);
    $('notesBody').addEventListener('input', onEdit);
    $('notesSearch').addEventListener('input', function () {
      var v = this.value;
      clearTimeout(S.timer);
      S.timer = setTimeout(function () { S.q = v.trim(); load(); }, SEARCH_DELAY);
    });
    document.addEventListener('keydown', function (e) {
      if ((e.metaKey || e.ctrlKey) && String(e.key).toLowerCase() === 's' && $('panel-notes').classList.contains('active') && !$('notesEdit').hidden) {
        e.preventDefault();
        save();
      }
    });
    root.addEventListener('beforeunload', function (e) { if (dirty()) { e.preventDefault(); e.returnValue = ''; } });
    detect();      // shows the tab when the routes are there
  }

  root.CodecNotes = { show: show, detect: detect, ready: function () { return S.ready === true; }, core: core, _state: S };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})(typeof window !== 'undefined' ? window : globalThis);
