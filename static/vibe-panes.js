/* Vibe code and preview panes that open when there is something to show
   (docs/VOICE-IN-CHAT-AND-VIBE-PANES-DESIGN.md, part 2).

   Each pane is auto, open or closed, kept per project in this browser (per page for single-file Vibe).
   Auto: the code pane is closed until there is a file, the preview until there is a page; then they open.
   The owner's click wins: a pane he closed stays closed in that project until he opens it again.
   Only the wide layout uses this; the phone keeps its Chat / Preview / Code tabs.

   The top half is plain functions, so Node tests them without a browser. */
(function (root) {
  'use strict';

  var STATES = ['auto', 'open', 'closed'];
  var PLACEHOLDER = /^(\/\/ Ask CODEC to build something|# New project)\s*$/;
  var KEY = 'codec-vibe-panes:';          // + project id, or nothing for single-file Vibe
  var OLD_CODE_KEY = 'codec-vibe-code';   // the old "Hide code" setting, one for every project

  function storageKey(projectId) { return KEY + (projectId ? String(projectId) : ''); }

  // A project with no choice yet: the old "Hide code" carries over, so nothing set before changes.
  function defaults(oldCode) { return { code: oldCode === 'off' ? 'closed' : 'auto', preview: 'auto' }; }

  function parse(raw, oldCode) {
    var d = defaults(oldCode), v = null;
    try { v = raw ? JSON.parse(raw) : null; } catch (e) { v = null; }
    if (v && typeof v === 'object') {
      if (STATES.indexOf(v.code) !== -1) d.code = v.code;
      if (STATES.indexOf(v.preview) !== -1) d.preview = v.preview;
    }
    return d;
  }

  function isOpen(state, hasContent) {
    if (state === 'open') return true;
    if (state === 'closed') return false;
    return !!hasContent;
  }

  // Something to show in the code pane: a project file, or real text in the single-file editor.
  function hasCode(paths, text) {
    if (paths && paths.length) return true;
    var t = String(text || '').trim();
    return t.length > 0 && !PLACEHOLDER.test(t);
  }

  // The same test the preview has always used for "this is a page".
  function isPage(text) {
    var c = String(text || '');
    return c.trim().indexOf('<!DOCTYPE') === 0 || c.indexOf('<canvas') !== -1 || c.indexOf('<body') !== -1 || c.indexOf('<html') !== -1;
  }

  // The owner's click on a pane: his choice, kept for the project.
  function choose(prefs, pane, open) {
    var n = { code: prefs.code, preview: prefs.preview };
    if (pane === 'code' || pane === 'preview') n[pane] = open ? 'open' : 'closed';
    return n;
  }

  // A pane that was closed and opens without a click: the phone tab gets its dot.
  function openedByItself(wasOpen, nowOpen, state) { return !wasOpen && nowOpen && state === 'auto'; }

  var core = { storageKey: storageKey, defaults: defaults, parse: parse, isOpen: isOpen, hasCode: hasCode,
    isPage: isPage, choose: choose, openedByItself: openedByItself };

  if (typeof document === 'undefined') {            // Node: only the plain functions
    if (typeof module !== 'undefined' && module.exports) module.exports = core;
    return;
  }

  // ── the page ───────────────────────────────────────────────────────────

  var cur = { key: null, prefs: null };
  var timer = null;

  function lsGet(k) { try { return root.localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { root.localStorage.setItem(k, v); } catch (e) { /* private window: this visit only */ } }
  function $(id) { return document.getElementById(id); }

  function projectId() {
    var VP = root.VibeProj;
    return VP && VP.on() ? VP.id() : '';
  }

  function prefs() {
    var k = storageKey(projectId());
    if (cur.key !== k) { cur.key = k; cur.prefs = parse(lsGet(k), lsGet(OLD_CODE_KEY)); }
    return cur.prefs;
  }

  function content() {
    var VP = root.VibeProj, ed = root.ed;
    var on = !!(VP && VP.on());
    var text = ed && typeof ed.getValue === 'function' ? ed.getValue() : '';
    var src = (on && VP.previewSource && VP.previewSource()) || text;
    return { code: hasCode(on ? VP.paths() : [], text), preview: isPage(src) || !!(on && VP.liveActive && VP.liveActive()) };
  }

  function dot(tabId, show) {
    var sp = document.querySelector('.sp');
    var d = document.querySelector('#' + tabId + ' .vdot');
    if (d && show && sp && sp.getAttribute('data-pane') !== (tabId === 'vtabCode' ? 'code' : 'preview')) d.hidden = false;
  }

  function setCode(open) {
    var sp = document.querySelector('.sp');
    if (!sp) return;
    var was = !sp.classList.contains('code-off');
    sp.classList.toggle('code-off', !open);
    var b = $('codeBtn');
    if (b) { b.setAttribute('aria-pressed', open ? 'true' : 'false'); b.textContent = open ? 'Hide code' : 'Show code'; }
    var s = $('showCode');
    if (s) s.hidden = open;
    if (open && !was && root.ed && typeof root.ed.layout === 'function') setTimeout(function () { root.ed.layout(); }, 0);
    return was;
  }

  // Only shows or hides the preview pane; doPrev fills it.
  function setPrev(open) {
    var sp = document.querySelector('.sp'), pp = $('pp');
    if (!sp || !pp) return;
    pp.classList.toggle('off', !open);
    sp.classList.toggle('prev-off', !open);
    var s = $('showPrev');
    if (s) s.hidden = open;
  }

  function prevShown() { var pp = $('pp'); return !!pp && !pp.classList.contains('off'); }

  // Look at what there is and open or close the panes that are on auto.
  function sync() {
    var p = prefs(), c = content();
    var codeOpen = isOpen(p.code, c.code);
    var wasCode = setCode(codeOpen);
    if (openedByItself(wasCode, codeOpen, p.code)) dot('vtabCode', true);
    var want = isOpen(p.preview, c.preview), was = prevShown();
    if (want && !was) {
      setPrev(true);
      if (c.preview && typeof root.doPrev === 'function') root.doPrev(true);   // fills it
    } else if (!want && was) {
      setPrev(false);
    } else setPrev(was);
  }

  function syncSoon() { clearTimeout(timer); timer = setTimeout(sync, 300); }

  // The owner's click: Code / Preview at the top of the chat, Hide code, the preview's close button, the Preview button.
  // noFill: the caller (doPrev itself) fills the preview.
  function pick(pane, open, noFill) {
    cur.prefs = choose(prefs(), pane, open);
    lsSet(cur.key, JSON.stringify(cur.prefs));
    if (pane === 'code') { setCode(open); return; }
    setPrev(open);
    if (open && !noFill && content().preview && typeof root.doPrev === 'function') root.doPrev(true);
  }

  function toggle(pane) {
    var open = pane === 'code' ? document.querySelector('.sp.code-off') !== null : !prevShown();
    pick(pane, open);
  }

  // doPrev asks before an automatic refresh opens the preview: never when the owner closed it.
  function allow(pane) { return prefs()[pane] !== 'closed'; }

  // The editor's text and model tell when the first file appears.
  function attachEditor(ed) {
    if (!ed) return;
    if (typeof ed.onDidChangeModelContent === 'function') ed.onDidChangeModelContent(syncSoon);
    if (typeof ed.onDidChangeModel === 'function') ed.onDidChangeModel(syncSoon);
    sync();
  }

  root.VibePanes = { sync: sync, syncSoon: syncSoon, pick: pick, toggle: toggle, allow: allow, setPrev: setPrev,
    attachEditor: attachEditor, isPage: isPage, _core: core };
  sync();
})(typeof window !== 'undefined' ? window : this);
