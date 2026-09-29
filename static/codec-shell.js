/* CODEC app shell (UI phase 2, P2.1; docs/P2.1-DESIGN.md).

   Every app page loads this as the first element of <body>:
     <script src="/static/codec-shell.js?v=..." data-page="chat" data-title="Chat"
             data-status data-model-slot></script>
   It runs synchronously and inserts, before the page's own content:
   - the sidebar (desktop column, 64px rail when collapsed, phone drawer);
   - the top bar (title, optional status dot and model slot, new chat on the
     phone, the quick-settings button);
   - the phone tab bar (in the flow, placed last with CSS order);
   - the quick-settings panel (#sidePanel), with the ids page code expects.
   It also owns the behaviour the pages used to copy: theme, text size, lock,
   the wake-word toggle and the notification count. The old global names stay
   (openSidePanel, applyTheme, setThemePref, setTextSize, lockSession, ...), so
   page code that calls them keeps working. Styles live in static/codec.css. */
(function () {
  'use strict';

  var script = document.currentScript;
  var ds = (script && script.dataset) || {};
  var PAGE = ds.page || '';
  var TITLE = ds.title || 'CODEC';
  var HAS_STATUS = 'status' in ds;
  var HAS_MODEL_SLOT = 'modelSlot' in ds;

  // ── Small helpers ─────────────────────────────────────────────────────────
  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } }
  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function $(id) { return document.getElementById(id); }
  function isPhone() { return window.innerWidth < 768; }
  function toast(msg) { if (typeof window.showToast === 'function') window.showToast(msg); }

  // ── Icons: line SVG, 1.75 stroke, round caps (no emoji, no glyphs) ───────
  var P = {
    panel: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18"/>',
    sliders: '<path d="M21 4h-7M10 4H3M21 12h-9M8 12H3M21 20h-5M12 20H3M14 2v4M8 10v4M16 18v4"/>',
    pen: '<path d="M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.4 2.6a1 1 0 0 1 3 3l-9 9a2 2 0 0 1-.85.5l-2.87.84a.5.5 0 0 1-.62-.62l.84-2.87a2 2 0 0 1 .5-.85z"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    home: '<path d="M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8"/><path d="M3 10a2 2 0 0 1 .71-1.53l7-6a2 2 0 0 1 2.58 0l7 6A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    voice: '<path d="M2 10v3M6 6v11M10 3v18M14 8v7M18 5v13M22 10v3"/>',
    vibe: '<path d="m16 18 6-6-6-6M8 6l-6 6 6 6"/>',
    tasks: '<path d="m3 17 2 2 4-4M3 7l2 2 4-4M13 6h8M13 12h8M13 18h8"/>',
    inbox: '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
    gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    user: '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="10" r="3"/><path d="M7 20.66V19a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v1.66"/>',
    lock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    close: '<path d="M18 6 6 18M6 6l12 12"/>',
    trash: '<path d="M3 6h18M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
    speaker: '<path d="M11 5 6 9H2v6h4l5 4z"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/>' +
      '<line id="muteX1" x1="4" y1="4" x2="20" y2="20" stroke-width="2.5" style="display:none"/>' +
      '<line id="muteX2" x1="4" y1="20" x2="20" y2="4" stroke-width="2.5" style="display:none"/>',
    mic: '<path d="M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/><path d="M19 10v1a7 7 0 0 1-14 0v-1M12 18v4M8 22h8"/>',
    monitor: '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>',
    camera: '<path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/>',
    video: '<path d="m23 7-7 5 7 5z"/><rect x="1" y="5" width="15" height="14" rx="2"/>',
    theme: '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 0 0 18z" fill="currentColor" stroke="none"/>',
    text: '<path d="M4 7V5h16v2M9 19h6M12 5v14"/>',
    plug: '<path d="M12 22v-5M9 8V2M15 8V2M18 8v5a6 6 0 0 1-12 0V8z"/>'
  };
  function ico(name, size, cls) {
    return '<svg class="cs-ico' + (cls ? ' ' + cls : '') + '" width="' + (size || 20) + '" height="' + (size || 20) +
      '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"' +
      ' stroke-linejoin="round" aria-hidden="true">' + P[name] + '</svg>';
  }

  var NAV = [
    { id: 'home', href: '/', label: 'Today', icon: 'home' },
    { id: 'chat', href: '/chat', label: 'Chat', icon: 'chat' },
    { id: 'voice', href: '/voice', label: 'Voice', icon: 'voice' },
    { id: 'vibe', href: '/vibe', label: 'Vibe', icon: 'vibe' },
    { id: 'tasks', href: '/tasks', label: 'Tasks', icon: 'tasks' }
  ];
  var KEY_HINT = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '') ? 'Cmd K' : 'Ctrl K';

  // ── Markup ────────────────────────────────────────────────────────────────
  function sidebarHTML() {
    var nav = NAV.map(function (n) {
      var on = n.id === PAGE;
      return '<a class="cs-row' + (on ? ' cs-on' : '') + '" href="' + n.href + '"' + (on ? ' aria-current="page"' : '') +
        ' title="' + n.label + '">' + ico(n.icon) + '<span class="cs-label">' + n.label + '</span></a>';
    }).join('');
    return '' +
      '<aside class="cs-side" id="csSide" aria-label="CODEC">' +
        '<div class="cs-brand">' +
          '<a class="cs-logo" href="/" title="CODEC"><img src="/static/logo.svg" alt="" width="20" height="20">' +
            '<span class="cs-label cs-wordmark">CODEC</span></a>' +
          '<button type="button" class="cs-ibtn cs-collapse" onclick="CodecShell.toggleRail()" title="Collapse sidebar"' +
            ' aria-label="Collapse sidebar">' + ico('panel', 18) + '</button>' +
          '<button type="button" class="cs-ibtn cs-drawer-close" onclick="CodecShell.closeDrawer()" aria-label="Close menu">' +
            ico('close', 18) + '</button>' +
        '</div>' +
        '<button type="button" class="cs-new" onclick="CodecShell.newChat()" title="New chat">' + ico('pen', 18) +
          '<span class="cs-label">New chat</span></button>' +
        '<label class="cs-search" title="Search chats">' + ico('search', 16) +
          '<input id="csSearch" type="search" placeholder="Search chats" autocomplete="off" aria-label="Search chats">' +
          '<kbd class="cs-kbd">' + KEY_HINT + '</kbd></label>' +
        '<nav class="cs-nav" aria-label="Pages">' + nav + '</nav>' +
        '<div class="cs-hist-head cs-label" id="csHistHead">Chats</div>' +
        '<div class="cs-hist" id="csHist" role="list"></div>' +
        '<div class="cs-foot">' +
          '<a class="cs-row" href="/tasks#reports" id="csInbox" title="Inbox">' + ico('inbox') +
            '<span class="cs-label">Inbox</span><span class="cs-badge" id="csInboxBadge" hidden>0</span></a>' +
          '<a class="cs-row" href="/#settings" id="csSettings" title="Settings">' + ico('gear') +
            '<span class="cs-label">Settings</span></a>' +
          '<div class="cs-profile">' + ico('user') + '<span class="cs-label">You</span>' +
            '<button type="button" class="cs-ibtn cs-lock" onclick="lockSession()" title="Lock" aria-label="Lock">' +
            ico('lock', 18) + '</button></div>' +
        '</div>' +
      '</aside>' +
      '<div class="cs-scrim" id="csScrim" onclick="CodecShell.closeDrawer()"></div>';
  }

  function topHTML() {
    return '' +
      '<header class="cs-top" id="csTop">' +
        '<button type="button" class="cs-ibtn cs-menu" onclick="CodecShell.openDrawer()" aria-label="Open menu">' +
          ico('panel') + '</button>' +
        '<div class="cs-title" id="csTitle">' + esc(TITLE) + '</div>' +
        (HAS_STATUS ? '<span class="status-dot" id="statusDot" title="CODEC status"></span>' : '') +
        (HAS_MODEL_SLOT ? '<span class="cs-top-slot" id="hdrModelSlot"></span>' : '') +
        '<div class="cs-top-right">' +
          '<button type="button" class="cs-ibtn cs-newchat" onclick="CodecShell.newChat()" aria-label="New chat"' +
            ' title="New chat">' + ico('pen') + '</button>' +
          '<button type="button" class="cs-ibtn menu-btn" id="menuBtn" onclick="openSidePanel()" title="Quick settings"' +
            ' aria-label="Quick settings">' + ico('sliders') + '</button>' +
        '</div>' +
      '</header>';
  }

  function tabsHTML() {
    var tabs = [NAV[0], NAV[1], NAV[2], NAV[4]].map(function (n) {
      var on = n.id === PAGE;
      return '<a class="cs-tab' + (on ? ' cs-on' : '') + '" href="' + n.href + '"' + (on ? ' aria-current="page"' : '') +
        '>' + ico(n.icon, 22) + '<span>' + n.label + '</span></a>';
    }).join('');
    return '<nav class="cs-tabs" id="csTabs" aria-label="Pages">' + tabs +
      '<a class="cs-tab" href="/tasks#reports" id="csTabInbox">' + ico('inbox', 22) + '<span>Inbox</span>' +
      '<span class="cs-badge" id="csTabInboxBadge" hidden>0</span></a></nav>';
  }

  function panelHTML() {
    return '' +
      '<div class="side-panel-overlay" id="sidePanelOverlay" onclick="closeSidePanel()"></div>' +
      '<div class="side-panel" id="sidePanel" role="dialog" aria-label="Quick settings">' +
        '<div class="side-panel-header"><h2>Quick settings</h2>' +
          '<button type="button" class="cs-ibtn side-panel-close" onclick="closeSidePanel()" aria-label="Close quick settings">' +
          ico('close', 18) + '</button></div>' +
        '<div class="side-panel-body">' +
          '<button type="button" class="sp-item" id="voiceBtn" onclick="CodecShell.voiceReplies()">' +
            '<svg class="cs-ico" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"' +
            ' stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + P.speaker + '</svg>' +
            '<span class="sp-item-label">Voice replies</span><span class="sp-state" id="voiceState"></span></button>' +
          '<button type="button" class="sp-item" id="wakeBtn" onclick="CodecShell.wakeWord()"' +
            ' title="Always-on &quot;Hey CODEC&quot; wake word">' + ico('mic', 18) +
            '<span class="sp-item-label">Wake word</span><span class="sp-state" id="wakeState"></span></button>' +
          '<button type="button" class="sp-item" id="screenBtn" data-needs="takeScreenshot"' +
            ' onclick="takeScreenshot();closeSidePanel()">' + ico('monitor', 18) +
            '<span class="sp-item-label">Screenshot</span></button>' +
          '<button type="button" class="sp-item" id="photoBtn" data-needs="takeServerPhoto"' +
            ' onclick="takeServerPhoto();closeSidePanel()">' + ico('camera', 18) +
            '<span class="sp-item-label">Photo (webcam)</span></button>' +
          '<button type="button" class="sp-item" id="camBtn" data-needs="toggleWebcamPip"' +
            ' onclick="toggleWebcamPip();closeSidePanel()">' + ico('video', 18) +
            '<span class="sp-item-label">Live video</span></button>' +
          '<div class="sp-divider"></div>' +
          '<div class="sp-item sp-theme" id="themeBtn" role="group" aria-label="Theme">' +
            '<svg class="cs-ico" id="themeIco" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"' +
            ' stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + P.theme + '</svg>' +
            '<span class="sp-item-label">Theme</span><span class="theme-seg">' +
            '<button type="button" data-theme-opt="system" onclick="setThemePref(\'system\')"' +
            ' title="Follow your system day-night setting">Auto</button>' +
            '<button type="button" data-theme-opt="light" onclick="setThemePref(\'light\')">Light</button>' +
            '<button type="button" data-theme-opt="dark" onclick="setThemePref(\'dark\')">Dark</button></span></div>' +
          '<div class="sp-item text-size-row">' + ico('text', 18) + '<span class="sp-item-label">Text size</span>' +
            '<span class="text-size-group" id="textSizeGroup" role="group" aria-label="Text size">' +
            '<button type="button" data-size="small" onclick="setTextSize(\'small\')" title="Small">S</button>' +
            '<button type="button" data-size="medium" onclick="setTextSize(\'medium\')" title="Medium">M</button>' +
            '<button type="button" data-size="large" onclick="setTextSize(\'large\')" title="Large">L</button></span></div>' +
          '<button type="button" class="sp-item" id="connectBtn" data-needs="openConnectSetup"' +
            ' onclick="openConnectSetup();closeSidePanel()">' + ico('plug', 18) +
            '<span class="sp-item-label">Connect AI model</span></button>' +
        '</div>' +
      '</div>';
  }

  // ── Mount ─────────────────────────────────────────────────────────────────
  var body = document.body;
  body.classList.add('cs', 'cs-page-' + (PAGE || 'other'));
  var holder = document.createElement('div');
  holder.innerHTML = sidebarHTML() + topHTML() + tabsHTML() + panelHTML();
  while (holder.firstChild) {
    if (script && script.parentNode === body) body.insertBefore(holder.firstChild, script);
    else body.insertBefore(holder.firstChild, body.firstChild);
  }

  // ── Sidebar: rail (desktop / tablet) and drawer (phone) ──────────────────
  var tabletWide = false;  // 768-1023px start as the rail; an expand lasts for this page view
  function applyRail() {
    var w = window.innerWidth;
    var rail = w >= 1024 ? lsGet('codec-sidebar') === 'rail' : (w >= 768 ? !tabletWide : false);
    body.classList.toggle('cs-rail', rail);
    var btn = document.querySelector('.cs-collapse');
    if (btn) {
      var label = rail ? 'Expand sidebar' : 'Collapse sidebar';
      btn.setAttribute('aria-label', label);
      btn.title = label + ' (' + (KEY_HINT.indexOf('Cmd') === 0 ? 'Cmd' : 'Ctrl') + '+Shift+S)';
    }
    if (w >= 768) closeDrawer();
  }
  function toggleRail() {
    var w = window.innerWidth;
    if (w < 768) { if (body.classList.contains('cs-drawer')) closeDrawer(); else openDrawer(); return; }
    if (w >= 1024) lsSet('codec-sidebar', body.classList.contains('cs-rail') ? 'full' : 'rail');
    else tabletWide = !tabletWide;
    applyRail();
  }
  function openDrawer() {
    if (!isPhone()) return;
    body.classList.add('cs-drawer');
    refreshHistory();
  }
  function closeDrawer() { body.classList.remove('cs-drawer'); }
  function focusSearch() {
    if (isPhone()) openDrawer();
    else if (body.classList.contains('cs-rail')) {
      if (window.innerWidth >= 1024) lsSet('codec-sidebar', 'full'); else tabletWide = true;
      applyRail();
    }
    var s = $('csSearch');
    if (s) { s.focus(); s.select(); }
  }
  applyRail();
  window.addEventListener('resize', applyRail);

  // ── Chat history ──────────────────────────────────────────────────────────
  var activeChat = null;
  var histTimer = null;
  function fmtTime(ts) {
    if (!ts) return '';
    var d = new Date(ts);
    if (isNaN(d)) return '';
    var now = new Date();
    if (d.toDateString() === now.toDateString()) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  }
  function histRow(id, title, ts, snippet) {
    return '<div class="cs-hrow' + (id === activeChat ? ' cs-on' : '') + '" role="listitem" data-sid="' + esc(id) + '">' +
      '<a class="cs-hmain" href="/chat#session=' + encodeURIComponent(id) + '" data-open="' + esc(id) + '">' +
        '<span class="cs-htitle">' + esc(title || 'New chat') + '</span>' +
        '<span class="cs-htime">' + esc(fmtTime(ts)) + '</span>' +
        (snippet ? '<span class="cs-hsnip">' + esc(snippet) + '</span>' : '') +
      '</a>' +
      '<button type="button" class="cs-hdel" data-del="' + esc(id) + '" title="Delete" aria-label="Delete chat">' +
        ico('trash', 16) + '</button></div>';
  }
  function histMsg(text) { return '<div class="cs-hmsg">' + esc(text) + '</div>'; }
  function refreshHistory() {
    var el = $('csHist');
    if (!el) return;
    var q = ($('csSearch') || {}).value || '';
    if (q.trim().length >= 2) { runSearch(q.trim()); return; }
    fetch('/api/qchat/sessions').then(function (r) { return r.ok ? r.json() : []; }).then(function (data) {
      var head = $('csHistHead');
      if (head) head.textContent = 'Chats';
      if (!Array.isArray(data) || !data.length) { el.innerHTML = histMsg('No conversations yet.'); return; }
      el.innerHTML = data.map(function (s) { return histRow(s.id, s.title, s.updated_at); }).join('');
    }).catch(function () { el.innerHTML = histMsg('Could not load chats.'); });
  }
  function refreshHistorySoon() { clearTimeout(histTimer); histTimer = setTimeout(refreshHistory, 400); }
  function runSearch(q) {
    var el = $('csHist');
    fetch('/api/qchat/search?q=' + encodeURIComponent(q)).then(function (r) { return r.ok ? r.json() : []; })
      .then(function (data) {
        var head = $('csHistHead');
        if (head) head.textContent = 'Results';
        if (!Array.isArray(data) || !data.length) { el.innerHTML = histMsg('No chats match "' + q + '".'); return; }
        el.innerHTML = data.map(function (s) { return histRow(s.session_id, s.title, s.timestamp, s.snippet); }).join('');
      }).catch(function () { el.innerHTML = histMsg('Search failed.'); });
  }
  var searchTimer = null;
  var search = $('csSearch');
  if (search) {
    search.addEventListener('input', function () {
      clearTimeout(searchTimer);
      var q = search.value.trim();
      searchTimer = setTimeout(function () { if (q.length >= 2) runSearch(q); else refreshHistory(); }, 300);
    });
    search.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { search.value = ''; refreshHistory(); search.blur(); e.stopPropagation(); }
    });
  }
  function setActiveChat(sid) {
    activeChat = sid || null;
    var rows = document.querySelectorAll('.cs-hrow');
    for (var i = 0; i < rows.length; i++) rows[i].classList.toggle('cs-on', rows[i].getAttribute('data-sid') === activeChat);
  }
  function openChat(sid) {
    if (PAGE === 'chat' && typeof window.loadSession === 'function') {
      closeDrawer();
      window.loadSession(sid);
      setActiveChat(sid);
      return true;
    }
    return false;  // let the link go to /chat#session=<id>
  }
  function deleteChat(sid) {
    if (!window.confirm('Delete this conversation?')) return;
    fetch('/api/qchat/session/' + encodeURIComponent(sid), { method: 'DELETE' }).then(function () {
      if (PAGE === 'chat' && window.sessionId === sid && typeof window.startNewSession === 'function') {
        window.startNewSession();
      }
      refreshHistory();
    }).catch(function () { toast('Could not delete the chat.'); });
  }
  var hist = $('csHist');
  if (hist) {
    hist.addEventListener('click', function (e) {
      var del = e.target.closest('[data-del]');
      if (del) { e.preventDefault(); e.stopPropagation(); deleteChat(del.getAttribute('data-del')); return; }
      var open = e.target.closest('[data-open]');
      if (open && !(e.metaKey || e.ctrlKey || e.shiftKey) && openChat(open.getAttribute('data-open'))) e.preventDefault();
    });
  }
  function newChat() {
    closeDrawer();
    if (PAGE === 'chat' && typeof window.startNewSession === 'function') { window.startNewSession(); return; }
    window.location.href = '/chat#new';
  }

  // Settings from Home stays on the page (Home reads #settings itself elsewhere).
  var settingsLink = $('csSettings');
  if (settingsLink) {
    settingsLink.addEventListener('click', function (e) {
      if (PAGE === 'home' && typeof window.showTab === 'function') {
        e.preventDefault();
        closeDrawer();
        window.showTab('settings');
        try { history.replaceState(null, '', '/#settings'); } catch (err) { /* ignore */ }
      }
    });
  }

  // ── Quick settings panel ─────────────────────────────────────────────────
  function openSidePanel() {
    closeDrawer();
    var p = $('sidePanel'), o = $('sidePanelOverlay');
    if (p) p.classList.add('open');
    if (o) o.classList.add('open');
    if (typeof window.updateVoiceIcon === 'function') window.updateVoiceIcon();
    refreshWake();
  }
  function closeSidePanel() {
    var p = $('sidePanel'), o = $('sidePanelOverlay');
    if (p) p.classList.remove('open');
    if (o) o.classList.remove('open');
  }
  function voiceReplies() {
    var f = window.toggleVoiceReply || window.toggleVoice;
    if (typeof f === 'function') { f(); return; }
    lsSet('codec-voice', lsGet('codec-voice') === 'true' ? 'false' : 'true');
    if (typeof window.updateVoiceIcon === 'function') window.updateVoiceIcon();
  }

  var wakeOn = false;
  function setWakeBadge(on) {
    var st = $('wakeState');
    if (!st) return;
    st.textContent = on ? 'On' : 'Off';
    st.classList.toggle('on', !!on);
  }
  function refreshWake() {
    fetch('/api/config').then(function (r) { return r.json(); }).then(function (d) {
      wakeOn = !!(d && d.wake && d.wake.wake_word_enabled);
      setWakeBadge(wakeOn);
    }).catch(function () { /* leave the badge as it is */ });
  }
  function wakeWord() {
    var target = !wakeOn;
    setWakeBadge(target);  // optimistic
    var btn = $('wakeBtn');
    if (btn) btn.style.opacity = '0.5';
    fetch('/api/config', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ 'Wake Word': { wake_word_enabled: target } })
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (d && d.saved) {
        wakeOn = target;
        toast(target ? 'Wake word on: say "Hey CODEC"' : 'Wake word off: CODEC will not wake on its name');
      } else {
        setWakeBadge(wakeOn);
        toast('Could not save: ' + ((d && d.error) || 'unknown'));
      }
    }).catch(function () {
      setWakeBadge(wakeOn);
      toast('Could not save the wake-word setting.');
    }).then(function () { if (btn) btn.style.opacity = ''; });
  }

  // ── Theme: system / light / dark ─────────────────────────────────────────
  var THEMES = ['system', 'light', 'dark'];
  function themePref() { var t = lsGet('codec-theme'); return (t === 'light' || t === 'dark') ? t : 'system'; }
  function themeResolved(pref) {
    if (pref !== 'system') return pref;
    try { return matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'; } catch (e) { return 'dark'; }
  }
  function applyTheme() {
    var pref = themePref(), eff = themeResolved(pref);
    document.documentElement.setAttribute('data-theme', eff);
    var m = document.querySelector('meta[name="theme-color"]');
    if (m) m.content = eff === 'light' ? '#faf8f5' : '#121215';
    var opts = document.querySelectorAll('[data-theme-opt]');
    for (var i = 0; i < opts.length; i++) {
      opts[i].setAttribute('aria-pressed', String(opts[i].getAttribute('data-theme-opt') === pref));
    }
    try { window.dispatchEvent(new CustomEvent('codec-theme', { detail: { pref: pref, theme: eff } })); } catch (e) { /* old browser */ }
  }
  function setThemePref(v) {
    if (THEMES.indexOf(v) < 0) return;
    lsSet('codec-theme', v);
    applyTheme();
    toast(v === 'system' ? 'Theme follows your system' : 'Theme: ' + v);
  }
  function toggleTheme() { setThemePref(THEMES[(THEMES.indexOf(themePref()) + 1) % THEMES.length]); }
  try {
    matchMedia('(prefers-color-scheme: light)').addEventListener('change', function () {
      if (themePref() === 'system') applyTheme();
    });
  } catch (e) { /* old browser */ }

  // ── Text size: S / M / L scale the type tokens; the root is never zoomed ─
  var SIZES = { small: 0.93, medium: 1, large: 1.12 };
  function textSize() { var s = lsGet('codec-text-size'); return SIZES[s] ? s : 'medium'; }
  function applyTextSize() {
    var root = document.documentElement;
    root.style.setProperty('--fs-scale', String(SIZES[textSize()]));
    var g = $('textSizeGroup');
    if (g) {
      var bs = g.querySelectorAll('button');
      for (var i = 0; i < bs.length; i++) {
        var on = bs[i].getAttribute('data-size') === textSize();
        bs[i].classList.toggle('sel', on);
        bs[i].setAttribute('aria-pressed', String(on));
      }
    }
  }
  function setTextSize(s) { if (!SIZES[s]) return; lsSet('codec-text-size', s); applyTextSize(); }

  // Another CODEC tab changing a preference keeps this one in step.
  window.addEventListener('storage', function (e) {
    if (e.key === 'codec-theme') applyTheme();
    else if (e.key === 'codec-text-size') applyTextSize();
    else if (e.key === 'codec-sidebar') applyRail();
  });

  function lockSession() {
    fetch('/api/auth/logout', { method: 'POST' }).then(function () {
      document.cookie = 'codec_session=;path=/;max-age=0;SameSite=Lax' + (location.protocol === 'https:' ? ';Secure' : '');
      window.location.href = '/auth';
    });
  }

  // ── Inbox count (one 30-second poll per page, as before) ─────────────────
  function pollInbox() {
    fetch('/api/notifications/count').then(function (r) { return r.json(); }).then(function (d) {
      var n = (d && (d.unread || d.count)) || 0;
      ['csInboxBadge', 'csTabInboxBadge'].forEach(function (id) {
        var b = $(id);
        if (b) { b.textContent = n > 99 ? '99+' : String(n); b.hidden = n === 0; }
      });
    }).catch(function () { /* offline: keep the last count */ });
  }

  // ── Keys: Cmd/Ctrl+Shift+S sidebar, Cmd/Ctrl+K search, Esc closes ────────
  document.addEventListener('keydown', function (e) {
    var mod = e.metaKey || e.ctrlKey;
    var k = (e.key || '').toLowerCase();
    if (mod && e.shiftKey && !e.altKey && k === 's') { e.preventDefault(); toggleRail(); return; }
    // Cmd+K starts chords inside the Vibe code editor; leave it to the editor there.
    var inEditor = e.target && e.target.closest && e.target.closest('.monaco-editor');
    if (mod && !e.shiftKey && !e.altKey && k === 'k' && !inEditor) { e.preventDefault(); focusSearch(); return; }
    if (e.key === 'Escape') {
      var p = $('sidePanel');
      if (p && p.classList.contains('open')) { closeSidePanel(); e.stopImmediatePropagation(); return; }
      if (body.classList.contains('cs-drawer')) { closeDrawer(); e.stopImmediatePropagation(); }
    }
  }, true);

  // ── Public API and the global names the pages call ───────────────────────
  window.CodecShell = {
    page: PAGE,
    toggleRail: toggleRail, openDrawer: openDrawer, closeDrawer: closeDrawer, focusSearch: focusSearch,
    refreshHistory: refreshHistory, refreshHistorySoon: refreshHistorySoon, setActiveChat: setActiveChat,
    newChat: newChat, voiceReplies: voiceReplies, wakeWord: wakeWord, refreshWake: refreshWake, pollInbox: pollInbox
  };
  window.openSidePanel = openSidePanel;
  window.closeSidePanel = closeSidePanel;
  window.applyTheme = applyTheme;
  window.setThemePref = setThemePref;
  window.toggleTheme = toggleTheme;
  window.applyTextSize = applyTextSize;
  window.setTextSize = setTextSize;
  window.lockSession = lockSession;
  window.toggleWakeWord = wakeWord;
  window.refreshWakeState = refreshWake;

  applyTheme();
  applyTextSize();

  // After the page's own scripts: hide quick settings the page cannot run,
  // sync the voice badge, load the lists.
  function ready() {
    var needs = document.querySelectorAll('#sidePanel [data-needs]');
    for (var i = 0; i < needs.length; i++) {
      needs[i].hidden = typeof window[needs[i].getAttribute('data-needs')] !== 'function';
    }
    if (typeof window.updateVoiceIcon === 'function') window.updateVoiceIcon();
    refreshHistory();
    pollInbox();
    setInterval(pollInbox, 30000);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ready);
  else ready();
})();
