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
  // Preferences live in localStorage; when storage is blocked (private mode, a
  // hardened profile) they still hold for this page view.
  var mem = {};
  function lsGet(k) {
    try { var v = localStorage.getItem(k); return v === null && k in mem ? mem[k] : v; } catch (e) { return k in mem ? mem[k] : null; }
  }
  function lsSet(k, v) { mem[k] = v; try { localStorage.setItem(k, v); } catch (e) { /* blocked: kept in memory */ } }
  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function $(id) { return document.getElementById(id); }
  function isPhone() { return window.innerWidth < 768; }
  // One toast for the shell's own messages (history actions, wake word), with an optional link.
  var toastTimer = null;
  function toast(msg, url) {
    var t = document.getElementById('csToast');
    if (!t) {
      t = document.createElement('div');
      t.id = 'csToast';
      t.className = 'cs-toast';
      t.setAttribute('role', 'status');
      document.body.appendChild(t);
    }
    t.textContent = msg;
    if (url && /^https:\/\//.test(url)) {
      var a = document.createElement('a');
      a.href = url;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.textContent = 'Open';
      t.appendChild(document.createTextNode(' '));
      t.appendChild(a);
    }
    t.classList.add('cs-show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.classList.remove('cs-show'); }, url ? 8000 : 3200);
  }

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
    dots: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    pin: '<path d="M12 17v5M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/>',
    archive: '<rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8M10 12h4"/>',
    back: '<path d="m15 18-6-6 6-6"/>',
    download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>',
    doc: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7z"/><path d="M14 2v4a2 2 0 0 0 2 2h4M16 13H8M16 17H8M10 9H8"/>',
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
        '<div class="cs-hist-head cs-label" id="csHistHead"><span id="csHistLabel">Chats</span>' +
          '<button type="button" class="cs-hlink" id="csSelectBtn" hidden>Select</button>' +
          '<button type="button" class="cs-hicon" id="csArchBtn" title="Archived chats" aria-label="Archived chats">' +
          ico('archive', 16) + '</button></div>' +
        '<div class="cs-hist" id="csHist" role="list"></div>' +
        '<div class="cs-selbar" id="csSelBar" hidden></div>' +
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
          '<button type="button" class="sp-item" id="dictBtn" hidden onclick="CodecShell.dictation.toggleMode()"' +
            ' title="Off: the mic records on this page and CODEC transcribes on your Mac">' + ico('mic', 18) +
            '<span class="sp-item-label">Browser dictation<span class="sp-hint">Uses your browser\'s cloud service</span>' +
            '</span><span class="sp-state" id="dictState"></span></button>' +
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
          '<button type="button" class="sp-item" id="installBtn" hidden onclick="CodecShell.install()">' +
            ico('download', 18) + '<span class="sp-item-label">Install CODEC</span></button>' +
          '<div class="sp-item sp-note" id="installHint" hidden>' + ico('download', 18) +
            '<span class="sp-item-label">To install on this phone: tap Share, then Add to Home Screen.</span></div>' +
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
  // P2.1 list; P2.2 (docs/P2.2-DESIGN.md): date groups with Pinned first, a row
  // menu (rename, pin, archive, export, Google Doc, delete), load more on scroll,
  // an archived view, and select mode with an in-page confirm. No native dialogs.
  var activeChat = null;
  var histTimer = null;
  var PAGE_SIZE = 30;
  var H = { items: [], offset: 0, done: false, loading: false, gen: 0, archived: false, search: '',
            select: false, sel: {}, confirm: null, rename: null };
  function fmtTime(ts) {
    if (!ts) return '';
    var d = new Date(ts);
    if (isNaN(d)) return '';
    var now = new Date();
    if (d.toDateString() === now.toDateString()) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  }
  function dayStart(d) { return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime(); }
  function groupOf(s) {
    if (s.pinned && !H.archived) return 'Pinned';
    var d = new Date(s.updated_at || s.created_at || 0);
    if (isNaN(d)) return 'Older';
    var today = dayStart(new Date()), day = dayStart(d);
    if (day >= today) return 'Today';
    if (day >= today - 864e5) return 'Yesterday';
    if (day >= today - 7 * 864e5) return 'Previous 7 days';
    return 'Older';
  }
  function findChat(id) { for (var i = 0; i < H.items.length; i++) if (H.items[i].id === id) return H.items[i]; return null; }
  function histMsg(text) { return '<div class="cs-hmsg">' + esc(text) + '</div>'; }
  function histRow(s) {
    var id = s.id, eid = esc(id);
    if (H.confirm && H.confirm.one === id) {
      return '<div class="cs-hrow cs-hask" role="listitem" data-sid="' + eid + '"><span class="cs-hq">Delete this chat?</span>' +
        '<button type="button" class="cs-hbtn cs-danger" data-act="one-yes">Delete</button>' +
        '<button type="button" class="cs-hbtn" data-act="one-no">Cancel</button></div>';
    }
    if (H.rename === id) {
      return '<div class="cs-hrow cs-on" role="listitem" data-sid="' + eid + '"><input class="cs-hrename" id="csRename"' +
        ' maxlength="60" value="' + esc(s.title || '') + '" aria-label="Chat name"></div>';
    }
    var picked = !!H.sel[id];
    return '<div class="cs-hrow' + (id === activeChat ? ' cs-on' : '') + (picked ? ' cs-picked' : '') +
        '" role="listitem" data-sid="' + eid + '">' +
      (H.select ? '<input type="checkbox" class="cs-hcheck" data-check="' + eid + '"' + (picked ? ' checked' : '') +
        ' aria-label="Select ' + esc(s.title || 'chat') + '">' : '') +
      '<a class="cs-hmain" href="/chat#session=' + encodeURIComponent(id) + '" data-open="' + esc(id) + '">' +
        '<span class="cs-htitle">' + esc(s.title || 'New chat') + '</span>' +
        '<span class="cs-htime">' + esc(fmtTime(s.updated_at)) + '</span>' +
        (s.snippet ? '<span class="cs-hsnip">' + esc(s.snippet) + '</span>' : '') +
      '</a>' +
      (H.select || H.search ? '' : '<button type="button" class="cs-hmore" data-menu="' + eid + '" aria-label="Chat options"' +
        ' aria-haspopup="menu" title="Options">' + ico('dots', 16) + '</button>') +
      '</div>';
  }
  function renderHistory() {
    var el = $('csHist');
    if (!el) return;
    var label = $('csHistLabel'), selBtn = $('csSelectBtn'), arcBtn = $('csArchBtn');
    if (label) label.textContent = H.search ? 'Results' : (H.archived ? 'Archived chats' : 'Chats');
    if (selBtn) { selBtn.hidden = !!H.search || !H.items.length; selBtn.textContent = H.select ? 'Done' : 'Select'; }
    if (arcBtn) {
      arcBtn.hidden = !!H.search || H.select;
      arcBtn.title = H.archived ? 'Back to chats' : 'Archived chats';
      arcBtn.setAttribute('aria-label', arcBtn.title);
      arcBtn.innerHTML = ico(H.archived ? 'back' : 'archive', 16);
    }
    var html = '', group = null;
    if (!H.items.length && !H.loading) {
      html += histMsg(H.search ? 'No chats match "' + H.search + '".' : (H.archived ? 'No archived chats.' : 'No conversations yet.'));
    }
    H.items.forEach(function (s) {
      if (!H.search) {
        var g = groupOf(s);
        if (g !== group) { group = g; html += '<div class="cs-group" role="presentation">' + g + '</div>'; }
      }
      html += histRow(s);
    });
    if (H.loading) html += histMsg('Loading...');
    var top = el.scrollTop;
    el.innerHTML = html;
    el.scrollTop = top;
    renderSelBar();
    if (H.rename) { var inp = $('csRename'); if (inp) { inp.focus(); inp.select(); } }
    // A tall window may show the whole first page: keep loading until it scrolls.
    if (!H.done && !H.loading && !H.search && el.clientHeight > 0 && el.scrollHeight <= el.clientHeight + 8) loadPage(false);
  }
  function renderSelBar() {
    var bar = $('csSelBar');
    if (!bar) return;
    if (!H.select) { bar.hidden = true; bar.innerHTML = ''; return; }
    var n = Object.keys(H.sel).length;
    bar.hidden = false;
    bar.innerHTML = H.confirm && H.confirm.many
      ? '<span class="cs-hq">Delete ' + n + (n === 1 ? ' chat' : ' chats') + '?</span>' +
        '<button type="button" class="cs-hbtn cs-danger" data-act="many-yes">Delete</button>' +
        '<button type="button" class="cs-hbtn" data-act="many-no">Cancel</button>'
      : '<span class="cs-hq">' + (n ? n + ' selected' : 'Select chats') + '</span>' +
        '<button type="button" class="cs-hbtn cs-danger" data-act="many-ask"' + (n ? '' : ' disabled') + '>Delete</button>' +
        '<button type="button" class="cs-hbtn" data-act="select-off">Cancel</button>';
  }
  // reset: start over, reloading as many chats as are shown now (so a refresh
  // after a save or a pin keeps the scroll place); otherwise the next page.
  function loadPage(reset) {
    var limit = PAGE_SIZE, offset = H.offset;
    if (reset) {
      H.gen++;
      limit = Math.min(Math.max(H.items.length, PAGE_SIZE), 100);
      offset = 0;
      H.done = false;
      H.loading = false;
      H.search = '';
    }
    if (H.loading || H.done) return;
    H.loading = true;
    var gen = H.gen;
    fetch('/api/qchat/sessions?offset=' + offset + '&limit=' + limit + '&archived=' + (H.archived ? 1 : 0))
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (data) {
        if (gen !== H.gen) return;
        data = Array.isArray(data) ? data : [];
        H.items = reset ? data : H.items.concat(data);
        H.offset = offset + data.length;
        H.done = data.length < limit;
        H.loading = false;
        var ids = {};
        H.items.forEach(function (s) { ids[s.id] = 1; });
        Object.keys(H.sel).forEach(function (k) { if (!ids[k]) delete H.sel[k]; });
        renderHistory();
      }).catch(function () {
        if (gen !== H.gen) return;
        H.loading = false;
        H.done = true;
        var el = $('csHist');
        if (el) el.innerHTML = histMsg('Could not load chats.');
      });
  }
  function refreshHistory() {
    if (H.rename || H.confirm) return;  // finish the rename or the delete first
    var q = (($('csSearch') || {}).value || '').trim();
    if (q.length >= 2) { runSearch(q); return; }
    loadPage(true);
  }
  function refreshHistorySoon() { clearTimeout(histTimer); histTimer = setTimeout(refreshHistory, 400); }
  function runSearch(q) {
    H.gen++;
    var gen = H.gen;
    H.search = q;
    H.select = false;
    H.sel = {};
    H.loading = true;
    fetch('/api/qchat/search?q=' + encodeURIComponent(q)).then(function (r) { return r.ok ? r.json() : []; })
      .then(function (data) {
        if (gen !== H.gen) return;
        H.items = (Array.isArray(data) ? data : []).map(function (s) {
          return { id: s.session_id, title: s.title, updated_at: s.timestamp, snippet: s.snippet };
        });
        H.loading = false;
        H.done = true;
        renderHistory();
      }).catch(function () {
        if (gen !== H.gen) return;
        H.loading = false;
        var el = $('csHist');
        if (el) el.innerHTML = histMsg('Search failed.');
      });
  }
  var searchTimer = null;
  var search = $('csSearch');
  if (search) {
    search.addEventListener('input', function () {
      clearTimeout(searchTimer);
      var q = search.value.trim();
      searchTimer = setTimeout(function () { if (q.length >= 2) runSearch(q); else loadPage(true); }, 300);
    });
    search.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { search.value = ''; loadPage(true); search.blur(); e.stopPropagation(); }
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
  function chatUrl(id) { return '/api/qchat/session/' + encodeURIComponent(id); }
  function patchChat(id, change) {
    return fetch(chatUrl(id), { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(change) })
      .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(d.error || r.status); return d; }); });
  }
  function afterDelete(ids) {
    if (PAGE === 'chat' && ids.indexOf(window.sessionId) >= 0 && typeof window.startNewSession === 'function') {
      window.startNewSession();
    }
  }
  function deleteChats(ids) {
    return Promise.all(ids.map(function (id) { return fetch(chatUrl(id), { method: 'DELETE' }); }))
      .then(function () { afterDelete(ids); })
      .catch(function () { toast('Could not delete every chat.'); });
  }
  function finishRename(save) {
    var id = H.rename, inp = $('csRename');
    if (!id) return;
    var s = findChat(id), title = inp ? inp.value.trim() : '';
    H.rename = null;
    if (!save || !s || !title || title === s.title) { renderHistory(); return; }
    s.title = title;
    renderHistory();
    patchChat(id, { title: title }).catch(function () { toast('Could not rename the chat.'); refreshHistory(); });
  }
  function download(id, format) {
    var a = document.createElement('a');
    a.href = chatUrl(id) + '/export?format=' + format;
    a.download = '';
    body.appendChild(a);
    a.click();
    a.remove();
  }
  function saveToDoc(id) {
    toast('Saving to Google Docs...');
    fetch(chatUrl(id) + '/gdoc', { method: 'POST' })
      .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(d.error || r.status); return d; }); })
      .then(function (d) { toast('Saved to Google Docs.', d.url); })
      .catch(function (e) { toast('Could not save: ' + e.message); });
  }

  // Row menu: one popover, positioned at the row's button.
  var pop = document.createElement('div');
  pop.className = 'cs-pop';
  pop.id = 'csPop';
  pop.setAttribute('role', 'menu');
  pop.hidden = true;
  body.appendChild(pop);
  function menuItem(act, icon, label, danger) {
    return '<button type="button" role="menuitem" class="cs-pop-item' + (danger ? ' cs-danger' : '') + '" data-act="' + act +
      '">' + ico(icon, 18) + '<span>' + label + '</span></button>';
  }
  function openMenu(btn, id) {
    var s = findChat(id);
    if (!s) return;
    closeMenu();
    pop.innerHTML = menuItem('rename', 'pen', 'Rename') +
      (H.archived ? '' : menuItem(s.pinned ? 'unpin' : 'pin', 'pin', s.pinned ? 'Unpin' : 'Pin')) +
      menuItem(s.archived ? 'unarchive' : 'archive', 'archive', s.archived ? 'Unarchive' : 'Archive') +
      '<div class="cs-pop-sep"></div>' +
      menuItem('md', 'download', 'Export as Markdown') + menuItem('json', 'download', 'Export as JSON') +
      menuItem('gdoc', 'doc', 'Save to Google Doc') +
      '<div class="cs-pop-sep"></div>' + menuItem('delete', 'trash', 'Delete', true);
    pop.setAttribute('data-sid', id);
    pop.hidden = false;
    var r = btn.getBoundingClientRect(), w = pop.offsetWidth, h = pop.offsetHeight;
    pop.style.left = Math.max(8, Math.min(r.right - w, window.innerWidth - w - 8)) + 'px';
    pop.style.top = (r.bottom + h + 8 > window.innerHeight ? Math.max(8, r.top - h - 4) : r.bottom + 4) + 'px';
    btn.setAttribute('aria-expanded', 'true');
    var first = pop.querySelector('.cs-pop-item');
    if (first) first.focus();
  }
  function closeMenu() {
    if (pop.hidden) return;
    pop.hidden = true;
    var b = document.querySelector('.cs-hmore[aria-expanded="true"]');
    if (b) b.setAttribute('aria-expanded', 'false');
  }
  pop.addEventListener('click', function (e) {
    var it = e.target.closest('[data-act]');
    if (!it) return;
    var id = pop.getAttribute('data-sid'), act = it.getAttribute('data-act');
    closeMenu();
    if (act === 'rename') { H.rename = id; renderHistory(); }
    else if (act === 'pin' || act === 'unpin') patchChat(id, { pinned: act === 'pin' }).then(refreshHistory, function () { toast('Could not pin the chat.'); });
    else if (act === 'archive' || act === 'unarchive') {
      patchChat(id, { archived: act === 'archive' }).then(function () {
        toast(act === 'archive' ? 'Chat archived.' : 'Chat moved back to your chats.');
        refreshHistory();
      }, function () { toast('Could not archive the chat.'); });
    }
    else if (act === 'md' || act === 'json') download(id, act);
    else if (act === 'gdoc') saveToDoc(id);
    else if (act === 'delete') { H.confirm = { one: id }; renderHistory(); }
  });
  pop.addEventListener('keydown', function (e) {
    var items = Array.prototype.slice.call(pop.querySelectorAll('.cs-pop-item'));
    var i = items.indexOf(document.activeElement);
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      var next = items[(i + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length];
      if (next) next.focus();
    }
  });
  document.addEventListener('mousedown', function (e) {
    if (!pop.hidden && !pop.contains(e.target) && !e.target.closest('.cs-hmore')) closeMenu();
  });

  var hist = $('csHist');
  if (hist) {
    hist.addEventListener('click', function (e) {
      var more = e.target.closest('[data-menu]');
      if (more) {
        e.preventDefault();
        if (!pop.hidden && pop.getAttribute('data-sid') === more.getAttribute('data-menu')) closeMenu();
        else openMenu(more, more.getAttribute('data-menu'));
        return;
      }
      var act = e.target.closest('[data-act]');
      if (act) {
        var a = act.getAttribute('data-act'), id = H.confirm && H.confirm.one;
        H.confirm = null;
        if (a === 'one-yes' && id) {
          H.items = H.items.filter(function (s) { return s.id !== id; });
          renderHistory();
          deleteChats([id]).then(refreshHistory);
        } else renderHistory();
        return;
      }
      if (H.select) {
        var row = e.target.closest('.cs-hrow');
        if (row) {
          e.preventDefault();
          var sid = row.getAttribute('data-sid');
          if (H.sel[sid]) delete H.sel[sid]; else H.sel[sid] = 1;
          renderHistory();
        }
        return;
      }
      var open = e.target.closest('[data-open]');
      if (open && !(e.metaKey || e.ctrlKey || e.shiftKey) && openChat(open.getAttribute('data-open'))) e.preventDefault();
    });
    hist.addEventListener('keydown', function (e) {
      if (e.target.id !== 'csRename') return;
      if (e.key === 'Enter') { e.preventDefault(); finishRename(true); }
      else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); finishRename(false); }
    });
    hist.addEventListener('focusout', function (e) { if (e.target.id === 'csRename') finishRename(true); });
    hist.addEventListener('scroll', function () {
      closeMenu();
      if (H.search || H.done || H.loading) return;
      if (hist.scrollTop + hist.clientHeight > hist.scrollHeight - 120) loadPage(false);
    });
  }
  var selBar = $('csSelBar');
  if (selBar) {
    selBar.addEventListener('click', function (e) {
      var act = e.target.closest('[data-act]');
      if (!act) return;
      var a = act.getAttribute('data-act');
      if (a === 'many-ask') H.confirm = { many: true };
      else if (a === 'many-no') H.confirm = null;
      else if (a === 'select-off') { H.select = false; H.sel = {}; H.confirm = null; }
      else if (a === 'many-yes') {
        var ids = Object.keys(H.sel);
        H.select = false;
        H.sel = {};
        H.confirm = null;
        H.items = H.items.filter(function (s) { return ids.indexOf(s.id) < 0; });
        renderHistory();
        deleteChats(ids).then(function () { toast(ids.length + (ids.length === 1 ? ' chat deleted.' : ' chats deleted.')); refreshHistory(); });
        return;
      }
      renderHistory();
    });
  }
  var selBtn = $('csSelectBtn');
  if (selBtn) selBtn.addEventListener('click', function () { H.select = !H.select; H.sel = {}; H.confirm = null; closeMenu(); renderHistory(); });
  var arcBtn = $('csArchBtn');
  if (arcBtn) {
    arcBtn.addEventListener('click', function () {
      H.archived = !H.archived;
      H.items = [];
      H.select = false;
      H.sel = {};
      H.confirm = null;
      closeMenu();
      loadPage(true);
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
    st.textContent = on ? 'ON' : 'OFF';
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

  // ── Install (P2.14): the browser's prompt where there is one, a hint on iOS ─
  var installEvt = null;
  function standalone() {
    try { return matchMedia('(display-mode: standalone)').matches || navigator.standalone === true; } catch (e) { return false; }
  }
  function isIOS() {
    return /iPhone|iPad|iPod/.test(navigator.userAgent || '') || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  }
  function syncInstall() {
    var b = $('installBtn'), hint = $('installHint');
    if (b) b.hidden = !installEvt || standalone();
    if (hint) hint.hidden = !isIOS() || standalone() || !!installEvt;
  }
  window.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault();  // no mini-infobar: Install lives in quick settings
    installEvt = e;
    window.deferredPrompt = e;
    syncInstall();
  });
  window.addEventListener('appinstalled', function () {
    installEvt = null;
    window.deferredPrompt = null;
    syncInstall();
    toast('CODEC is installed.');
  });
  function install() {
    if (!installEvt) return;
    var ev = installEvt;
    installEvt = null;
    window.deferredPrompt = null;
    closeSidePanel();
    ev.prompt();
    Promise.resolve(ev.userChoice).then(syncInstall, syncInstall);
  }

  // ── Phone notifications (P3.13): Web Push on this device ─────────────────
  // The Mac keeps the device list (routes/push.py). This device remembers that
  // the owner turned push on here, so a subscription the browser replaced or
  // dropped is re-sent or renewed on the next visit without a prompt (the
  // permission is already granted). A device removed in Settings stays removed.
  function csrf(h) {
    var m = document.cookie.match(/(?:^|;\s*)codec_csrf=([^;]+)/);
    if (m) h['x-csrf-token'] = m[1];
    return h;
  }
  function postJSON(url, data) {
    var h = csrf({ 'Content-Type': 'application/json' });
    return fetch(url, { method: 'POST', headers: h, body: JSON.stringify(data || {}) }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
        return d;
      });
    });
  }
  function keyBytes(b64u) {
    var s = String(b64u || '').replace(/-/g, '+').replace(/_/g, '/');
    s += '==='.slice(0, (4 - s.length % 4) % 4);
    var raw = atob(s), out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }
  function sameKey(sub, bytes) {
    try {
      var k = new Uint8Array(sub.options.applicationServerKey);
      if (k.length !== bytes.length) return false;
      for (var i = 0; i < k.length; i++) if (k[i] !== bytes[i]) return false;
      return true;
    } catch (e) { return true; /* the browser does not say: keep it */ }
  }
  function pushCapable() {
    return 'serviceWorker' in navigator && window.isSecureContext && 'PushManager' in window && 'Notification' in window;
  }
  function swReady() {
    return navigator.serviceWorker.register('/sw.js').then(function () { return navigator.serviceWorker.ready; });
  }
  function currentSub() {
    return swReady().then(function (reg) { return reg.pushManager.getSubscription(); });
  }
  // 'unsupported' | 'install-first' (iPhone or iPad outside the Home Screen app)
  // | 'blocked' | 'ready' (the browser can subscribe)
  function pushSupport() {
    if (!pushCapable()) return isIOS() && !standalone() && window.isSecureContext ? 'install-first' : 'unsupported';
    return Notification.permission === 'denied' ? 'blocked' : 'ready';
  }
  function askPermission() {
    if (Notification.permission === 'granted') return Promise.resolve('granted');
    return new Promise(function (resolve) {
      var p = Notification.requestPermission(resolve);  // older Safari: callback only
      if (p && typeof p.then === 'function') p.then(resolve, function () { resolve('default'); });
    });
  }
  function subscribeWith(reg, bytes) {
    return reg.pushManager.getSubscription().then(function (old) {
      if (old && sameKey(old, bytes)) return old;
      var drop = old ? old.unsubscribe().catch(function () {}) : Promise.resolve();
      return drop.then(function () {
        return reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: bytes });
      });
    });
  }
  function sendSub(sub) {
    return postJSON('/api/push/subscribe', { subscription: sub.toJSON() }).then(function (d) {
      lsSet('codec-push', '1');
      lsSet('codec-push-ep', sub.endpoint);
      return d;
    });
  }
  // Call straight from a click: the permission prompt has to come first.
  function pushOn(publicKey) {
    if (!pushCapable()) return Promise.reject(new Error('unsupported'));
    if (!publicKey) return Promise.reject(new Error('The Mac has no push key yet.'));
    return askPermission().then(function (perm) {
      if (perm !== 'granted') throw new Error(perm === 'denied' ? 'blocked' : 'dismissed');
      return swReady();
    }).then(function (reg) { return subscribeWith(reg, keyBytes(publicKey)); }).then(sendSub);
  }
  function pushOff() {
    lsSet('codec-push', '0');
    lsSet('codec-push-ep', '');
    if (!pushCapable()) return Promise.resolve({});
    return currentSub().then(function (sub) {
      if (!sub) return {};
      var ep = sub.endpoint;
      return sub.unsubscribe().catch(function () {}).then(function () {
        return postJSON('/api/push/unsubscribe', { endpoint: ep });
      });
    });
  }
  // The id routes/push.py gives a device: the first 12 hex digits of SHA-256(endpoint).
  function deviceId(endpoint) {
    if (!endpoint || !window.crypto || !crypto.subtle) return Promise.resolve('');
    return crypto.subtle.digest('SHA-256', new TextEncoder().encode(endpoint)).then(function (buf) {
      var b = new Uint8Array(buf), hex = '';
      for (var i = 0; i < 6; i++) hex += ('0' + b[i].toString(16)).slice(-2);
      return hex;
    });
  }
  function pushResync() {
    try {
      if (lsGet('codec-push') !== '1' || !pushCapable() || Notification.permission !== 'granted') return;
    } catch (e) { return; }
    swReady().then(function (reg) {
      return reg.pushManager.getSubscription().then(function (sub) {
        if (sub) return lsGet('codec-push-ep') === sub.endpoint ? null : sendSub(sub);
        return fetch('/api/push').then(function (r) { return r.json(); }).then(function (cfg) {
          if (cfg && cfg.public_key) return subscribeWith(reg, keyBytes(cfg.public_key)).then(sendSub);
        });
      });
    }).catch(function () { /* the next visit tries again */ });
  }

  // ── Dictation (P2.5): the mic buttons record here and CODEC's own Whisper
  // transcribes on the Mac (POST /api/transcribe). The browser's speech
  // service (Web Speech; Chrome sends the audio to Google) only when the owner
  // turns on Browser dictation in quick settings, per device. The text goes
  // into the input for review; nothing is sent by dictation.
  var DICT = { state: 'idle', btn: null, input: null, opts: {}, ph: null, rec: null, stream: null, chunks: [],
               ctx: null, raf: 0, sr: null };
  var DICT_SILENCE_MS = 2000, DICT_MAX_MS = 120000, DICT_QUIET_RMS = 0.02;
  function speechClass() { return window.SpeechRecognition || window.webkitSpeechRecognition || null; }
  function dictMode() { return lsGet('codec-dictation') === 'browser' && speechClass() ? 'browser' : 'local'; }
  function syncDict() {
    var b = $('dictBtn'), s = $('dictState');
    if (b) b.hidden = !speechClass();
    if (s) s.textContent = dictMode() === 'browser' ? 'On' : 'Off';
  }
  function toggleDictMode() {
    var browser = dictMode() !== 'browser';
    lsSet('codec-dictation', browser ? 'browser' : 'local');
    syncDict();
    toast(browser ? "Browser dictation on: the mic uses your browser's cloud service (Chrome sends the audio to Google)."
                  : 'Browser dictation off: dictation is transcribed on your Mac.');
  }
  function dictUI(state) {
    DICT.state = state;
    var b = DICT.btn, inp = DICT.input;
    if (b) {
      b.classList.toggle('recording', state === 'listening');
      b.classList.toggle('cs-mic-live', state === 'listening' && !DICT.sr);
      b.classList.toggle('cs-mic-busy', state === 'working');
      b.setAttribute('aria-pressed', state === 'listening' ? 'true' : 'false');
      if (state !== 'listening') b.style.removeProperty('--mic-level');
    }
    if (inp) {
      if (state === 'idle') { if (DICT.ph !== null) inp.placeholder = DICT.ph; }
      else inp.placeholder = state === 'listening' ? 'Listening... tap the mic to stop' : 'Transcribing...';
    }
    if (DICT.opts.onState) { try { DICT.opts.onState(state); } catch (e) { /* page hook */ } }
  }
  function dictInsert(text) {
    var inp = DICT.input;
    if (!inp || !text) return;
    var v = inp.value;
    inp.value = v + (v && !/\s$/.test(v) ? ' ' : '') + text;
    inp.dispatchEvent(new Event('input', { bubbles: true }));
    try { inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length); } catch (e) { /* not a text field */ }
  }
  function dictRelease() {
    if (DICT.raf) cancelAnimationFrame(DICT.raf);
    DICT.raf = 0;
    if (DICT.stream) DICT.stream.getTracks().forEach(function (t) { t.stop(); });
    DICT.stream = null;
    if (DICT.ctx) { try { DICT.ctx.close(); } catch (e) { /* already closed */ } }
    DICT.ctx = null;
  }
  function recordType() {
    if (!window.MediaRecorder || typeof MediaRecorder.isTypeSupported !== 'function') return '';
    var types = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus'];
    for (var i = 0; i < types.length; i++) if (MediaRecorder.isTypeSupported(types[i])) return types[i];
    return '';
  }
  // Level ring on the button; stops after DICT_SILENCE_MS of quiet once speech was heard.
  function dictMeter(stream) {
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    var ctx;
    try { ctx = new AC(); } catch (e) { return; }
    DICT.ctx = ctx;
    var an = ctx.createAnalyser();
    an.fftSize = 1024;
    ctx.createMediaStreamSource(stream).connect(an);
    var buf = new Float32Array(an.fftSize), started = Date.now(), heard = false, quietSince = 0;
    (function tick() {
      if (DICT.state !== 'listening' || DICT.ctx !== ctx) return;
      an.getFloatTimeDomainData(buf);
      var sum = 0;
      for (var i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
      var rms = Math.sqrt(sum / buf.length), now = Date.now();
      if (DICT.btn) DICT.btn.style.setProperty('--mic-level', Math.min(1, rms * 8).toFixed(2));
      if (rms > DICT_QUIET_RMS) { heard = true; quietSince = 0; }
      else if (heard && !quietSince) quietSince = now;
      if ((quietSince && now - quietSince > DICT_SILENCE_MS) || now - started > DICT_MAX_MS) { dictStop(); return; }
      DICT.raf = requestAnimationFrame(tick);
    })();
  }
  function dictUpload(type) {
    dictRelease();
    var blob = new Blob(DICT.chunks, { type: type });
    DICT.chunks = [];
    DICT.rec = null;
    if (blob.size < 1000) { dictUI('idle'); toast('Nothing was recorded.'); return; }
    var ext = /mp4|aac/.test(type) ? 'm4a' : /ogg/.test(type) ? 'ogg' : 'webm';
    var fd = new FormData();
    fd.append('file', blob, 'dictation.' + ext);
    fetch('/api/transcribe', { method: 'POST', headers: csrf({}), body: fd }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error(d.error || ('Dictation failed (HTTP ' + r.status + ').'));
        return d;
      });
    }).then(function (d) {
      dictUI('idle');
      if (d.text) dictInsert(d.text);
      else toast('Nothing clear was heard. Try again a little closer to the mic.');
    }, function (e) {
      dictUI('idle');
      toast(e && e.message ? e.message : 'Dictation failed.');
    });
  }
  function dictStop() {
    if (DICT.state !== 'listening') return;
    if (DICT.sr) { DICT.sr.stop(); return; }
    var rec = DICT.rec;
    dictUI('working');
    if (rec && rec.state !== 'inactive') rec.stop();
    else { dictRelease(); dictUI('idle'); }
  }
  function dictBrowser() {
    var SR = speechClass(), r = new SR(), inp = DICT.input, base = inp ? inp.value : '';
    DICT.sr = r;
    r.lang = navigator.language || 'en-US';
    r.interimResults = true;
    r.continuous = false;
    r.onresult = function (e) {
      var t = '';
      for (var i = 0; i < e.results.length; i++) t += e.results[i][0].transcript;
      if (!inp) return;
      inp.value = base + (base && !/\s$/.test(base) ? ' ' : '') + t;
      inp.dispatchEvent(new Event('input', { bubbles: true }));
    };
    r.onerror = function (e) { if (e.error !== 'no-speech' && e.error !== 'aborted') toast('Browser dictation: ' + e.error); };
    r.onend = function () { DICT.sr = null; dictUI('idle'); };
    dictUI('listening');
    try { r.start(); } catch (e) { DICT.sr = null; dictUI('idle'); }
  }
  function dictStart(btn, input, opts) {
    DICT.btn = btn || null;
    DICT.input = input || null;
    DICT.opts = opts || {};
    DICT.ph = input ? input.placeholder : null;
    if (dictMode() === 'browser') { dictBrowser(); return; }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
      toast('This browser cannot record audio here. Type instead.');
      return;
    }
    dictUI('listening');
    navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }).then(function (stream) {
      if (DICT.state !== 'listening') { stream.getTracks().forEach(function (t) { t.stop(); }); return; }
      DICT.stream = stream;
      DICT.chunks = [];
      var type = recordType(), rec;
      try { rec = type ? new MediaRecorder(stream, { mimeType: type }) : new MediaRecorder(stream); }
      catch (e) { dictRelease(); dictUI('idle'); toast('Could not start recording.'); return; }
      DICT.rec = rec;
      rec.ondataavailable = function (e) { if (e.data && e.data.size) DICT.chunks.push(e.data); };
      rec.onstop = function () { dictUpload(rec.mimeType || type || 'audio/webm'); };
      rec.start(250);
      dictMeter(stream);
    }, function (err) {
      dictUI('idle');
      var n = err && err.name;
      toast(n === 'NotAllowedError' || n === 'SecurityError' ? 'The microphone is blocked. Allow it for CODEC in the browser settings.'
          : n === 'NotFoundError' ? 'No microphone found.' : 'Could not start the microphone.');
    });
  }
  // The mic buttons call this: a first tap starts, a second tap stops.
  function dictToggle(btn, input, opts) {
    if (DICT.state === 'listening') { dictStop(); return; }
    if (DICT.state === 'working') return;
    dictStart(btn, input, opts);
  }
  window.addEventListener('pagehide', dictRelease);

  // ── Keys: Cmd/Ctrl+Shift+S sidebar, Cmd/Ctrl+K search, Esc closes ────────
  document.addEventListener('keydown', function (e) {
    var mod = e.metaKey || e.ctrlKey;
    var k = (e.key || '').toLowerCase();
    if (mod && e.shiftKey && !e.altKey && k === 's') { e.preventDefault(); toggleRail(); return; }
    // Cmd+K starts chords inside the Vibe code editor; leave it to the editor there.
    var inEditor = e.target && e.target.closest && e.target.closest('.monaco-editor');
    if (mod && !e.shiftKey && !e.altKey && k === 'k' && !inEditor) { e.preventDefault(); focusSearch(); return; }
    if (e.key === 'Escape') {
      if (!pop.hidden) { closeMenu(); e.stopImmediatePropagation(); return; }
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
    newChat: newChat, voiceReplies: voiceReplies, wakeWord: wakeWord, refreshWake: refreshWake, pollInbox: pollInbox,
    install: install,
    push: { support: pushSupport, subscription: currentSub, on: pushOn, off: pushOff, deviceId: deviceId,
            post: postJSON },
    dictation: { toggle: dictToggle, stop: dictStop, mode: dictMode, toggleMode: toggleDictMode,
                 state: function () { return DICT.state; } }
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
    syncInstall();
    // The offline shell (P2.14): static assets only, never /api (static/sw.js).
    if ('serviceWorker' in navigator && window.isSecureContext) {
      navigator.serviceWorker.register('/sw.js').catch(function () { /* not fatal */ });
    }
    pushResync();
    syncDict();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ready);
  else ready();
})();
