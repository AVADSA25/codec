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
  // One toast for the shell's own messages (history actions, wake word) and the pages' (CodecShell.toast),
  // with an optional link.
  var toastTimer = null;
  function toast(msg, url, action) {
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
    if (action && typeof action.run === 'function') {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'cs-toast-act';
      b.textContent = action.label || 'Open';
      b.addEventListener('click', function () { t.classList.remove('cs-show'); action.run(); });
      t.appendChild(document.createTextNode(' '));
      t.appendChild(b);
    }
    t.classList.add('cs-show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.classList.remove('cs-show'); }, url || action ? 8000 : 3200);
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
    plug: '<path d="M12 22v-5M9 8V2M15 8V2M18 8v5a6 6 0 0 1-12 0V8z"/>',
    sound: '<path d="M11 5 6 9H2v6h4l5 4z"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>',
    pause: '<rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/>',
    play: '<path d="M7 4v16l13-8z"/>',
    stop: '<rect x="5" y="5" width="14" height="14" rx="2"/>',
    tool: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.8-3.8a6 6 0 0 1-7.9 7.9l-6.9 6.9a2.1 2.1 0 0 1-3-3l6.9-6.9a6 6 0 0 1 7.9-7.9z"/>',
    cpu: '<rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
    keys: '<rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>',
    chev: '<path d="m6 9 6 6 6-6"/>',
    bulb: '<path d="M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.1V17h6v-.2c0-.8.4-1.6 1-2.1A7 7 0 0 0 12 2z"/>'
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
          '<a class="cs-row" href="/#inbox" id="csInbox" title="Inbox">' + ico('inbox') +
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

  // Read-aloud player strip (P2.8): under the top bar, in the page flow; shown while reading.
  function playerHTML() {
    return '<div class="cs-player" id="csPlayer" role="region" aria-label="Read aloud" hidden>' + ico('sound', 16) +
      '<span class="cs-player-label">Reading aloud</span><span class="cs-player-pos" id="csPlayerPos"></span>' +
      '<button type="button" class="cs-player-btn" id="csPlayerToggle" onclick="CodecShell.speech.toggle()"' +
      ' aria-label="Pause">' + ico('pause', 18) + '</button>' +
      '<button type="button" class="cs-player-btn cs-player-rate" id="csPlayerRate" onclick="CodecShell.speech.rate()"' +
      ' aria-label="Reading speed" title="Reading speed">1.0x</button>' +
      '<button type="button" class="cs-player-btn" id="csPlayerStop" onclick="CodecShell.speech.stop()"' +
      ' aria-label="Stop reading">' + ico('stop', 18) + '</button></div>';
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
      '<a class="cs-tab" href="/#inbox" id="csTabInbox">' + ico('inbox', 22) + '<span>Inbox</span>' +
      '<span class="cs-badge" id="csTabInboxBadge" hidden>0</span></a></nav>';
  }

  function panelHTML() {
    return '' +
      '<div class="side-panel-overlay" id="sidePanelOverlay" onclick="closeSidePanel()"></div>' +
      '<div class="side-panel" id="sidePanel" role="dialog" aria-modal="true" aria-label="Quick settings" data-dialog="open">' +
        '<div class="side-panel-header"><h2>Quick settings</h2>' +
          '<button type="button" class="cs-ibtn side-panel-close" onclick="closeSidePanel()" aria-label="Close quick settings" data-dialog-close>' +
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
  holder.innerHTML = sidebarHTML() + topHTML() + playerHTML() + tabsHTML() + panelHTML();
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
    var b = $('wakeBtn');
    if (b) b.setAttribute('aria-pressed', String(!!on));  // P2.13
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

  // ── Inbox (P3.2, docs/P3.2-DESIGN.md) ─────────────────────────────────────
  // One drawer (a bottom sheet on the phone) over GET /api/inbox, the only poller
  // left: approvals and questions (Needs you), reports, agent updates and
  // suggestions. The server picks each item's actions; this only posts what it
  // was given. Pages that show the same data (Home's question panel) subscribe.
  var INBOX = { el: null, items: [], counts: {}, filter: '', open: false, seen: null, subs: [], timer: null,
                loaded: false, full: {}, sig: '' };
  var INBOX_GROUPS = [['needs_you', 'Needs you'], ['reports', 'Reports'], ['agents', 'Agents'], ['suggestions', 'Suggestions']];

  function pollInbox() {
    clearTimeout(INBOX.timer);
    return fetch('/api/inbox').then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
      if (!d || !Array.isArray(d.items)) return;
      INBOX.items = d.items;
      INBOX.counts = d.counts || {};
      INBOX.loaded = true;
      inboxBadges();
      inboxNotifyNew();
      if (INBOX.open) inboxRender();
      inboxDrawMounts();
      INBOX.subs.forEach(function (fn) { try { fn(INBOX.items.slice(), INBOX.counts); } catch (e) { /* the page's own */ } });
    }).catch(function () { /* offline: keep what is shown */ }).then(inboxSchedule);
  }
  // 5 s while something needs the owner or the drawer is open, 15 s otherwise, 60 s in the background.
  function inboxSchedule() {
    clearTimeout(INBOX.timer);
    var ms = document.hidden ? 60000 : (INBOX.open || INBOX.counts.needs_you ? 5000 : 15000);
    INBOX.timer = setTimeout(pollInbox, ms);
  }
  document.addEventListener('visibilitychange', function () { if (!document.hidden) pollInbox(); });
  function inboxOnChange(fn) {
    if (typeof fn !== 'function') return;
    INBOX.subs.push(fn);
    if (INBOX.loaded) { try { fn(INBOX.items.slice(), INBOX.counts); } catch (e) { /* the page's own */ } }
  }

  // A page shows an Inbox group with the same cards and actions (P3.4: Today's Needs you).
  var MOUNTS = [];
  function inboxMount(el, group, onCount) {
    if (!el) return;
    el.addEventListener('click', inboxClick);
    MOUNTS.push({ el: el, group: /^(needs_you|reports|agents|suggestions)$/.test(group || '') ? group : 'needs_you',
                  sig: null, onCount: typeof onCount === 'function' ? onCount : null });
    if (INBOX.loaded) inboxDrawMounts();
  }
  function inboxDrawMounts() {
    MOUNTS.forEach(function (m) {
      var items = INBOX.items.filter(function (i) { return i.group === m.group; });
      var sig = items.map(function (i) { return [i.id, i.read, i.title, i.agent_status || ''].join(':'); }).join(',');
      if (sig !== m.sig) {
        m.sig = sig;
        var typed = {};  // an answer being typed survives the redraw
        m.el.querySelectorAll('.cs-qcard').forEach(function (q) { typed[q.getAttribute('data-qid')] = q.querySelector('.cs-q-text').value; });
        m.el.innerHTML = '';
        items.forEach(function (it) {
          var card = inboxCard(it);
          if (it.kind === 'question' && typed[it.question_id]) card.querySelector('.cs-q-text').value = typed[it.question_id];
          m.el.appendChild(card);
        });
      }
      if (m.onCount) { try { m.onCount(items.length); } catch (err) { /* the page's own */ } }
    });
  }

  function inboxBadges() {
    var n = INBOX.counts.badge || 0, urgent = (INBOX.counts.needs_you || 0) > 0;
    ['csInboxBadge', 'csTabInboxBadge'].forEach(function (id) {
      var b = $(id);
      if (!b) return;
      b.textContent = n > 99 ? '99+' : String(n);
      b.hidden = n === 0;
      b.classList.toggle('cs-badge-urgent', urgent);
    });
    ['csInbox', 'csTabInbox'].forEach(function (id) {
      var a = $(id);
      if (a) a.setAttribute('aria-label', n ? 'Inbox, ' + n + ' new' + (urgent ? ', something needs you' : '') : 'Inbox');
    });
  }

  // A Needs-you item that was not there at the last poll: a toast with Open, and a desktop
  // notification when the page is in the background and already may show one (never a prompt).
  function inboxNotifyNew() {
    var needs = INBOX.items.filter(function (i) { return i.group === 'needs_you'; });
    var first = INBOX.seen === null;
    if (first) INBOX.seen = {};
    var fresh = needs.filter(function (i) { return !INBOX.seen[i.id]; });
    needs.forEach(function (i) { INBOX.seen[i.id] = 1; });
    if (first || !fresh.length || INBOX.open) return;
    var it = fresh[0];
    toast('Needs you: ' + it.title, null, { label: 'Open', run: function () { inboxOpen('needs_you'); } });
    try {
      if (document.hidden && window.Notification && Notification.permission === 'granted') {
        var note = new Notification('CODEC: ' + it.title, { tag: 'codec-inbox-' + it.id, icon: '/favicon.png' });
        note.onclick = function () { window.focus(); inboxOpen('needs_you'); note.close(); };
      }
    } catch (e) { /* no notifications on this page */ }
  }

  function inboxEl() {
    if (INBOX.el) return INBOX.el;
    var w = document.createElement('div');
    w.className = 'cs-inbox-back';
    w.id = 'csInboxBack';
    w.hidden = true;
    w.innerHTML =
      '<section class="cs-inbox" id="csInboxPanel" role="dialog" aria-modal="true" aria-labelledby="csInboxTitle" data-dialog="open">' +
        '<div class="cs-inbox-head"><h2 id="csInboxTitle">Inbox</h2>' +
          '<button type="button" class="cs-ibtn" id="csInboxClose" aria-label="Close the inbox" data-dialog-close>' + ico('close', 18) + '</button></div>' +
        '<div class="cs-inbox-tabs" role="tablist" aria-label="Show">' + INBOX_GROUPS.map(function (g) {
          return '<button type="button" role="tab" class="cs-inbox-tab" id="csIbTab_' + g[0] + '" data-g="' + g[0] +
            '" aria-selected="false" aria-controls="csInboxList">' + g[1] + ' <span class="cs-inbox-n"></span></button>';
        }).join('') + '</div>' +
        '<div class="cs-inbox-list" id="csInboxList" role="tabpanel" tabindex="-1"></div>' +
        '<div class="cs-inbox-foot"><button type="button" class="cs-dbtn" id="csInboxAllRead">Mark all read</button></div>' +
      '</section>';
    document.body.appendChild(w);
    INBOX.el = w;
    w.addEventListener('click', function (e) { if (e.target === w) inboxClose(); });
    $('csInboxClose').addEventListener('click', inboxClose);
    var tabs = w.querySelector('.cs-inbox-tabs');
    tabs.addEventListener('click', function (e) {
      var b = e.target.closest('.cs-inbox-tab');
      if (b) { INBOX.filter = b.getAttribute('data-g'); INBOX.sig = ''; inboxRender(); }
    });
    tabs.addEventListener('keydown', function (e) {
      if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
      var all = [].slice.call(tabs.querySelectorAll('.cs-inbox-tab')), i = all.indexOf(document.activeElement);
      if (i < 0) return;
      e.preventDefault();
      var next = all[(i + (e.key === 'ArrowRight' ? 1 : all.length - 1)) % all.length];
      next.focus();
      next.click();
    });
    $('csInboxAllRead').addEventListener('click', inboxAllRead);
    $('csInboxList').addEventListener('click', inboxClick);
    dlgWatch();  // P2.13: while it shows, Tab stays inside, Esc closes it, focus goes back
    return w;
  }
  function inboxPick() {
    if (INBOX.counts.needs_you) return 'needs_you';
    for (var i = 1; i < INBOX_GROUPS.length; i++) if (INBOX.counts[INBOX_GROUPS[i][0]]) return INBOX_GROUPS[i][0];
    return 'reports';
  }
  function inboxOpen(filter) {
    var w = inboxEl();
    INBOX.filter = /^(needs_you|reports|agents|suggestions)$/.test(filter || '') ? filter : inboxPick();
    INBOX.sig = '';
    w.hidden = false;
    $('csInboxPanel').classList.add('open');
    INBOX.open = true;
    inboxRender();
    pollInbox();
  }
  function inboxClose() {
    if (!INBOX.el || !INBOX.open) return;
    $('csInboxPanel').classList.remove('open');
    INBOX.el.hidden = true;
    INBOX.open = false;
    inboxSchedule();
  }
  // /#inbox or /#inbox=reports on any page; Tasks' old #reports link too.
  function inboxFromHash() {
    var h = location.hash || '', m = h.match(/^#inbox(?:=(needs_you|reports|agents|suggestions))?$/);
    var old = PAGE === 'tasks' && h === '#reports';
    if (!m && !old) return;
    try { history.replaceState(null, '', location.pathname + location.search); } catch (e) { /* file:// */ }
    inboxOpen(old ? 'reports' : m[1]);
  }

  function inboxWhen(s) {
    var t = Date.parse(s || '');
    if (!t) return '';
    var m = Math.round((Date.now() - t) / 60000);
    if (m < 1) return 'now';
    if (m < 60) return m + ' min ago';
    if (m < 24 * 60) return Math.round(m / 60) + ' h ago';
    return new Date(t).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
  }
  function inboxItem(id) {
    for (var i = 0; i < INBOX.items.length; i++) if (INBOX.items[i].id === id) return INBOX.items[i];
    return null;
  }
  function inboxText(it) {
    var full = INBOX.full[it.id];
    if (full == null) return '<p class="cs-ib-text">' + esc(it.body) + (it.more ? '...' : '') + '</p>';
    var md = window.codecMarkdown;
    return '<div class="cs-ib-text cs-ib-full' + (md ? ' md' : '') + '">' + (md ? md.html(full) : esc(full)) + '</div>';
  }
  function inboxCard(it) {
    if (it.kind === 'question') return questionCard(it);
    var el = document.createElement('article');
    el.className = 'cs-ib cs-ib-' + it.kind + (it.read ? '' : ' cs-ib-unread') + (it.dangerous ? ' cs-ib-danger' : '');
    el.setAttribute('data-id', it.id);
    var head = '<div class="cs-ib-head"><span class="cs-ib-title">' + esc(it.title) + '</span>' +
      '<span class="cs-ib-when">' + esc(inboxWhen(it.created)) + '</span></div>';
    var acts = [];
    if (it.kind === 'approval') {
      el.innerHTML = head + '<code class="cs-ib-cmd">' + esc(it.command) + '</code>' +
        (it.body ? '<p class="cs-ib-text">This will: ' + esc(it.body) + '</p>' : '') +
        (it.expires_in != null ? '<p class="cs-ib-meta">Waits ' + it.expires_in + ' s more, then it is refused.</p>' : '');
      acts.push('<button type="button" class="cs-dbtn cs-dbtn-primary' + (it.dangerous ? ' cs-dbtn-danger' : '') + '" data-act="allow">Allow once</button>');
      acts.push('<button type="button" class="cs-dbtn" data-act="deny">Deny</button>');
    } else {
      var meta = it.kind === 'agent' && it.agent_id ? 'Agent ' + it.agent_id + (it.agent_status ? ': ' + it.agent_status.replace(/_/g, ' ') : '')
        : (it.status === 'running' ? 'Still running' : (it.status === 'error' || it.status === 'failed' ? 'Failed' : ''));
      el.innerHTML = head + (meta ? '<p class="cs-ib-meta">' + esc(meta) + '</p>' : '') + inboxText(it);
      if (INBOX.full[it.id] == null && (it.more || !it.read)) acts.push('<button type="button" class="cs-dbtn" data-act="open">Open</button>');
      if (it.doc_url) acts.push('<a class="cs-dbtn" data-act="link" href="' + esc(it.doc_url) + '" target="_blank" rel="noopener noreferrer">Open the document</a>');
      if (it.kind === 'report') acts.push('<a class="cs-dbtn" data-act="link" href="/chat#report=' + encodeURIComponent(it.id) + '">Discuss in chat</a>');
      (it.actions || []).forEach(function (a, i) {
        acts.push('<button type="button" class="cs-dbtn' + (i === 0 ? ' cs-dbtn-primary' : '') + '" data-act="run" data-i="' + i + '">' + esc(a.label) + '</button>');
      });
    }
    if (acts.length) el.insertAdjacentHTML('beforeend', '<div class="cs-ib-acts">' + acts.join('') + '</div>');
    return el;
  }
  function inboxRender() {
    if (!INBOX.el) return;
    var c = INBOX.counts, f = INBOX.filter;
    INBOX.el.querySelectorAll('.cs-inbox-tab').forEach(function (b) {
      var g = b.getAttribute('data-g'), n = c[g] || 0;
      b.setAttribute('aria-selected', String(g === f));
      b.tabIndex = g === f ? 0 : -1;
      b.querySelector('.cs-inbox-n').textContent = n ? String(n) : '';
    });
    $('csInboxList').setAttribute('aria-labelledby', 'csIbTab_' + f);
    var items = INBOX.items.filter(function (i) { return i.group === f; });
    $('csInboxAllRead').hidden = f === 'needs_you' || !items.some(function (i) { return !i.read; });
    // Redraw only when the list changed, so an answer being typed is not lost to the refresh.
    var sig = f + '|' + items.map(function (i) {
      return [i.id, i.read, i.title, i.agent_status || '', i.expires_in != null ? Math.floor(i.expires_in / 30) : '',
              INBOX.full[i.id] != null].join(':');
    }).join(',');
    if (sig === INBOX.sig) return;
    INBOX.sig = sig;
    var list = $('csInboxList'), typed = {};
    list.querySelectorAll('.cs-qcard').forEach(function (q) { typed[q.getAttribute('data-qid')] = q.querySelector('.cs-q-text').value; });
    list.innerHTML = '';
    if (!items.length) {
      list.innerHTML = '<p class="cs-inbox-empty">' + (f === 'needs_you' ? 'Nothing needs you right now.' : 'Nothing here yet.') + '</p>';
      return;
    }
    items.forEach(function (it) {
      var card = inboxCard(it);
      if (it.kind === 'question' && typed[it.question_id]) card.querySelector('.cs-q-text').value = typed[it.question_id];
      list.appendChild(card);
    });
    if (window.codecMarkdown && list.querySelector('.cs-ib-full.md')) window.codecMarkdown.enhance(list);
  }
  function inboxMarkRead(ids) {
    ids = ids.filter(function (id) { return /^(notif|auto)_/.test(id); });
    if (!ids.length) return Promise.resolve();
    INBOX.items.forEach(function (i) { if (ids.indexOf(i.id) >= 0) i.read = true; });
    return postJSON('/api/inbox/read', { ids: ids }).catch(function () { /* next poll shows the truth */ }).then(pollInbox);
  }
  function inboxAllRead() {
    inboxMarkRead(INBOX.items.filter(function (i) { return i.group === INBOX.filter && !i.read; }).map(function (i) { return i.id; }));
  }
  function inboxClick(e) {
    var b = e.target.closest('[data-act]');
    var card = e.target.closest('.cs-ib');
    if (!b || !card) return;
    if (b.tagName === 'A') { inboxMarkRead([card.getAttribute('data-id')]); return; }  // the link still opens
    var it = inboxItem(card.getAttribute('data-id'));
    if (!it) return;
    var act = b.getAttribute('data-act');
    if (act === 'allow' || act === 'deny') {
      b.disabled = true;
      postJSON('/api/approvals/' + encodeURIComponent(it.approval_id) + '/' + act, {}).then(function () {
        toast(act === 'allow' ? 'Allowed once.' : 'Denied.');
      }, function (err) { toast('Could not answer: ' + err.message); }).then(pollInbox);
    } else if (act === 'open') {
      b.disabled = true;
      fetch('/api/inbox/item/' + encodeURIComponent(it.id)).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
        INBOX.full[it.id] = d && typeof d.body === 'string' ? d.body : it.body;
        INBOX.sig = '';
        inboxRender();
      }).catch(function () { b.disabled = false; });
      if (!it.read) inboxMarkRead([it.id]);
    } else if (act === 'run') {
      var a = (it.actions || [])[+b.getAttribute('data-i')];
      if (!a) return;
      var go = function () {
        b.disabled = true;
        postJSON(a.endpoint, a.body || {}).then(function () { toast(a.label + ': done.'); },
          function (err) { toast(a.label + ' failed: ' + err.message); }).then(function () {
          if (!it.read) inboxMarkRead([it.id]); else pollInbox();
        });
      };
      if (!a.confirm) { go(); return; }
      var what = a.body && a.body.value ? a.body.value : '';
      ask({ title: 'Grant this permission?', message: 'Agent ' + (it.agent_id || '') + ' may then use: ' + what +
            '. It applies to this agent only.', confirm: 'Grant' }).then(function (ok) { if (ok) go(); });
    }
  }

  // ── One card for an ask_user question (P3.2): the Inbox and Home's Flash panel both use it ──
  function questionCard(q) {
    var qid = String(q.question_id || q.id || '');
    var card = document.createElement('article');
    card.className = 'cs-ib cs-qcard' + (q.strict ? ' cs-ib-danger' : '');
    card.setAttribute('data-qid', qid);
    card.setAttribute('data-id', q.id || qid);
    var opts = Array.isArray(q.options) ? q.options : [];
    card.innerHTML =
      '<div class="cs-ib-head"><span class="cs-ib-title">' + esc(q.agent ? q.agent + ' is asking' : 'CODEC is asking') + '</span>' +
        (q.deadline ? '<span class="cs-ib-when cs-q-left" data-deadline="' + esc(q.deadline) + '"></span>' : '') + '</div>' +
      '<p class="cs-ib-text">' + esc(q.body || q.question || '') + '</p>' +
      (q.strict ? '<p class="cs-q-warn">This cannot be undone. To go ahead, type the word <b>' + esc(q.verb || 'from its button') +
        '</b> or use its button.</p>' : '') +
      (opts.length ? '<div class="cs-ib-acts">' + opts.map(function (o) {
        return '<button type="button" class="cs-dbtn cs-q-opt">' + esc(o) + '</button>'; }).join('') + '</div>' : '') +
      '<div class="cs-q-row"><textarea class="cs-q-text" rows="1" aria-label="Your answer" placeholder="Type your answer"></textarea>' +
        '<button type="button" class="cs-dbtn cs-dbtn-primary cs-q-send">Send</button></div>' +
      '<p class="cs-q-status" role="status" aria-live="polite"></p>';
    var status = card.querySelector('.cs-q-status'), text = card.querySelector('.cs-q-text');
    function send(answer) {
      answer = String(answer || '').trim();
      if (!answer) { text.focus(); return; }
      status.className = 'cs-q-status';
      status.textContent = 'Sending...';
      postJSON('/api/agents/answer/' + encodeURIComponent(qid), { answer: answer, answered_via: 'pwa' }).then(function (d) {
        if (d && d.ok) {
          status.textContent = 'Answered.';
          card.classList.add('cs-q-done');
          setTimeout(pollInbox, 600);
        } else {
          status.className = 'cs-q-status err';
          status.textContent = 'Not accepted: ' + ((d && d.reason) || 'try again').replace(/_/g, ' ') +
            (d && d.remaining_attempts != null ? '. ' + d.remaining_attempts + ' tries left.' : '.');
        }
      }, function (err) {
        status.className = 'cs-q-status err';
        status.textContent = 'Could not send: ' + err.message;
        setTimeout(pollInbox, 600);
      });
    }
    card.querySelectorAll('.cs-q-opt').forEach(function (b) { b.addEventListener('click', function () { send(b.textContent); }); });
    card.querySelector('.cs-q-send').addEventListener('click', function () { send(text.value); });
    text.addEventListener('keydown', function (e) {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); send(text.value); }
    });
    setTimeout(qTick, 0);  // once the page has put the card in
    return card;
  }
  // Time left on every shown question, once a second while there are any.
  var QTICK = null;
  function qTick() {
    var all = document.querySelectorAll('.cs-q-left[data-deadline]');
    all.forEach(function (el) {
      var ms = Date.parse(el.getAttribute('data-deadline')) - Date.now();
      el.textContent = !(ms > 0) ? 'time is up' : (ms >= 60000 ? Math.floor(ms / 60000) + ' min ' : '') + Math.floor(ms % 60000 / 1000) + ' s left';
    });
    clearTimeout(QTICK);
    if (all.length || !INBOX.loaded) QTICK = setTimeout(qTick, 1000);
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
    if (s) { var on = dictMode() === 'browser'; s.textContent = on ? 'ON' : 'OFF'; s.classList.toggle('on', on); }
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

  // ── Read aloud (P2.8): the whole answer, sentence by sentence, through
  // POST /api/tts (CODEC's Kokoro on the Mac). The next two pieces are fetched
  // while one plays; the speed is the audio playback rate, per device.
  // speech text: begin (tests/test_tts_reader.py runs this block under Node)
  function speechText(md) {
    var s = String(md == null ? '' : md).replace(/\r\n?/g, '\n');
    s = s.replace(/```[\s\S]*?(?:```|$)/g, '\n').replace(/~~~[\s\S]*?(?:~~~|$)/g, '\n');   // code blocks
    s = s.replace(/<[^>\n]+>/g, ' ');                                                    // HTML tags
    s = s.replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1').replace(/\[([^\]]+)\]\([^)]*\)/g, '$1');     // images, links
    s = s.replace(/<?\bhttps?:\/\/[^\s>)]+>?/g, 'the link');                               // bare URLs
    s = s.replace(/`([^`\n]*)`/g, '$1');                                                 // inline code: its text
    s = s.replace(/^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*(?:\|[ \t]*:?-{3,}:?[ \t]*)*\|?[ \t]*$/gm, ''); // table rules
    s = s.replace(/^[ \t]*\|(.*)\|[ \t]*$/gm, function (m, row) {                          // table rows
      return row.split('|').map(function (c) { return c.trim(); }).filter(Boolean).join(', ') + '.';
    });
    s = s.replace(/^[ \t]*(?:[-*_][ \t]*){3,}$/gm, '');                                    // rules
    s = s.replace(/^[ \t]{0,3}#{1,6}[ \t]+/gm, '').replace(/^[ \t]*>[ \t]?/gm, '');         // headings, quotes
    s = s.replace(/^[ \t]*(?:[-*+]|\d+[.)])[ \t]+/gm, '');                                  // list markers
    s = s.replace(/\*\*([^*\n]+)\*\*/g, '$1').replace(/\*([^*\n]+)\*/g, '$1').replace(/~~([^~\n]+)~~/g, '$1');
    s = s.replace(/(^|[^\w])__([^_\n]+)__(?=[^\w]|$)/g, '$1$2').replace(/(^|[^\w])_([^_\n]+)_(?=[^\w]|$)/g, '$1$2');
    // One line per heading, item or paragraph, each ending in punctuation so it is read as a pause.
    return s.split('\n').map(function (l) { return l.replace(/[ \t]+/g, ' ').trim(); }).filter(Boolean)
      .map(function (l) { return /[.!?:;,\u2026]["')\]]*$/.test(l) ? l : l + '.'; }).join('\n');
  }
  // Sentence-sized pieces of at most `max` characters; short neighbours are merged.
  function speechChunks(text, max) {
    max = max || 280;
    var parts = [];
    String(text || '').split(/\n+/).forEach(function (line) {
      (line.match(/.*?[.!?\u2026]+["')\]]*(?=\s|$)|.+$/g) || []).forEach(function (s) {
        s = s.trim();
        while (s.length > max) {
          var cut = s.lastIndexOf(', ', max);
          if (cut < max / 2) cut = s.lastIndexOf(' ', max);
          if (cut < max / 2) cut = max;
          parts.push(s.slice(0, cut + 1).trim());
          s = s.slice(cut + 1).trim();
        }
        if (s) parts.push(s);
      });
    });
    var out = [];
    parts.forEach(function (p) {
      var last = out[out.length - 1];
      if (last !== undefined && last.length < 60 && last.length + 1 + p.length <= max) out[out.length - 1] = last + ' ' + p;
      else out.push(p);
    });
    return out;
  }
  // speech text: end
  var RATES = [0.9, 1, 1.1, 1.2, 1.3, 1.4];
  var SAY = { gen: 0, parts: [], i: 0, reqs: [], urls: [], audio: null, btn: null, paused: false,
              rate: RATES.indexOf(parseFloat(lsGet('codec-read-rate'))) >= 0 ? parseFloat(lsGet('codec-read-rate')) : 1 };
  function sayUI() {
    var bar = $('csPlayer');
    if (!bar) return;
    bar.hidden = !SAY.parts.length;
    var pos = $('csPlayerPos'), tog = $('csPlayerToggle'), rate = $('csPlayerRate');
    if (pos) pos.textContent = SAY.parts.length > 1 ? (Math.min(SAY.i + 1, SAY.parts.length) + ' of ' + SAY.parts.length) : '';
    if (tog) {
      tog.innerHTML = ico(SAY.paused ? 'play' : 'pause', 18);
      tog.setAttribute('aria-label', SAY.paused ? 'Resume' : 'Pause');
    }
    if (rate) rate.textContent = SAY.rate.toFixed(1) + 'x';
  }
  function sayFetch(i, gen) {
    if (!SAY.reqs[i]) {
      SAY.reqs[i] = fetch('/api/tts', { method: 'POST', headers: csrf({ 'Content-Type': 'application/json' }),
                                         body: JSON.stringify({ text: SAY.parts[i] }) }).then(function (r) {
        if (!r.ok) return r.json().catch(function () { return {}; }).then(function (d) { throw new Error(d.error || ('HTTP ' + r.status)); });
        return r.blob();
      }).then(function (b) {
        var u = URL.createObjectURL(b);
        if (gen !== SAY.gen) { URL.revokeObjectURL(u); throw new Error('stale'); }
        SAY.urls[i] = u;
        return u;
      });
    }
    return SAY.reqs[i];
  }
  function sayNext(gen) {
    if (gen !== SAY.gen) return;
    var i = SAY.i;
    if (i >= SAY.parts.length) { sayStop(); return; }
    sayUI();
    for (var k = i + 1; k <= i + 2 && k < SAY.parts.length; k++) sayFetch(k, gen).catch(function () { /* its turn retries */ });
    sayFetch(i, gen).then(function (url) {
      if (gen !== SAY.gen) return;
      if (!SAY.audio) SAY.audio = new Audio();
      var a = SAY.audio;
      a.onended = function () {
        if (gen !== SAY.gen) return;
        URL.revokeObjectURL(url);
        SAY.urls[i] = null;
        SAY.i = i + 1;
        sayNext(gen);
      };
      a.onerror = function () { if (gen === SAY.gen) { toast('Read aloud stopped: the audio could not play.'); sayStop(); } };
      a.src = url;
      a.defaultPlaybackRate = SAY.rate;
      a.playbackRate = SAY.rate;
      if (!SAY.paused) a.play().catch(function (e) {
        if (gen !== SAY.gen) return;
        // Autoplay without a tap (voice replies): wait for Resume instead of failing.
        if (e && e.name === 'NotAllowedError') { SAY.paused = true; sayUI(); return; }
        toast('Read aloud stopped: the audio could not play.');
        sayStop();
      });
    }, function (e) {
      if (gen !== SAY.gen || (e && e.message === 'stale')) return;
      toast('Read aloud stopped: ' + ((e && e.message) || 'the voice service failed.'));
      sayStop();
    });
  }
  function sayStop() {
    SAY.gen++;
    if (SAY.audio) {
      try { SAY.audio.pause(); } catch (e) { /* not started */ }
      SAY.audio.removeAttribute('src');
    }
    SAY.urls.forEach(function (u) { if (u) URL.revokeObjectURL(u); });
    SAY.urls = [];
    SAY.reqs = [];
    SAY.parts = [];
    SAY.i = 0;
    SAY.paused = false;
    if (SAY.btn) SAY.btn.classList.remove('speaking');
    SAY.btn = null;
    sayUI();
  }
  // Read `text` (Markdown) aloud from the start; `opts.btn` gets the 'speaking' class meanwhile.
  function speak(text, opts) {
    var parts = speechChunks(speechText(text));
    sayStop();
    if (!parts.length) return false;
    SAY.gen++;
    SAY.parts = parts;
    SAY.btn = (opts && opts.btn) || null;
    if (SAY.btn) SAY.btn.classList.add('speaking');
    sayNext(SAY.gen);
    return true;
  }
  function sayToggle() {
    if (!SAY.parts.length) return;
    SAY.paused = !SAY.paused;
    var a = SAY.audio;
    if (a) { if (SAY.paused) a.pause(); else if (a.getAttribute('src')) a.play().catch(function () { /* next tap */ }); }
    sayUI();
  }
  function sayRate() {
    SAY.rate = RATES[(RATES.indexOf(SAY.rate) + 1) % RATES.length];
    lsSet('codec-read-rate', String(SAY.rate));
    if (SAY.audio) { SAY.audio.defaultPlaybackRate = SAY.rate; SAY.audio.playbackRate = SAY.rate; }
    sayUI();
  }
  window.addEventListener('pagehide', sayStop);

  // ── Schedule this (P3.3): turns a question into a prompt job on a plain-language timing ──
  var SCHED = { el: null, timer: null, busy: false };
  function schedEl() {
    if (SCHED.el) return SCHED.el;
    var wrap = document.createElement('div');
    wrap.className = 'cs-dialog-backdrop';
    wrap.id = 'csSchedDialog';
    wrap.hidden = true;
    wrap.innerHTML =
      '<div class="cs-dialog" role="dialog" aria-modal="true" aria-labelledby="csSchedTitle">' +
        '<h2 id="csSchedTitle">Schedule this</h2>' +
        '<label class="cs-field"><span>Ask CODEC</span><textarea id="csSchedPrompt" maxlength="4000" rows="3"></textarea></label>' +
        '<label class="cs-field"><span>When</span><input id="csSchedWhen" autocomplete="off"' +
          ' placeholder="weekdays at 7:30, every 2 hours, mondays at 9am"></label>' +
        '<div class="cs-dialog-note" id="csSchedPreview" aria-live="polite"></div>' +
        '<label class="cs-check"><input type="checkbox" id="csSchedToday">Also put the result on Today</label>' +
        '<label class="cs-check"><input type="checkbox" id="csSchedSpeak">Say it on the Mac</label>' +
        '<label class="cs-check"><input type="checkbox" id="csSchedChanged">Only send it when the result changed</label>' +
        '<div class="cs-dialog-error" id="csSchedError" role="alert"></div>' +
        '<div class="cs-dialog-actions"><button type="button" class="cs-dbtn" id="csSchedCancel">Cancel</button>' +
          '<button type="button" class="cs-dbtn cs-dbtn-primary" id="csSchedSave">Schedule</button></div>' +
      '</div>';
    document.body.appendChild(wrap);
    wrap.addEventListener('click', function (e) { if (e.target === wrap) schedClose(); });
    wrap.addEventListener('keydown', function (e) { if (e.key === 'Escape') { e.stopPropagation(); schedClose(); } });
    $('csSchedCancel').addEventListener('click', schedClose);
    $('csSchedSave').addEventListener('click', schedSave);
    $('csSchedWhen').addEventListener('input', schedPreview);
    SCHED.el = wrap;
    return wrap;
  }
  function schedPreview() {
    clearTimeout(SCHED.timer);
    SCHED.timer = setTimeout(function () {
      var v = $('csSchedWhen').value.trim(), el = $('csSchedPreview');
      if (!v) { el.textContent = ''; el.classList.remove('err'); return; }
      postJSON('/api/schedules/parse', { when: v }).then(function (d) {
        el.classList.toggle('err', !d.ok);
        el.textContent = d.ok ? d.text + (d.next_run ? '. Next run ' + new Date(d.next_run).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' }) + '.' : '') : d.error;
      }).catch(function () { /* the save shows the error */ });
    }, 250);
  }
  function schedOpen(opts) {
    var el = schedEl();
    // A crew job (P2.6: Chat's "Schedule daily") runs that crew with the text as its task.
    SCHED.crew = opts && opts.kind === 'crew' && opts.crew ? { name: String(opts.crew), label: String(opts.label || opts.crew) } : null;
    $('csSchedTitle').textContent = SCHED.crew ? 'Schedule ' + SCHED.crew.label : 'Schedule this';
    el.querySelector('.cs-field span').textContent = SCHED.crew ? 'Task for the crew (optional)' : 'Ask CODEC';
    $('csSchedChanged').parentNode.hidden = !!SCHED.crew;
    $('csSchedPrompt').value = (opts && (opts.prompt || opts.topic)) || '';
    $('csSchedWhen').value = '';
    $('csSchedPreview').textContent = '';
    $('csSchedError').textContent = '';
    ['csSchedToday', 'csSchedSpeak', 'csSchedChanged'].forEach(function (id) { $(id).checked = false; });
    el.hidden = false;
    ($('csSchedPrompt').value ? $('csSchedWhen') : $('csSchedPrompt')).focus();
  }
  function schedClose() { if (SCHED.el) SCHED.el.hidden = true; }
  function schedSave() {
    if (SCHED.busy) return;
    var prompt = $('csSchedPrompt').value.trim(), err = $('csSchedError');
    var deliver = ['notification'];
    if ($('csSchedToday').checked) deliver.push('today');
    SCHED.busy = true;
    err.textContent = '';
    var job = SCHED.crew
      ? { kind: 'crew', crew: SCHED.crew.name, topic: prompt, when: $('csSchedWhen').value,
          label: (prompt ? SCHED.crew.label + ': ' + prompt.split('\n')[0] : SCHED.crew.label).slice(0, 80),
          deliver: deliver, speak: $('csSchedSpeak').checked, enabled: true }
      : { kind: 'prompt', prompt: prompt, when: $('csSchedWhen').value, deliver: deliver,
          label: prompt.split('\n')[0].slice(0, 80), speak: $('csSchedSpeak').checked,
          only_if_changed: $('csSchedChanged').checked, enabled: true };
    postJSON('/api/schedules', job).then(function (d) {
      schedClose();
      toast('Scheduled: ' + ((d.schedule && d.schedule.summary) || 'saved') + '. See it in Tasks.', '/tasks');
    }, function (e) { err.textContent = (e && e.message) || 'Could not schedule it.'; })
      .then(function () { SCHED.busy = false; });
  }

  // ── One modal for questions (P2.6; docs/P2.6-DESIGN.md) ─────────────────
  // CodecShell.ask({title, message, confirm, cancel, danger, input: {label, value,
  // placeholder, pattern, patternText, required, requiredText, inputmode, maxlength,
  // type}}) -> Promise: true/false, or the text/null when there is an input. It
  // replaces the browser's confirm and prompt dialogs: a bottom sheet on the phone, Enter confirms,
  // Esc and the backdrop cancel, focus goes back where it was.
  var ASK = { el: null, resolve: null, input: null, back: null };
  function askEl() {
    if (ASK.el) return ASK.el;
    var wrap = document.createElement('div');
    wrap.className = 'cs-dialog-backdrop';
    wrap.id = 'csAsk';
    wrap.hidden = true;
    wrap.innerHTML =
      '<div class="cs-dialog" role="alertdialog" aria-modal="true" aria-labelledby="csAskTitle" aria-describedby="csAskMsg">' +
        '<h2 id="csAskTitle"></h2><p class="cs-ask-msg" id="csAskMsg"></p>' +
        '<label class="cs-field" id="csAskField" hidden><span id="csAskLabel"></span><input id="csAskInput" autocomplete="off"></label>' +
        '<div class="cs-dialog-error" id="csAskError" role="alert"></div>' +
        '<div class="cs-dialog-actions"><button type="button" class="cs-dbtn" id="csAskCancel"></button>' +
          '<button type="button" class="cs-dbtn cs-dbtn-primary" id="csAskOk"></button></div>' +
      '</div>';
    document.body.appendChild(wrap);
    wrap.addEventListener('mousedown', function (e) { if (e.target === wrap) askDone(false); });
    wrap.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); askDone(false); return; }
      if (e.key === 'Enter' && e.target && e.target.id === 'csAskInput') { e.preventDefault(); askDone(true); return; }
      if (e.key === 'Tab') {  // keep Tab inside the dialog, even over another one (the Inbox's Grant)
        e.stopPropagation();
        var f = [].slice.call(wrap.querySelectorAll('input, button')).filter(function (x) { return x.offsetParent !== null; });
        var i = f.indexOf(document.activeElement);
        if (e.shiftKey && i <= 0) { e.preventDefault(); f[f.length - 1].focus(); } else if (!e.shiftKey && i === f.length - 1) { e.preventDefault(); f[0].focus(); }
      }
    });
    $('csAskCancel').addEventListener('click', function () { askDone(false); });
    $('csAskOk').addEventListener('click', function () { askDone(true); });
    ASK.el = wrap;
    return wrap;
  }
  function ask(opts) {
    opts = opts || {};
    var el = askEl(), inp = $('csAskInput'), f = opts.input || null;
    if (ASK.resolve) askDone(false);  // a newer question replaces an open one
    $('csAskTitle').textContent = opts.title || 'Are you sure?';
    $('csAskMsg').textContent = opts.message || '';
    $('csAskMsg').hidden = !opts.message;
    $('csAskField').hidden = !f;
    $('csAskError').textContent = '';
    if (f) {
      $('csAskLabel').textContent = f.label || '';
      inp.type = f.type || 'text';
      inp.value = f.value || '';
      inp.placeholder = f.placeholder || '';
      if (f.inputmode) inp.setAttribute('inputmode', f.inputmode); else inp.removeAttribute('inputmode');
      if (f.maxlength) inp.maxLength = f.maxlength; else inp.removeAttribute('maxlength');
    }
    $('csAskOk').textContent = opts.confirm || 'OK';
    $('csAskOk').classList.toggle('cs-dbtn-danger', !!opts.danger);
    $('csAskCancel').textContent = opts.cancel || 'Cancel';
    ASK.input = f;
    ASK.back = document.activeElement;
    el.hidden = false;
    // A destructive question starts on Cancel; a question with a field, in the field.
    (f ? inp : (opts.danger ? $('csAskCancel') : $('csAskOk'))).focus();
    return new Promise(function (resolve) { ASK.resolve = resolve; });
  }
  function askDone(ok) {
    if (!ASK.resolve) return;
    var value = !!ok, f = ASK.input;
    if (f) {
      var v = $('csAskInput').value.trim(), err = $('csAskError');
      if (ok && f.required && !v) { err.textContent = f.requiredText || 'This is needed.'; $('csAskInput').focus(); return; }
      if (ok && f.pattern && v && !new RegExp('^(?:' + f.pattern + ')$').test(v)) {
        err.textContent = f.patternText || 'That does not look right.'; $('csAskInput').focus(); return;
      }
      value = ok ? v : null;
    }
    var done = ASK.resolve;
    ASK.resolve = null;
    ASK.input = null;
    var inside = ASK.el.contains(document.activeElement);
    ASK.el.hidden = true;
    try { if (ASK.back && ASK.back.focus) ASK.back.focus(); } catch (e) { /* the element is gone */ }
    // Focus must not stay on a hidden button when there was nothing to go back to.
    if (inside && ASK.el.contains(document.activeElement)) document.activeElement.blur();
    done(value);
  }

  // ── One styled picker over a native <select> (P2.6) ───────────────────────
  // CodecShell.menu(select, {className}) -> {sync, open, close, button, wrap}: a
  // button with the chosen option opens a listbox (Up/Down, Home/End, Enter or
  // Space, Esc, type to jump). The select stays in the page, hidden, as the source
  // of truth: picking sets its value and fires its `change`; code that changes its
  // options or attributes updates the button at once, and code that only sets its
  // value within half a second (one shared check; no property is overridden).
  var MENU = { open: null, all: [], timer: null };
  function menu(sel, opts) {
    if (!sel) return null;
    if (sel.__csMenu) return sel.__csMenu;
    opts = opts || {};
    var wrap = document.createElement('span');
    wrap.className = 'cs-menu-wrap';
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.id = (sel.id || 'csSel') + 'Btn';
    btn.className = 'cs-menu-btn' + (opts.className ? ' ' + opts.className : '');
    btn.setAttribute('aria-haspopup', 'listbox');
    btn.setAttribute('aria-expanded', 'false');
    btn.innerHTML = '<span class="cs-menu-label"></span>' + ico('chev', 14, 'cs-menu-chev');
    var list = document.createElement('div');
    list.className = 'cs-menu-list';
    list.id = btn.id + 'List';
    list.tabIndex = -1;
    list.hidden = true;
    list.setAttribute('role', 'listbox');
    list.setAttribute('aria-label', sel.getAttribute('aria-label') || sel.title || 'Choose');
    btn.setAttribute('aria-controls', list.id);
    sel.parentNode.insertBefore(wrap, sel);
    wrap.appendChild(btn);
    wrap.appendChild(sel);
    document.body.appendChild(list);
    sel.hidden = true;
    sel.style.setProperty('display', 'none', 'important');  // an inline display on the select would beat `hidden`
    sel.tabIndex = -1;
    var st = { active: 0, typed: '', typedAt: 0 };
    function items() { return [].slice.call(sel.options); }
    function sync() {
      var o = sel.options[sel.selectedIndex], lab = btn.querySelector('.cs-menu-label'), text = o ? o.textContent : '';
      if (lab.textContent !== text) lab.textContent = text;
      if (btn.title !== (sel.title || '')) btn.title = sel.title || '';
      if (btn.disabled !== !!sel.disabled) btn.disabled = !!sel.disabled;
      btn.classList.toggle('alt', sel.classList.contains('alt'));
    }
    function render() {
      list.innerHTML = '';
      var last = null;
      items().forEach(function (o, i) {
        var g = o.parentNode && o.parentNode.nodeName === 'OPTGROUP' ? o.parentNode.label : null;
        if (g && g !== last) {
          var h = document.createElement('div');
          h.className = 'cs-menu-group';
          h.setAttribute('role', 'presentation');
          h.textContent = g;
          list.appendChild(h);
        }
        last = g;
        var it = document.createElement('div');
        it.className = 'cs-menu-item' + (i === st.active ? ' cs-active' : '');
        it.id = list.id + '-' + i;
        it.setAttribute('role', 'option');
        it.setAttribute('aria-selected', String(i === sel.selectedIndex));
        if (o.disabled) it.setAttribute('aria-disabled', 'true');
        it.textContent = o.textContent;
        it.addEventListener('mousedown', function (e) { e.preventDefault(); });
        it.addEventListener('click', function () { choose(i); });
        list.appendChild(it);
      });
      list.setAttribute('aria-activedescendant', list.id + '-' + st.active);
      var a = $(list.id + '-' + st.active);
      if (a && a.scrollIntoView) a.scrollIntoView({ block: 'nearest' });
    }
    function place() {
      var r = btn.getBoundingClientRect();
      list.style.minWidth = Math.max(r.width, 160) + 'px';
      var h = Math.min(list.scrollHeight, 320), below = window.innerHeight - r.bottom;
      var top = (below >= h + 8 || below >= r.top) ? r.bottom + 4 : r.top - h - 4;
      list.style.top = Math.max(8, top) + 'px';
      list.style.left = Math.max(8, Math.min(r.left, window.innerWidth - list.offsetWidth - 8)) + 'px';
    }
    function open(o) {
      if (btn.disabled || !list.hidden) return;
      if (MENU.open && MENU.open !== api) MENU.open.close(false);
      st.onClose = o && o.onClose;  // P2.7: told once whether a new value was picked
      st.changed = false;
      st.active = Math.max(0, sel.selectedIndex);
      render();
      list.hidden = false;
      place();
      btn.setAttribute('aria-expanded', 'true');
      list.focus();
      MENU.open = api;
    }
    function close(refocus) {
      if (list.hidden) return;
      list.hidden = true;
      btn.setAttribute('aria-expanded', 'false');
      if (MENU.open === api) MENU.open = null;
      if (refocus) btn.focus();
      var cb = st.onClose;
      st.onClose = null;
      if (cb) { try { cb(st.changed); } catch (e) { /* the page's callback */ } }
    }
    function move(to) {
      var all = items(), n = all.length, i = to;
      for (var k = 0; k < n && all[(i + n) % n] && all[(i + n) % n].disabled; k++) i += to >= st.active ? 1 : -1;
      st.active = Math.max(0, Math.min(n - 1, i));
      render();
    }
    function choose(i) {
      var o = items()[i];
      if (!o || o.disabled) return;
      var changed = sel.selectedIndex !== i;
      sel.selectedIndex = i;
      st.changed = changed;
      sync();
      close(true);
      if (changed) sel.dispatchEvent(new Event('change', { bubbles: true }));
    }
    btn.addEventListener('click', function () { if (list.hidden) open(); else close(false); });
    btn.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
    });
    list.addEventListener('keydown', function (e) {
      var n = items().length;
      if (e.key === 'ArrowDown') { e.preventDefault(); move(st.active + 1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); move(st.active - 1); }
      else if (e.key === 'Home') { e.preventDefault(); move(0); }
      else if (e.key === 'End') { e.preventDefault(); move(n - 1); }
      else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); choose(st.active); }
      else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(true); }
      else if (e.key === 'Tab') { close(false); }
      else if (e.key.length === 1 && /\S/.test(e.key)) {  // type to jump
        var now = Date.now();
        st.typed = (now - st.typedAt < 700 ? st.typed : '') + e.key.toLowerCase();
        st.typedAt = now;
        var all = items();
        for (var k = 0; k < all.length; k++) {
          if (all[k].textContent.trim().toLowerCase().indexOf(st.typed) === 0) { move(k); break; }
        }
      }
    });
    sel.addEventListener('change', sync);
    new MutationObserver(sync).observe(sel, { childList: true, subtree: true, attributes: true,
                                              attributeFilter: ['disabled', 'class', 'title', 'selected'] });
    var api = { sync: sync, open: open, close: close, button: btn, wrap: wrap };
    sel.__csMenu = api;
    MENU.all.push(api);
    if (!MENU.timer) {
      MENU.timer = setInterval(function () { if (!document.hidden) MENU.all.forEach(function (m) { m.sync(); }); }, 500);
    }
    sync();
    return api;
  }
  document.addEventListener('mousedown', function (e) {
    if (MENU.open && !e.target.closest('.cs-menu-list, .cs-menu-btn')) MENU.open.close(false);
  });

  // ── Action menu (P2.7): a popover of commands at a button, e.g. a reply's
  // three-dot menu. items: [{label, icon, run, danger}]. Same look and keys as the
  // chat-history row menu: Up/Down, Home/End, Enter, Esc returns focus.
  var ACT = { el: null, btn: null, items: [] };
  function actionsClose(refocus) {
    if (!ACT.el || ACT.el.hidden) return;
    ACT.el.hidden = true;
    if (ACT.btn) {
      ACT.btn.setAttribute('aria-expanded', 'false');
      if (refocus) ACT.btn.focus();
    }
    ACT.btn = null;
  }
  function actions(btn, items) {
    if (!btn || !items || !items.length) return;
    if (ACT.el && !ACT.el.hidden && ACT.btn === btn) { actionsClose(true); return; }
    if (!ACT.el) {
      ACT.el = document.createElement('div');
      ACT.el.className = 'cs-pop';
      ACT.el.id = 'csActions';
      ACT.el.setAttribute('role', 'menu');
      ACT.el.hidden = true;
      body.appendChild(ACT.el);
      ACT.el.addEventListener('click', function (e) {
        var it = e.target.closest('[data-i]');
        if (!it) return;
        var item = ACT.items[+it.getAttribute('data-i')];
        actionsClose(false);
        if (item && typeof item.run === 'function') item.run();
      });
      ACT.el.addEventListener('keydown', function (e) {
        var all = [].slice.call(ACT.el.querySelectorAll('.cs-pop-item'));
        var i = all.indexOf(document.activeElement), next = null;
        if (e.key === 'ArrowDown') next = all[(i + 1) % all.length];
        else if (e.key === 'ArrowUp') next = all[(i + all.length - 1) % all.length];
        else if (e.key === 'Home') next = all[0];
        else if (e.key === 'End') next = all[all.length - 1];
        else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); actionsClose(true); return; }
        else if (e.key === 'Tab') { actionsClose(false); return; }
        if (next) { e.preventDefault(); next.focus(); }
      });
      document.addEventListener('mousedown', function (e) {
        if (ACT.el && !ACT.el.hidden && !ACT.el.contains(e.target) && !(ACT.btn && ACT.btn.contains(e.target))) actionsClose(false);
      });
    }
    actionsClose(false);
    ACT.items = items;
    ACT.el.innerHTML = items.map(function (it, i) {
      var lab = document.createElement('span');
      lab.textContent = it.label;
      return '<button type="button" role="menuitem" class="cs-pop-item' + (it.danger ? ' cs-danger' : '') + '" data-i="' + i + '">' +
        (it.icon ? ico(it.icon, 18) : '') + lab.outerHTML + '</button>';
    }).join('');
    ACT.btn = btn;
    btn.setAttribute('aria-haspopup', 'menu');
    btn.setAttribute('aria-expanded', 'true');
    ACT.el.hidden = false;
    var r = btn.getBoundingClientRect(), w = ACT.el.offsetWidth, h = ACT.el.offsetHeight;
    ACT.el.style.left = Math.max(8, Math.min(r.right - w, window.innerWidth - w - 8)) + 'px';
    ACT.el.style.top = (r.bottom + h + 8 > window.innerHeight ? Math.max(8, r.top - h - 4) : r.bottom + 4) + 'px';
    var first = ACT.el.querySelector('.cs-pop-item');
    if (first) first.focus();
  }
  window.addEventListener('resize', function () { if (MENU.open) MENU.open.close(false); });
  document.addEventListener('scroll', function (e) {
    if (MENU.open && !(e.target && e.target.closest && e.target.closest('.cs-menu-list'))) MENU.open.close(false);
  }, true);

  // ── Command palette (P2.3; docs/P2.3-DESIGN.md) ─────────────────────────
  // Cmd/Ctrl+K on every page, over one list: chats (the loaded list, then
  // /api/qchat/search from two letters), pages, skills (into Chat as a pick),
  // models (POST /api/model), the Morning briefing, a new chat and the
  // shortcut sheet. The caret stays in the box; Up/Down, Enter, Esc.
  var PAL = { el: null, items: [], active: 0, mentions: null, models: null, found: [], q: '', timer: null, gen: 0 };
  var PAL_PAGES = NAV.map(function (n) { return { label: n.label, href: n.href, icon: n.icon }; }).concat([
    { label: 'Inbox', href: '/#inbox', icon: 'inbox', run: function () { inboxOpen(); } },
    { label: 'Settings', href: '/#settings', icon: 'sliders' },
    { label: 'Skills', href: '/#skills', icon: 'tool' },
    { label: 'Connections', href: '/#connector', icon: 'plug' },
    { label: 'Cortex', href: '/cortex', icon: 'monitor' },
    { label: 'Audit', href: '/#audit', icon: 'doc' }
  ]);
  function palEl() {
    if (PAL.el) return PAL.el;
    var wrap = document.createElement('div');
    wrap.className = 'cs-dialog-backdrop cs-pal-backdrop';
    wrap.id = 'csPalette';
    wrap.hidden = true;
    wrap.innerHTML =
      '<div class="cs-pal" role="dialog" aria-modal="true" aria-label="Command palette">' +
        '<div class="cs-pal-search">' + ico('search', 18) +
          '<input id="csPalInput" role="combobox" aria-expanded="true" aria-controls="csPalList" aria-autocomplete="list"' +
          ' autocomplete="off" spellcheck="false" placeholder="Search chats, pages, skills and models"></div>' +
        '<div class="cs-pal-list" id="csPalList" role="listbox" aria-label="Results"></div>' +
        '<div class="cs-pal-foot">Up and Down to move, Enter to open, Esc to close</div>' +
      '</div>';
    document.body.appendChild(wrap);
    wrap.addEventListener('mousedown', function (e) { if (e.target === wrap) palClose(); });
    var input = wrap.querySelector('input');
    input.addEventListener('input', function () { palQuery(input.value); });
    input.addEventListener('keydown', function (e) {
      var n = PAL.items.length;
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        if (n) { PAL.active = e.key === 'ArrowDown' ? (PAL.active + 1) % n : (PAL.active - 1 + n) % n; palRender(); }
      } else if (e.key === 'Enter' && !e.isComposing) {
        e.preventDefault();
        palRun(PAL.items[PAL.active]);
      } else if (e.key === 'Escape') {
        e.preventDefault(); e.stopPropagation(); palClose();
      }
    });
    PAL.el = wrap;
    return wrap;
  }
  function palMatch(q, s) { return !q || String(s || '').toLowerCase().indexOf(q) >= 0; }
  function palItems() {
    var q = PAL.q, out = [], seen = {};
    function add(group, it) { it.group = group; out.push(it); }
    var actions = [
      { name: 'New chat', icon: 'pen', run: function () { newChat(); } },
      { name: 'Start the Morning briefing', icon: 'play', run: palBriefing },
      { name: 'Keyboard shortcuts', desc: MOD + '+/', icon: 'keys', run: shortcutsOpen }
    ];
    var chats = [];
    if (!H.search) H.items.forEach(function (c) { if (c && c.id && palMatch(q, c.title)) chats.push(c); });
    if (q.length >= 2) PAL.found.forEach(function (c) { chats.push(c); });
    chats = chats.filter(function (c) { if (seen[c.id]) return false; seen[c.id] = 1; return true; }).slice(0, q ? 8 : 5);
    var pages = PAL_PAGES.filter(function (p) { return palMatch(q, p.label); });
    if (!q) {
      actions.forEach(function (a) { add('Actions', a); });
      pages.forEach(function (p) { add('Pages', { name: p.label, icon: p.icon, href: p.href }); });
      chats.forEach(function (c) { add('Recent chats', { name: c.title || 'Untitled chat', icon: 'chat', chat: c.id }); });
      return out;
    }
    chats.forEach(function (c) { add('Chats', { name: c.title || 'Untitled chat', desc: c.snippet || '', icon: 'chat', chat: c.id }); });
    pages.forEach(function (p) { add('Pages', { name: p.label, icon: p.icon, href: p.href }); });
    ((PAL.mentions && PAL.mentions.skills) || []).forEach(function (s) {
      if (palMatch(q, s.name) || palMatch(q, s.group)) add('Skills', { name: s.name, desc: s.description, icon: 'tool', skill: s.name });
    });
    ((PAL.models && PAL.models.models) || []).forEach(function (m) {
      var label = m.label || m.id;
      if (palMatch(q, 'model ' + label + ' ' + m.id)) {
        add('Models', { name: label, desc: m.active ? 'In use' : (m.role || 'Switch to this model'), icon: 'cpu', model: m });
      }
    });
    actions.forEach(function (a) { if (palMatch(q, a.name)) add('Actions', a); });
    return out;
  }
  function palQuery(v) {
    PAL.q = String(v || '').trim().toLowerCase();
    PAL.active = 0;
    palRender();
    clearTimeout(PAL.timer);
    if (PAL.q.length < 2) { PAL.found = []; return; }
    var gen = ++PAL.gen, q = PAL.q;
    PAL.timer = setTimeout(function () {
      fetch('/api/qchat/search?q=' + encodeURIComponent(q)).then(function (r) { return r.ok ? r.json() : []; })
        .then(function (data) {
          if (gen !== PAL.gen) return;
          PAL.found = (Array.isArray(data) ? data : []).map(function (s) {
            return { id: s.session_id, title: s.title, snippet: s.snippet };
          });
          palRender();
        }).catch(function () { /* the list stands */ });
    }, 200);
  }
  function palRender() {
    var list = $('csPalList'), input = $('csPalInput');
    if (!list) return;
    PAL.items = palItems();
    if (PAL.active >= PAL.items.length) PAL.active = 0;
    list.innerHTML = '';
    if (!PAL.items.length) {
      list.innerHTML = '<div class="cs-pal-empty">Nothing matches.</div>';
      input.removeAttribute('aria-activedescendant');
      return;
    }
    var last = '';
    PAL.items.forEach(function (it, i) {
      if (it.group !== last) {
        last = it.group;
        var g = document.createElement('div');
        g.className = 'cs-pal-group';
        g.setAttribute('role', 'presentation');
        g.textContent = it.group;
        list.appendChild(g);
      }
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'cs-pal-item';
      b.id = 'csPal-' + i;
      b.tabIndex = -1;
      b.setAttribute('role', 'option');
      b.setAttribute('aria-selected', String(i === PAL.active));
      b.innerHTML = ico(it.icon || 'dots', 16) + '<span class="cs-pal-name"></span><span class="cs-pal-desc"></span>';
      b.querySelector('.cs-pal-name').textContent = it.name;
      b.querySelector('.cs-pal-desc').textContent = it.desc || '';
      b.addEventListener('mousedown', function (e) { e.preventDefault(); });
      b.addEventListener('click', function () { palRun(it); });
      list.appendChild(b);
    });
    input.setAttribute('aria-activedescendant', 'csPal-' + PAL.active);
    var a = $('csPal-' + PAL.active);
    if (a) a.scrollIntoView({ block: 'nearest' });
  }
  function palOpen() {
    var el = palEl(), input = $('csPalInput');
    closeMenu();
    closeDrawer();
    el.hidden = false;
    input.value = '';
    PAL.found = [];
    palQuery('');
    input.focus();
    if (!H.items.length) loadPage(true);
    // Skills and models for the list; read on every open, so a new model or skill shows.
    fetch('/api/mentions').then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d) { PAL.mentions = d; if (!el.hidden) palRender(); } }).catch(function () {});
    fetch('/api/models').then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d) { PAL.models = d; if (!el.hidden) palRender(); } }).catch(function () {});
  }
  function palClose() { if (PAL.el) PAL.el.hidden = true; }
  function palRun(it) {
    if (!it) return;
    palClose();
    if (it.run) { it.run(); return; }
    if (it.href) { window.location.href = it.href; return; }
    if (it.chat) { if (!openChat(it.chat)) window.location.href = '/chat#session=' + encodeURIComponent(it.chat); return; }
    if (it.skill) {
      if (PAGE === 'chat' && typeof window.codecChatPick === 'function') { window.codecChatPick('skill', it.skill); return; }
      // Handed to Chat in this tab's storage, never in the link.
      try { sessionStorage.setItem('codec-chat-pick', JSON.stringify({ kind: 'skill', name: it.skill })); } catch (e) { /* blocked */ }
      window.location.href = '/chat';
      return;
    }
    if (it.model) palModel(it.model);
  }
  function palModel(m) {
    var label = m.label || m.id;
    if (m.active) { toast(label + ' is already in use.'); return; }
    toast('Switching the model to ' + label + '. This can take a minute.');
    postJSON('/api/model', { model: m.id }).then(function (d) {
      if (d && d.ok) {
        toast('Model: ' + label);
        if (typeof window.loadModels === 'function') window.loadModels();
      } else {
        toast('Could not switch: ' + ((d && d.error) || 'unknown'));
      }
    }, function (e) { toast('Could not switch: ' + ((e && e.message) || 'unknown')); });
  }
  function palBriefing() {
    postJSON('/api/briefing/run', {}).then(function () { toast('Morning briefing started'); },
      function (e) { toast((e && e.message) || 'Could not start the briefing'); });
  }

  // ── Keyboard shortcuts sheet (Cmd/Ctrl+/) ──
  var MOD = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '') ? 'Cmd' : 'Ctrl';
  var KEYS_EL = null;
  function shortcutsOpen() {
    if (!KEYS_EL) {
      var rows = [
        [MOD + '+K', 'Command palette: chats, pages, skills, models'],
        [MOD + '+/', 'This list'],
        [MOD + '+Shift+S', 'Show or hide the sidebar'],
        ['Enter', 'Send (Chat)'],
        ['Shift+Enter', 'New line (Chat)'],
        ['/', 'Commands, at the start of a Chat message'],
        ['@', 'Skills, crews, agents and MCP servers (Chat)'],
        ['Up', 'Edit your last message, when the Chat box is empty'],
        ['Esc', 'Close a menu or a dialog']
      ];
      KEYS_EL = document.createElement('div');
      KEYS_EL.className = 'cs-dialog-backdrop';
      KEYS_EL.id = 'csKeys';
      KEYS_EL.hidden = true;
      KEYS_EL.innerHTML = '<div class="cs-dialog" role="dialog" aria-modal="true" aria-labelledby="csKeysTitle" tabindex="-1">' +
        '<h2 id="csKeysTitle">Keyboard shortcuts</h2><table class="cs-keys"><tbody>' +
        rows.map(function (r) { return '<tr><td><kbd>' + esc(r[0]) + '</kbd></td><td>' + esc(r[1]) + '</td></tr>'; }).join('') +
        '</tbody></table><div class="cs-dialog-actions"><button type="button" class="cs-dbtn" id="csKeysClose">Close</button></div></div>';
      document.body.appendChild(KEYS_EL);
      KEYS_EL.addEventListener('mousedown', function (e) { if (e.target === KEYS_EL) shortcutsClose(); });
      KEYS_EL.addEventListener('keydown', function (e) { if (e.key === 'Escape') { e.stopPropagation(); shortcutsClose(); } });
      $('csKeysClose').addEventListener('click', shortcutsClose);
    }
    palClose();
    KEYS_EL.hidden = false;
    $('csKeysClose').focus();
  }
  function shortcutsClose() { if (KEYS_EL) KEYS_EL.hidden = true; }

  // ── Keys: Cmd/Ctrl+Shift+S sidebar, Cmd/Ctrl+K palette, Cmd/Ctrl+/ shortcuts, Esc closes ────────
  document.addEventListener('keydown', function (e) {
    var mod = e.metaKey || e.ctrlKey;
    var k = (e.key || '').toLowerCase();
    if (mod && e.shiftKey && !e.altKey && k === 's') { e.preventDefault(); toggleRail(); return; }
    // Cmd+K starts chords inside the Vibe code editor; leave it to the editor there.
    var inEditor = e.target && e.target.closest && e.target.closest('.monaco-editor');
    if (mod && !e.shiftKey && !e.altKey && k === 'k' && !inEditor) {
      e.preventDefault();
      if (PAL.el && !PAL.el.hidden) palClose(); else palOpen();
      return;
    }
    if (mod && !e.altKey && (k === '/' || e.code === 'Slash') && !inEditor) { e.preventDefault(); shortcutsOpen(); return; }
    if (e.key === 'Escape') {
      if (ASK.el && !ASK.el.hidden) { askDone(false); e.stopImmediatePropagation(); return; }
      if (MENU.open) { MENU.open.close(true); e.stopImmediatePropagation(); return; }
      if (PAL.el && !PAL.el.hidden) { palClose(); e.stopImmediatePropagation(); return; }
      if (KEYS_EL && !KEYS_EL.hidden) { shortcutsClose(); e.stopImmediatePropagation(); return; }
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
    inbox: { open: inboxOpen, close: inboxClose, refresh: pollInbox, onChange: inboxOnChange, mount: inboxMount,
             items: function () { return INBOX.items.slice(); } },
    questionCard: questionCard,
    install: install, toast: toast, palette: palOpen, shortcuts: shortcutsOpen, ask: ask, menu: menu, actions: actions,
    push: { support: pushSupport, subscription: currentSub, on: pushOn, off: pushOff, deviceId: deviceId,
            post: postJSON },
    dictation: { toggle: dictToggle, stop: dictStop, mode: dictMode, toggleMode: toggleDictMode,
                 state: function () { return DICT.state; } },
    speech: { speak: speak, stop: sayStop, toggle: sayToggle, rate: sayRate, text: speechText, chunks: speechChunks,
              reading: function () { return SAY.parts.length > 0; } },
    schedule: { open: schedOpen, close: schedClose, save: schedSave },
    watchDialogs: dlgWatch
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
  // ── Dialogs and clickable rows (P2.13, docs/P2.13-DESIGN.md) ─────────────
  // Any element marked data-dialog is a modal: while it shows, Tab stays inside it,
  // Esc presses its [data-dialog-close] button, and focus goes back where it was
  // when it closes. data-dialog="open" means "shown while it has class open";
  // an empty value means "shown while it is displayed". The page opens and
  // closes it as before; the shell only watches.
  var DLG = { stack: [], seen: [] };
  var FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]):not([type="hidden"]),select:not([disabled]),' +
    'textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
  function dlgShown(el) {
    if (!el.isConnected || el.hidden) return false;
    var cls = el.getAttribute('data-dialog');
    if (cls) return el.classList.contains(cls);
    var cs = window.getComputedStyle(el);
    return cs.display !== 'none' && cs.visibility !== 'hidden';
  }
  function dlgFocusables(el) {
    return [].slice.call(el.querySelectorAll(FOCUSABLE)).filter(function (f) {
      return f.getClientRects().length > 0 && !f.closest('[hidden]');
    });
  }
  function dlgSync(el) {
    var shown = dlgShown(el), i = DLG.stack.indexOf(el);
    if (shown && i < 0) {
      el.__csReturn = document.activeElement;
      DLG.stack.push(el);
      setTimeout(function () {
        if (!dlgShown(el) || el.contains(document.activeElement)) return;
        var f = dlgFocusables(el);
        (el.querySelector('[autofocus]') || f[0] || el).focus();
      }, 30);
    } else if (!shown && i >= 0) {
      DLG.stack.splice(i, 1);
      var back = el.__csReturn;
      el.__csReturn = null;
      if (back && back.isConnected && typeof back.focus === 'function' && (!document.activeElement ||
          document.activeElement === document.body || el.contains(document.activeElement))) back.focus();
    }
  }
  function dlgWatch() {
    var all = document.querySelectorAll('[data-dialog]');
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      if (DLG.seen.indexOf(el) >= 0) continue;
      DLG.seen.push(el);
      if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1');
      new MutationObserver((function (d) { return function () { dlgSync(d); }; })(el))
        .observe(el, { attributes: true, attributeFilter: ['class', 'style', 'hidden'] });
      dlgSync(el);
    }
  }
  document.addEventListener('keydown', function (e) {
    var top = DLG.stack[DLG.stack.length - 1];
    if (top && !dlgShown(top)) { dlgSync(top); top = DLG.stack[DLG.stack.length - 1]; }
    if (top && e.key === 'Tab') {
      var f = dlgFocusables(top);
      if (!f.length) { e.preventDefault(); top.focus(); return; }
      var first = f[0], last = f[f.length - 1], a = document.activeElement;
      if (e.shiftKey && (a === first || !top.contains(a))) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && (a === last || !top.contains(a))) { e.preventDefault(); first.focus(); }
      return;
    }
    if (top && e.key === 'Escape') {
      var x = top.querySelector('[data-dialog-close]');
      if (x) { e.preventDefault(); x.click(); }
      return;
    }
    // A clickable row that cannot be a <button> (it holds other controls) is
    // role="button" tabindex="0": Enter and Space press it like a button.
    var t = e.target;
    if ((e.key === 'Enter' || e.key === ' ') && t && t.getAttribute && t.getAttribute('role') === 'button' &&
        !/^(BUTTON|A|INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) {
      e.preventDefault();
      t.click();
    }
  });

  // Voice replies: each page's updateVoiceIcon writes ON / OFF into #voiceState;
  // the button's aria-pressed follows it (P2.13).
  function syncVoicePressed() {
    var st = $('voiceState'), b = $('voiceBtn');
    if (st && b) b.setAttribute('aria-pressed', String(st.textContent.trim() === 'ON'));
  }
  function ready() {
    dlgWatch();
    var vs = $('voiceState');
    if (vs) new MutationObserver(syncVoicePressed).observe(vs, { childList: true, characterData: true, subtree: true });
    syncVoicePressed();
    var needs = document.querySelectorAll('#sidePanel [data-needs]');
    for (var i = 0; i < needs.length; i++) {
      needs[i].hidden = typeof window[needs[i].getAttribute('data-needs')] !== 'function';
    }
    if (typeof window.updateVoiceIcon === 'function') window.updateVoiceIcon();
    refreshHistory();
    pollInbox();
    inboxFromHash();
    window.addEventListener('hashchange', inboxFromHash);
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
