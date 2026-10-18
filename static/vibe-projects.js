/* Vibe projects on the page (docs/VIBE-PROJECTS-DESIGN.md).

   Keeps a project's files as editor models, shows them as tabs, writes the AI's files
   into them while the answer streams, applies them behind a checkpoint when it ends,
   and offers Undo and the Versions drawer. Loaded after static/vibe-files.js and
   before the page's own script; the page calls into window.VibeProj and this file
   calls the page's globals (ed, addMsg, addOut, esc, detectLang) only when it runs.

   Nothing here runs a file. Every file name and label is put on the page with
   textContent, never as HTML. */
(function () {
  'use strict';

  var VF = window.VibeFiles;
  var KIND = { start: 'Start', before: 'Before AI', ai: 'AI change', restore: 'Restore',
    before_restore: 'Before restore', manual: 'Saved' };
  var PLACEHOLDER = /^(\/\/ Ask CODEC to build something|# New project)\s*$/;
  var CONTEXT_CHARS = 60000;

  // files[path] = { model, base, fresh }   base = what is on disk, fresh = made during this answer
  var P = { id: null, title: '', files: {}, active: null, canUndo: false, disabled: false,
    scratch: null, live: null, lastAi: null, saveTimer: null, tabTimer: null };

  function $(id) { return document.getElementById(id); }
  function toast(m) { if (window.CodecShell && window.CodecShell.toast) window.CodecShell.toast(m); }
  function out(cls, m) { if (typeof window.addOut === 'function') window.addOut(cls, m); }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function api(method, url, body) {
    var opts = { method: method, credentials: 'same-origin' };
    if (body !== undefined) { opts.headers = { 'Content-Type': 'application/json' }; opts.body = JSON.stringify(body); }
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) { var e = new Error((d && d.error) || ('HTTP ' + r.status)); e.status = r.status; throw e; }
        return d;
      });
    });
  }
  function base(path) { return '/api/vibe/projects/' + P.id + path; }
  function fileUrl(path) { return base('/file?path=' + encodeURIComponent(path)); }

  // ── editor models ───────────────────────────────────────────────────────
  function makeModel(path, text) {
    var m = window.monaco.editor.createModel(text, VF.monacoLang(path));
    m.onDidChangeContent(function () { if (!P.live) { scheduleTabs(); scheduleSave(); } });
    return m;
  }

  // A model of our own to show when no project file is open. Monaco throws away the model it
  // made for the editor as soon as another one is shown, so that one cannot be kept for this.
  function scratch(text) {
    if (!P.scratch || P.scratch.isDisposed()) {
      P.scratch = window.monaco.editor.createModel(text || '// Ask CODEC to build something\n', 'plaintext');
    }
    return P.scratch;
  }

  // One undo step in the editor for the whole write, so Cmd-Z also takes it back.
  function setText(model, text) {
    if (model.getValue() === text) return;
    model.pushStackElement();
    model.pushEditOperations([], [{ range: model.getFullModelRange(), text: text }], function () { return null; });
    model.pushStackElement();
  }

  function order(paths) {
    function rank(p) { return p === 'index.html' ? 0 : /^(main|app|script)\./.test(p) ? 1 : 2; }
    return paths.slice().sort(function (a, b) { return rank(a) - rank(b) || (a < b ? -1 : a > b ? 1 : 0); });
  }

  function activate(path) {
    var f = P.files[path];
    if (!f || !window.ed) return;
    P.active = path;
    if (window.ed.getModel() !== f.model) window.ed.setModel(f.model);
    var fn = $('fn');
    if (fn) { fn.value = path; fn.readOnly = true; fn.title = 'The open file. Renaming is not available yet.'; }
    var lg = $('lg');
    if (lg) lg.disabled = true;
    renderTabs();
  }

  function renderTabs() {
    var bar = $('ftabs');
    if (!bar) return;
    var paths = order(Object.keys(P.files));
    bar.hidden = !P.id || !paths.length;
    bar.textContent = '';
    paths.forEach(function (p) {
      var f = P.files[p];
      var b = el('button', 'ftab' + (p === P.active ? ' sel' : ''));
      b.type = 'button';
      b.setAttribute('role', 'tab');
      b.setAttribute('aria-selected', p === P.active ? 'true' : 'false');
      b.title = p;
      b.appendChild(el('span', 'ftab-n', p.split('/').pop()));
      if (f.fresh) b.appendChild(el('span', 'ftab-dot live'));
      else if (f.model.getValue() !== f.base) b.appendChild(el('span', 'ftab-dot'));
      b.addEventListener('click', function () { activate(p); });
      bar.appendChild(b);
    });
    var undo = $('undoBtn'), ver = $('verBtn');
    if (undo) { undo.hidden = !P.id; undo.disabled = !P.canUndo; }
    if (ver) ver.hidden = !P.id;
  }
  function scheduleTabs() { clearTimeout(P.tabTimer); P.tabTimer = setTimeout(renderTabs, 250); }

  // The preview follows the files: after an undo, a restore, an opened project and a saved edit.
  function refreshPreview() {
    if (typeof window.refreshPreview === 'function') window.refreshPreview();
  }

  // ── saving the person's own edits ──────────────────────────────────────
  function scheduleSave() {
    clearTimeout(P.saveTimer);
    P.saveTimer = setTimeout(function () {
      flush().then(function () { if (!P.live) refreshPreview(); }).catch(function () {});
    }, 1500);
  }

  function dirtyPaths() {
    return Object.keys(P.files).filter(function (p) {
      var f = P.files[p];
      return !f.fresh && f.model.getValue() !== f.base;
    });
  }

  function flush(keepalive) {
    if (!P.id) return Promise.resolve();
    var id = P.id;
    var jobs = dirtyPaths().map(function (p) {
      var f = P.files[p], text = f.model.getValue();
      var opts = { method: 'PUT', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: p, content: text }) };
      if (keepalive && text.length < 30000) opts.keepalive = true;
      return fetch('/api/vibe/projects/' + id + '/file', opts).then(function (r) {
        if (!r.ok) return r.json().catch(function () { return {}; }).then(function (d) { throw new Error(d.error || ('HTTP ' + r.status)); });
        if (P.files[p]) P.files[p].base = text;
      });
    });
    return Promise.all(jobs).then(function () { renderTabs(); });
  }

  // ── opening, creating, leaving a project ───────────────────────────────
  function dispose() {
    Object.keys(P.files).forEach(function (p) {
      try { P.files[p].model.dispose(); } catch (e) { /* already gone */ }
    });
    P.files = {};
  }

  function detach() {
    clearTimeout(P.saveTimer);
    if (Object.keys(P.files).length && window.ed && window.monaco) window.ed.setModel(scratch());
    dispose();
    P.id = null; P.title = ''; P.active = null; P.canUndo = false; P.live = null; P.lastAi = null;
    var fn = $('fn'), lg = $('lg');
    if (fn) { fn.readOnly = false; fn.title = ''; }
    if (lg) lg.disabled = false;
    closeVersions();
    renderTabs();
  }

  function pickFirst(paths) {
    return order(paths)[0] || null;
  }

  function open(id) {
    return api('GET', '/api/vibe/projects/' + id).then(function (info) {
      detach();
      P.id = id; P.title = info.title || id; P.canUndo = !!info.can_undo;
      return loadFiles(info.files.map(function (f) { return f.path; })).then(function () {
        var first = pickFirst(Object.keys(P.files));
        if (first) activate(first); else renderTabs();
        refreshPreview();
        return info;
      });
    });
  }

  function loadFiles(paths) {
    return Promise.all(paths.map(function (p) {
      return api('GET', fileUrl(p)).catch(function () { return null; });
    })).then(function (list) {
      list.forEach(function (d, i) {
        if (!d || d.binary || d.too_large || typeof d.content !== 'string') return;
        var path = paths[i], f = P.files[path];
        if (f) { setText(f.model, d.content); f.base = d.content; f.fresh = false; }
        else P.files[path] = { model: makeModel(path, d.content), base: d.content, fresh: false };
      });
    });
  }

  // After an undo or a restore: make the editor match the folder again.
  function reload() {
    return api('GET', '/api/vibe/projects/' + P.id).then(function (info) {
      var want = info.files.map(function (f) { return f.path; });
      Object.keys(P.files).forEach(function (p) {
        if (want.indexOf(p) === -1) {
          if (window.ed && window.ed.getModel() === P.files[p].model) window.ed.setModel(scratch());
          try { P.files[p].model.dispose(); } catch (e) { /* gone */ }
          delete P.files[p];
        }
      });
      P.canUndo = !!info.can_undo;
      return loadFiles(want).then(function () {
        if (!P.files[P.active]) P.active = pickFirst(Object.keys(P.files));
        if (P.active) activate(P.active); else renderTabs();
        refreshPreview();
      });
    });
  }

  function meaningful(text) { return text.trim().length > 10 && !PLACEHOLDER.test(text.trim()); }

  function titleFrom(msg) {
    var t = String(msg || '').trim();
    t = t.length > 40 ? t.substring(0, 40).replace(/\s+\S*$/, '') : t;
    t = t.replace(/[^a-zA-Z0-9 _-]/g, '').trim().replace(/\s+/g, '-').toLowerCase();
    return t.length > 3 ? t : 'untitled';
  }

  function create(msg) {
    var text = window.ed ? window.ed.getValue() : '';
    var seed = [];
    var seedPath = '';
    if (meaningful(text)) {
      var fnv = ($('fn') && $('fn').value || '').trim();
      var hasExt = /\.[A-Za-z0-9]{1,8}$/.test(fnv) && !/^untitled/i.test(fnv);
      var lang = typeof window.detectLang === 'function' ? window.detectLang(text) : '';
      seedPath = hasExt ? fnv : VF.defaultName(lang);
      seed.push({ path: seedPath, content: text });
    }
    return api('POST', '/api/vibe/projects', { title: titleFrom(msg), files: seed }).then(function (info) {
      P.id = info.id; P.title = info.title || info.id; P.canUndo = false;
      if (seedPath) {   // what the editor showed becomes the project's first file
        var m = makeModel(seedPath, text);
        P.files[seedPath] = { model: m, base: text, fresh: false };
        window.ed.setModel(m);
        activate(seedPath);
      } else renderTabs();
    });
  }

  // Called by the page when a message is sent. Resolves true when the project flow runs,
  // false when the page should use the single-file flow.
  function begin(msg) {
    if (P.disabled || !VF || !window.monaco) return Promise.resolve(false);
    var go = P.id ? flush().catch(function (e) { toast('Could not save your edits: ' + e.message); })
                  : create(msg);
    return go.then(function () { return true; }, function (e) {
      if (!P.id) { P.disabled = true; out('sy', 'Projects are not available here; using single-file Vibe'); return false; }
      throw e;
    });
  }

  // ── what the model is told about the files ─────────────────────────────
  function contextBlock(editorText) {
    var paths = P.id ? order(Object.keys(P.files)) : [];
    if (!paths.length) {
      return meaningful(editorText || '') ? '\n\n[CURRENT CODE]\n```\n' + editorText + '\n```' : '';
    }
    paths = [P.active].concat(paths.filter(function (p) { return p !== P.active; })).filter(Boolean);
    var used = 0, parts = ['\n\n[PROJECT FILES]\nOpen file: ' + P.active + '\nAll files: ' + paths.join(', ')];
    paths.forEach(function (p) {
      var text = P.files[p].model.getValue();
      if (used + text.length > CONTEXT_CHARS) { parts.push('(' + p + ' is not shown: too long for this message)'); return; }
      used += text.length;
      parts.push('```' + (VF.langOfPath(p) || '') + ' file:' + p + '\n' + text + (text.slice(-1) === '\n' ? '' : '\n') + '```');
    });
    return parts.join('\n');
  }

  // ── an answer arriving ─────────────────────────────────────────────────
  function startLive() {
    P.live = { active0: P.active, touched: {}, cur: null, last: {} };
  }

  function live(raw) {
    if (!P.live) startLive();
    var blocks = VF.resolvePaths(VF.parseBlocks(raw), P.live.active0).filter(function (b) { return b.code.length > 0; });
    blocks.forEach(function (b, i) {
      var f = P.files[b.path];
      if (!f) {
        f = P.files[b.path] = { model: makeModel(b.path, ''), base: '', fresh: true };
        P.live.touched[b.path] = 'new';
      } else if (!P.live.touched[b.path]) {
        P.live.touched[b.path] = 'old';
        f.model.pushStackElement();
      }
      if (P.live.last[b.path] !== b.code) {
        P.live.last[b.path] = b.code;
        f.model.pushEditOperations([], [{ range: f.model.getFullModelRange(), text: b.code }], function () { return null; });
      }
      if (i === blocks.length - 1 && P.live.cur !== b.path) { P.live.cur = b.path; activate(b.path); }
      else if (i === blocks.length - 1) renderTabs();
    });
    // Follow the typing only while the editor can be seen: revealing a line in a hidden editor
    // (code pane hidden, phone on another tab) leaves it scrolled to the end when it comes back.
    var node = window.ed && window.ed.getDomNode && window.ed.getDomNode();
    if (node && node.offsetHeight > 0 && P.live.cur && window.ed.getModel() === (P.files[P.live.cur] || {}).model) {
      window.ed.revealLine(window.ed.getModel().getLineCount());
    }
  }

  function endLive() {
    Object.keys((P.live || {}).touched || {}).forEach(function (p) {
      if (P.files[p]) P.files[p].model.pushStackElement();
    });
  }

  function revertLive() {
    var live = P.live;
    P.live = null;
    if (!live) return;
    Object.keys(live.touched).forEach(function (p) {
      var f = P.files[p];
      if (!f) return;
      if (live.touched[p] === 'new') {
        if (window.ed && window.ed.getModel() === f.model) window.ed.setModel(scratch());
        try { f.model.dispose(); } catch (e) { /* gone */ }
        delete P.files[p];
      } else setText(f.model, f.base);
    });
    if (!P.files[P.active] && live.active0 && P.files[live.active0]) P.active = live.active0;
    if (P.files[P.active]) activate(P.active); else renderTabs();
  }

  function abort() {
    var had = !!P.live;
    revertLive();
    return had;
  }

  // The answer is complete: write its files behind a checkpoint.
  function finish(raw, message) {
    var live0 = P.live;
    var blocks = VF.resolvePaths(VF.parseBlocks(raw), live0 ? live0.active0 : P.active).filter(function (b) { return b.code.trim() !== ''; });
    if (!blocks.length) { revertLive(); return Promise.resolve({ applied: false, blocks: [] }); }
    blocks = blocks.map(function (b) { return { lang: b.lang, path: b.path, code: b.code.slice(-1) === '\n' ? b.code : b.code + '\n' }; });
    endLive();
    return api('POST', base('/apply'), { blocks: blocks.map(function (b) { return { path: b.path, content: b.code }; }), label: message })
      .then(function (res) {
        P.live = null;
        blocks.forEach(function (b) {
          var f = P.files[b.path];
          if (f) { setText(f.model, b.code); f.base = b.code; f.fresh = false; }
        });
        if (res.checkpoint) { P.canUndo = true; P.lastAi = res.checkpoint.id; }
        renderTabs();
        return { applied: true, res: res, blocks: blocks };
      }, function (e) { revertLive(); throw e; });
  }

  // ── what the person sees in the chat after an apply ───────────────────
  function messageNode(explanation, result) {
    var box = el('div', 'mg ma');
    if (explanation) {
      var p = el('div', null, explanation);
      p.style.whiteSpace = 'pre-wrap';
      box.appendChild(p);
    }
    var res = result.res || {};
    var chips = el('div', 'chips');
    (res.changed || []).forEach(function (c) {
      var chip = el('button', 'chip ' + c.status);
      chip.type = 'button';
      chip.title = 'Open ' + c.path;
      chip.appendChild(el('span', 'chip-n', c.path));
      chip.appendChild(el('span', 'chip-s', c.status === 'added' ? 'new' : '+' + c.added + ' −' + c.removed));
      chip.addEventListener('click', function () { if (P.files[c.path]) activate(c.path); });
      chips.appendChild(chip);
    });
    if (!(res.changed || []).length) chips.appendChild(el('span', 'chip-none', 'No file changed'));
    box.appendChild(chips);
    if (res.checkpoint && res.before) {
      var undo = el('button', 'undo-link', 'Revert to before this');
      undo.type = 'button';
      undo.addEventListener('click', function () { revertTo(res.before, res.checkpoint.id, undo); });
      box.appendChild(undo);
    }
    return box;
  }

  function revertTo(beforeId, cpId, btn) {
    var later = P.lastAi && P.lastAi !== cpId;
    var ask = window.CodecShell && window.CodecShell.ask
      ? window.CodecShell.ask({ title: 'Go back to before this change?',
          message: (later ? 'Later changes are removed from your files too. ' : '') + 'Every version stays in Versions, so you can come back.',
          confirm: 'Go back' })
      : Promise.resolve(true);
    return Promise.resolve(ask).then(function (ok) {
      if (!ok) return;
      return flush().then(function () { return api('POST', base('/restore'), { checkpoint: beforeId }); })
        .then(reload).then(function () { toast('Went back to before that change'); if (btn) btn.disabled = true; })
        .catch(function (e) { toast('Could not go back: ' + e.message); });
    });
  }

  // ── undo and versions ──────────────────────────────────────────────────
  function undo() {
    if (!P.id) return Promise.resolve();
    return flush().then(function () { return api('POST', base('/undo')); })
      .then(function () { return reload(); })
      .then(function () { toast('Undid the last AI change'); out('sy', 'Undid the last AI change'); })
      .catch(function (e) { toast(e.status === 409 ? 'Nothing to undo' : 'Could not undo: ' + e.message); if (e.status === 409) { P.canUndo = false; renderTabs(); } });
  }

  function fmtTime(ts) {
    var d = new Date(ts);
    return isNaN(d) ? '' : d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  }

  function diffNode(text) {
    var pre = el('pre', 'vdiff');
    String(text || '').split('\n').forEach(function (ln, i, all) {
      if (i === all.length - 1 && ln === '') return;
      var cls = ln.indexOf('+++') === 0 || ln.indexOf('---') === 0 ? 'h' : ln[0] === '+' ? 'a' : ln[0] === '-' ? 'r' : ln.indexOf('@@') === 0 ? 'h' : '';
      pre.appendChild(el('span', cls, ln + '\n'));
    });
    return pre;
  }

  function versions() {
    if (!P.id) return Promise.resolve();
    var drawer = $('vd'), listEl = $('vdList');
    if (!drawer || !listEl) return Promise.resolve();
    listEl.textContent = '';
    listEl.appendChild(el('div', 'vmsg', 'Loading...'));
    drawer.classList.add('vo');
    var ov = $('vov'); if (ov) ov.classList.add('sh');
    return flush().catch(function () {}).then(function () { return api('GET', base('/checkpoints?limit=200')); }).then(function (cps) {
      listEl.textContent = '';
      if (!cps.length) { listEl.appendChild(el('div', 'vmsg', 'No versions yet.')); return; }
      cps.forEach(function (cp, i) { listEl.appendChild(versionRow(cp, i === 0)); });
    }).catch(function (e) { listEl.textContent = ''; listEl.appendChild(el('div', 'vmsg', 'Could not load versions: ' + e.message)); });
  }

  function versionRow(cp, newest) {
    var row = el('div', 'vrow');
    var head = el('button', 'vrow-h');
    head.type = 'button';
    head.setAttribute('aria-expanded', 'false');
    head.appendChild(el('span', 'vkind k-' + cp.kind, KIND[cp.kind] || cp.kind));
    head.appendChild(el('span', 'vlabel', cp.label || cp.id));
    var st = cp.stat || {};
    var meta = fmtTime(cp.ts) + (st.lines_added || st.lines_removed ? '  ·  +' + st.lines_added + ' −' + st.lines_removed : '') +
      '  ·  ' + (cp.files_count || 0) + (cp.files_count === 1 ? ' file' : ' files');
    head.appendChild(el('span', 'vmeta', meta));
    row.appendChild(head);
    var body = el('div', 'vbody');
    body.hidden = true;
    row.appendChild(body);
    var loaded = false;
    head.addEventListener('click', function () {
      body.hidden = !body.hidden;
      head.setAttribute('aria-expanded', body.hidden ? 'false' : 'true');
      if (body.hidden || loaded) return;
      loaded = true;
      body.appendChild(el('div', 'vmsg', 'Loading...'));
      api('GET', base('/diff?from=' + encodeURIComponent(cp.parent || '') + '&to=' + encodeURIComponent(cp.id))).then(function (d) {
        body.textContent = '';
        if (!d.files.length) body.appendChild(el('div', 'vmsg', 'No file differs from the version before it.'));
        d.files.forEach(function (f) {
          var h = el('div', 'vfile');
          h.appendChild(el('span', 'vfile-s s-' + f.status, f.status));
          h.appendChild(el('span', 'vfile-n', f.path));
          body.appendChild(h);
          body.appendChild(diffNode(f.diff));
        });
        if (newest) body.appendChild(el('div', 'vmsg', 'This is the current version.'));
        else {
          var btn = el('button', 'bb vrestore', 'Restore this version');
          btn.type = 'button';
          btn.addEventListener('click', function () { restoreTo(cp, btn); });
          body.appendChild(btn);
        }
      }).catch(function (e) { body.textContent = ''; body.appendChild(el('div', 'vmsg', 'Could not load: ' + e.message)); });
    });
    return row;
  }

  function restoreTo(cp, btn) {
    var ask = window.CodecShell && window.CodecShell.ask
      ? window.CodecShell.ask({ title: 'Restore this version?', message: 'Your files go back to "' + (cp.label || cp.id) +
          '". The files as they are now are saved as a version first, so nothing is lost.', confirm: 'Restore' })
      : Promise.resolve(true);
    return Promise.resolve(ask).then(function (ok) {
      if (!ok) return;
      btn.disabled = true;
      return flush().then(function () { return api('POST', base('/restore'), { checkpoint: cp.id }); })
        .then(reload)
        .then(function () { closeVersions(); toast('Restored'); out('sy', 'Restored a version'); })
        .catch(function (e) { btn.disabled = false; toast('Could not restore: ' + e.message); });
    });
  }

  function closeVersions() {
    var d = $('vd'); if (d) d.classList.remove('vo');
    var ov = $('vov'); if (ov) ov.classList.remove('sh');
  }

  // ── small hooks for the page ───────────────────────────────────────────
  function previewSource() {
    if (!P.id) return null;
    var texts = {};
    Object.keys(P.files).forEach(function (p) { texts[p] = P.files[p].model.getValue(); });
    var root = P.active && /\.html?$/i.test(P.active) ? P.active : (texts['index.html'] != null ? 'index.html' : null);
    return root ? VF.inlineAssets(texts[root], texts) : null;
  }

  function setActiveText(text) {
    var f = P.files[P.active];
    if (f) setText(f.model, text); else if (window.ed) window.ed.setValue(text);
  }

  document.addEventListener('visibilitychange', function () { if (document.hidden && P.id) flush(true).catch(function () {}); });
  window.addEventListener('pagehide', function () { if (P.id) flush(true).catch(function () {}); });

  window.VibeProj = {
    begin: begin, contextBlock: contextBlock, live: live, finish: finish, abort: abort, open: open,
    detach: detach, undo: undo, versions: versions, closeVersions: closeVersions, messageNode: messageNode,
    previewSource: previewSource, setActiveText: setActiveText,
    id: function () { return P.id; }, title: function () { return P.title; }, active: function () { return P.active; },
    on: function () { return !!P.id; }, flush: flush,
    _state: P   // for tests and debugging
  };
})();
