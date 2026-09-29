/* CODEC Markdown renderer (UI phase 1, PR-C).

   One renderer for Chat replies, Home (Flash) replies and Tasks report bodies:
   marked (GFM, a single newline stays a line break) -> DOMPurify -> DOM
   post-processing. Model output never reaches innerHTML without DOMPurify.
   When marked or DOMPurify did not load, the text is shown escaped, with its
   line breaks, never as HTML.

   Post-processing: each code block gets highlight.js colours and a header
   with its language, Copy and Download; each table gets a scrolling box with
   Copy (tab-separated text). These buttons are made here, after sanitizing,
   and bound with addEventListener, never with inline handlers.

   Math and diagrams (MATH; docs/MATH-DESIGN.md): $...$ and \(...\) inline,
   $$...$$, \[...\] and ```math display, drawn by KaTeX; ```mermaid blocks in a
   finished reply drawn by Mermaid and shown as an image. Both libraries load
   the first time a reply needs them, and both outputs pass through their own
   DOMPurify instance before they reach the page.

   Load after static/vendor/marked.umd.js, purify.min.js and highlight.min.js.
   API (window.codecMarkdown):
     html(text)        sanitized HTML string, for markup built as a string;
                       call enhance() on it once it is in the page
     render(el, text)  el gets the rendered text and class "md"
     enhance(root)     adds code and table boxes, math and diagrams inside
                       root's .md elements
     stream(el, opts)  {update(text), cancel()}: re-renders at most every
                       80 ms while a reply streams; render() on the same
                       element cancels it, so the final render always wins
     texHtml(tex, display)  KaTeX's sanitized HTML for a formula, or null
                       while KaTeX is not loaded
     safeSvg(svg, idPrefix) a Mermaid SVG sanitized and made self-contained
                       for an <img>, or '' */
(function () {
  'use strict';

  var THROTTLE_MS = 80;
  var AUTO_DETECT_MAX = 10000; // longer code without a language stays plain
  var AUTO_DETECT_MIN_RELEVANCE = 3; // below this, highlight.js is guessing
  // Detection picks among these only: across all bundled languages it called short
  // Python "lua" and HTML "php-template".
  var AUTO_DETECT_LANGS = ['python', 'javascript', 'typescript', 'bash', 'shell', 'json', 'yaml',
    'sql', 'xml', 'css', 'go', 'rust', 'java', 'c', 'cpp', 'csharp', 'ruby', 'php', 'swift',
    'kotlin', 'diff', 'ini', 'markdown'];
  var CACHE_MAX = 96;
  var MATH_MAX = 4000; // a longer formula stays as code
  var DIAGRAM_MAX = 20000; // a longer diagram stays as code

  // Vendored (static/vendor/SOURCES.md) and loaded on first need. The version
  // in the URL keeps an upgrade out of old caches.
  var LIBS = {
    katex: {global: 'katex', js: '/static/vendor/katex/katex.min.js?v=0.18.9',
      css: '/static/vendor/katex/katex.min.css?v=0.18.9'},
    mermaid: {global: 'mermaid', js: '/static/vendor/mermaid.min.js?v=12.0.0'}
  };

  // Pictures load only from Image mode's own files or inline data. Any other
  // address would be fetched the moment a reply renders, so a reply (or a web
  // page it quotes) could send data to another site without a click. Those
  // become links.
  var LOCAL_IMG = /^\/api\/image\/file\/[0-9a-f]{12}\/\d{2}\.png$/;
  var DATA_IMG = /^data:image\/(?:png|jpe?g|gif|webp);base64,[a-z0-9+\/=]+$/i;
  var LANG_CLASS = /^language-[\w.+#-]{1,40}$/;

  var PURIFY_CONFIG = {
    USE_PROFILES: {html: true}, // no SVG or MathML
    FORBID_TAGS: ['style', 'form', 'input', 'button', 'textarea', 'select', 'option',
      'iframe', 'object', 'embed', 'dialog', 'video', 'audio', 'source', 'track', 'picture'],
    // id/name/data-*: a reply must not collide with the page's own ids or its
    // data-act buttons. srcset/background/poster: they load addresses too.
    FORBID_ATTR: ['style', 'id', 'name', 'srcset', 'background', 'poster', 'autofocus'],
    ALLOW_DATA_ATTR: false
  };

  var ICON_COPY = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
  var ICON_DOWNLOAD = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/></svg>';

  // Download file extension by fence language; other short names are used as-is.
  var EXT = {
    python: 'py', py: 'py', javascript: 'js', js: 'js', jsx: 'jsx', typescript: 'ts', ts: 'ts',
    tsx: 'tsx', json: 'json', bash: 'sh', sh: 'sh', shell: 'sh', zsh: 'sh', console: 'sh',
    html: 'html', xml: 'xml', css: 'css', scss: 'scss', less: 'less', yaml: 'yml', yml: 'yml',
    markdown: 'md', md: 'md', sql: 'sql', go: 'go', golang: 'go', rust: 'rs', rs: 'rs',
    java: 'java', kotlin: 'kt', kt: 'kt', swift: 'swift', c: 'c', h: 'h', cpp: 'cpp',
    'c++': 'cpp', cc: 'cpp', csharp: 'cs', cs: 'cs', 'c#': 'cs', ruby: 'rb', rb: 'rb',
    php: 'php', 'php-template': 'php', perl: 'pl', lua: 'lua', r: 'r', diff: 'diff',
    patch: 'diff', ini: 'ini', toml: 'toml', graphql: 'graphql', objectivec: 'm',
    vbnet: 'vb', wasm: 'wat', makefile: 'mk', dockerfile: 'dockerfile',
    plaintext: 'txt', text: 'txt', txt: 'txt', 'python-repl': 'txt'
  };

  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function plain(text) {
    return esc(text).replace(/\r\n?|\n/g, '<br>');
  }

  // ── Math marks: $...$ and \(...\) inline; $$...$$ and \[...\] display ──
  // Money is not math: an inline $...$ may not start or end with a space or
  // hold a backtick, and its closing $ may not run into a letter or digit
  // ("$5 and $10", "$PATH:$HOME"). The mark is a <code> with a language-math
  // class, which the sanitizer keeps; KaTeX replaces it after sanitizing.
  function inlineMath(src) {
    var m = /^\$\$((?:\\[\s\S]|[^\\$])+?)\$\$/.exec(src);
    if (m) return {raw: m[0], text: m[1], display: true};
    m = /^\\\[([\s\S]+?)\\\]/.exec(src);
    if (m) return {raw: m[0], text: m[1], display: true};
    m = /^\\\(([\s\S]+?)\\\)/.exec(src);
    if (m) return {raw: m[0], text: m[1], display: false};
    m = /^\$((?:\\[\s\S]|[^\\$\n`])+?)\$/.exec(src);
    if (m && !/^\s|\s$/.test(m[1]) && !/^[A-Za-z0-9]/.test(src.charAt(m[0].length))) {
      return {raw: m[0], text: m[1], display: false};
    }
    return null;
  }
  var MATH_EXTENSIONS = [{
    name: 'mathBlock',
    level: 'block',
    start: function (src) { var m = /\n {0,3}(?:\$\$|\\\[)/.exec(src); return m ? m.index + 1 : undefined; },
    tokenizer: function (src) {
      var m = /^ {0,3}\$\$([\s\S]+?)\$\$ *(?:\n+|$)/.exec(src) || /^ {0,3}\\\[([\s\S]+?)\\\] *(?:\n+|$)/.exec(src);
      if (m) return {type: 'mathBlock', raw: m[0], text: m[1].trim()};
    },
    renderer: function (token) {
      return '<pre><code class="language-math">' + esc(token.text) + '</code></pre>\n';
    }
  }, {
    name: 'mathInline',
    level: 'inline',
    start: function (src) { var m = /\$|\\[([]/.exec(src); return m ? m.index : undefined; },
    tokenizer: function (src) {
      var t = inlineMath(src);
      if (t) return {type: 'mathInline', raw: t.raw, text: t.text, display: t.display};
    },
    renderer: function (token) {
      return '<code class="language-math-' + (token.display ? 'display' : 'inline') + '">' + esc(token.text) + '</code>';
    }
  }];

  // ── marked: one private instance, so page code sharing the global keeps its defaults ──
  var parser = null;
  function getParser() {
    if (parser) return parser;
    var M = window.marked;
    if (!M || typeof M.Marked !== 'function') return null;
    var p = new M.Marked({gfm: true, breaks: true});
    p.use({extensions: MATH_EXTENSIONS});
    p.use({renderer: {
      // The sanitizer drops <input>, so a task-list box is written out.
      checkbox: function (token) {
        return '<code>' + (token.checked ? '[x]' : '[ ]') + '</code> ';
      },
      image: function (token) {
        var href = String(token.href || '');
        if (LOCAL_IMG.test(href) || DATA_IMG.test(href)) return false; // marked's own <img>
        return '<a href="' + esc(href) + '">' + esc(token.text || href || 'image') + '</a>';
      }
    }});
    parser = p;
    return parser;
  }

  // ── DOMPurify: a private instance, so these hooks never touch another user ──
  var purifier = null;
  function afterAttributes(node) {
    var tag = node.nodeName;
    if (tag === 'A' || tag === 'AREA') {
      node.setAttribute('target', '_blank');
      node.setAttribute('rel', 'noopener noreferrer');
    } else if (tag === 'IMG') {
      var src = node.getAttribute('src') || '';
      if (!LOCAL_IMG.test(src) && !DATA_IMG.test(src)) node.removeAttribute('src');
    }
    // Only marked's language class survives: a reply cannot dress itself up
    // in the page's own classes (buttons, approval cards, bubbles).
    if (node.hasAttribute && node.hasAttribute('class') &&
        !(tag === 'CODE' && LANG_CLASS.test(node.getAttribute('class')))) {
      node.removeAttribute('class');
    }
  }
  function getPurifier() {
    if (purifier) return purifier;
    var D = window.DOMPurify;
    if (typeof D !== 'function' || !D.isSupported) return null;
    var p = D(window);
    if (!p || !p.isSupported || typeof p.sanitize !== 'function') return null;
    p.setConfig(PURIFY_CONFIG);
    p.addHook('afterSanitizeAttributes', afterAttributes);
    purifier = p;
    return purifier;
  }

  // ── KaTeX and Mermaid: loaded the first time a reply needs them ──
  var loading = {};
  function addTag(tag, url) {
    return new Promise(function (resolve, reject) {
      var el = document.createElement(tag);
      if (tag === 'script') { el.src = url; el.async = true; } else { el.rel = 'stylesheet'; el.href = url; }
      el.onload = function () { resolve(); };
      el.onerror = function () { reject(new Error('could not load ' + url)); };
      document.head.appendChild(el);
    });
  }
  // One promise per library; a failed load stays failed until the page reloads.
  function need(name) {
    if (loading[name]) return loading[name];
    var lib = LIBS[name];
    var waits = window[lib.global] ? [] : [addTag('script', lib.js)];
    if (lib.css) waits.push(addTag('link', lib.css));
    loading[name] = Promise.all(waits).then(function () {
      if (!window[lib.global]) throw new Error(name + ' did not load');
    });
    return loading[name];
  }

  // KaTeX's output has its own purifier: its classes and inline sizes stay,
  // MathML and the SVG of roots and arrows stay; ids, links and any url( in a
  // style go. trust:false already refuses \href, \url and \includegraphics.
  var mathPurifier = null;
  var STYLE_URL = /url\(|expression\(|@import|javascript:/i;
  function getMathPurifier() {
    if (mathPurifier) return mathPurifier;
    var D = window.DOMPurify;
    if (typeof D !== 'function' || !D.isSupported) return null;
    var p = D(window);
    if (!p || typeof p.sanitize !== 'function') return null;
    p.setConfig({
      USE_PROFILES: {html: true, svg: true, mathMl: true},
      ADD_TAGS: ['semantics', 'annotation'],
      FORBID_TAGS: PURIFY_CONFIG.FORBID_TAGS.concat(['a', 'img', 'image', 'use', 'foreignObject']),
      FORBID_ATTR: ['id', 'name', 'href', 'xlink:href', 'srcset', 'background', 'poster', 'autofocus'],
      ALLOW_DATA_ATTR: false
    });
    p.addHook('afterSanitizeAttributes', function (n) {
      if (n.hasAttribute && n.hasAttribute('style') && STYLE_URL.test(n.getAttribute('style'))) n.removeAttribute('style');
    });
    mathPurifier = p;
    return p;
  }
  var mathCache = new Map();
  function texHtml(tex, display) {
    var k = window.katex;
    tex = String(tex == null ? '' : tex);
    if (!k || typeof k.renderToString !== 'function' || !tex.trim() || tex.length > MATH_MAX) return null;
    var key = (display ? 'D' : 'I') + tex;
    if (mathCache.has(key)) {
      var hit = mathCache.get(key);
      mathCache.delete(key);
      mathCache.set(key, hit);
      return hit;
    }
    var out = null;
    try {
      var p = getMathPurifier();
      if (p) {
        out = p.sanitize(k.renderToString(tex, {displayMode: !!display, output: 'htmlAndMathml',
          throwOnError: false, trust: false, strict: 'ignore', maxSize: 20, maxExpand: 500}));
      }
    } catch (e) {
      out = null; // stays as code
    }
    mathCache.set(key, out);
    if (mathCache.size > CACHE_MAX * 2) mathCache.delete(mathCache.keys().next().value);
    return out;
  }

  // A Mermaid SVG, made safe to show as an image: no scripts, handlers,
  // foreignObject, images or outside links; url(...) and href only to the
  // diagram's own #ids; ids only with the diagram's own prefix. The size comes
  // from the viewBox, since an <img> needs one.
  var svgPurifier = null, svgPrefix = '';
  var OUTSIDE_URL = /url\(\s*['"]?\s*(?!#)/i;
  var SVG_CONFIG = {USE_PROFILES: {svg: true, svgFilters: true}, ADD_TAGS: ['style'],
    FORBID_TAGS: ['script', 'foreignObject', 'image', 'a', 'iframe'], ALLOW_DATA_ATTR: false,
    RETURN_DOM_FRAGMENT: true};
  function getSvgPurifier() {
    if (svgPurifier) return svgPurifier;
    var D = window.DOMPurify;
    if (typeof D !== 'function' || !D.isSupported) return null;
    var p = D(window);
    if (!p || typeof p.sanitize !== 'function') return null;
    p.addHook('uponSanitizeElement', function (n, data) {
      if (data.tagName === 'style' && n.textContent) {
        n.textContent = n.textContent.replace(/@import[^;]*;?/gi, '')
          .replace(/url\(\s*['"]?\s*(?!#)[^)]*\)/gi, 'none');
      }
    });
    p.addHook('afterSanitizeAttributes', function (n) {
      if (!n.attributes) return;
      for (var i = n.attributes.length - 1; i >= 0; i--) {
        var name = n.attributes[i].name, v = n.attributes[i].value, low = name.toLowerCase();
        if (/^on/.test(low) ||
            ((low === 'href' || low === 'xlink:href') && v.charAt(0) !== '#') ||
            (low === 'id' && (!svgPrefix || v.indexOf(svgPrefix) !== 0)) ||
            OUTSIDE_URL.test(v)) {
          n.removeAttribute(name);
        }
      }
    });
    svgPurifier = p;
    return p;
  }
  function safeSvg(svg, idPrefix) {
    var p = getSvgPurifier();
    if (!p || !svg) return '';
    svgPrefix = String(idPrefix || '');
    try {
      var frag = p.sanitize(String(svg), SVG_CONFIG);
      var root = frag && frag.firstElementChild;
      if (!root || String(root.nodeName).toLowerCase() !== 'svg') return '';
      var vb = String(root.getAttribute('viewBox') || '').trim().split(/[\s,]+/).map(Number);
      if (vb.length === 4 && vb[2] > 0 && vb[3] > 0) {
        root.setAttribute('width', String(Math.ceil(vb[2])));
        root.setAttribute('height', String(Math.ceil(vb[3])));
      }
      return new XMLSerializer().serializeToString(root);
    } catch (e) {
      return '';
    } finally {
      svgPrefix = '';
    }
  }

  function html(text) {
    var src = String(text == null ? '' : text);
    var p = getParser(), purify = getPurifier();
    if (!p || !purify) return plain(src);
    try {
      return purify.sanitize(p.parse(src));
    } catch (e) {
      return plain(src);
    }
  }

  // ── Toast + clipboard ──
  function toast(msg) {
    try { if (typeof window.showToast === 'function') window.showToast(msg); } catch (e) {}
  }
  // execCommand path for plain-http LAN access, where navigator.clipboard is
  // undefined. iOS Safari needs contentEditable, a Range selection and no
  // pointer-events:none; 16px stops it zooming. Same as the pages' _copyFallback.
  function _copyFallback(text) {
    try {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.contentEditable = 'true';
      ta.readOnly = false;
      ta.style.position = 'fixed';
      ta.style.top = '0';
      ta.style.left = '0';
      ta.style.width = '1px';
      ta.style.height = '1px';
      ta.style.padding = '0';
      ta.style.border = 'none';
      ta.style.outline = 'none';
      ta.style.boxShadow = 'none';
      ta.style.background = 'transparent';
      ta.style.fontSize = '16px';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      var sel = window.getSelection();
      var range = document.createRange();
      range.selectNodeContents(ta);
      sel.removeAllRanges();
      sel.addRange(range);
      ta.setSelectionRange(0, ta.value.length);
      ta.focus();
      var ok = document.execCommand('copy');
      sel.removeAllRanges();
      document.body.removeChild(ta);
      return ok;
    } catch (e) {
      return false;
    }
  }
  function copy(text, btn) {
    function ok() {
      if (btn) {
        btn.classList.add('copied');
        setTimeout(function () { btn.classList.remove('copied'); }, 1500);
      }
      toast('Copied');
    }
    function fail() {
      if (_copyFallback(text)) ok(); else toast('Copy failed');
    }
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(ok, fail);
        return;
      }
    } catch (e) {}
    fail();
  }

  function fileName(lang) {
    var l = String(lang || '').toLowerCase();
    var ext = Object.prototype.hasOwnProperty.call(EXT, l) ? EXT[l] : (/^[a-z0-9]{1,10}$/.test(l) ? l : 'txt');
    return 'snippet.' + ext;
  }
  function download(text, lang, name, type) {
    try {
      var url = URL.createObjectURL(new Blob([text], {type: type || 'text/plain;charset=utf-8'}));
      var a = document.createElement('a');
      a.href = url;
      a.download = name || fileName(lang);
      a.style.display = 'none';
      document.body.appendChild(a);
      a.click();
      setTimeout(function () { URL.revokeObjectURL(url); a.remove(); }, 1000);
    } catch (e) {
      toast('Download failed');
    }
  }

  // ── Post-processing ──
  function node(tag, cls) {
    var n = document.createElement(tag);
    n.className = cls;
    return n;
  }
  function iconButton(label, svg) {
    var b = node('button', 'md-btn');
    b.type = 'button';
    b.title = label;
    b.setAttribute('aria-label', label);
    b.innerHTML = svg; // constant markup from this file
    return b;
  }
  function langOf(code) {
    var m = /(?:^|\s)language-([\w.+#-]+)/.exec(code.className || '');
    return m ? m[1].toLowerCase() : '';
  }

  // Highlighted HTML by language + text. A streamed reply is re-rendered every
  // 80 ms, so a finished block is coloured once, not on every token.
  var cache = new Map();
  var hljsReady = false;
  function highlight(code, lang, streaming) {
    var h = window.hljs;
    if (!h || typeof h.highlightElement !== 'function') return lang;
    if (!hljsReady) {
      hljsReady = true;
      try { h.configure({languages: AUTO_DETECT_LANGS}); } catch (e) {}
    }
    if (lang && !h.getLanguage(lang)) return lang; // unknown language: plain, no console warning
    var text = code.textContent;
    var key = lang + '\u0000' + text;
    var hit = cache.get(key);
    if (hit) {
      cache.delete(key);
      cache.set(key, hit);
      if (hit.html !== null) { code.innerHTML = hit.html; code.className = hit.cls; }
      return hit.lang;
    }
    // Detection runs on the final render only, and not on very long text.
    if (!lang && (streaming || text.length > AUTO_DETECT_MAX)) return '';
    var shown = lang;
    try {
      h.highlightElement(code);
      if (!lang) {
        var r = code.result || {};
        if ((r.relevance || 0) >= AUTO_DETECT_MIN_RELEVANCE && r.language) {
          shown = String(r.language);
        } else {
          code.textContent = text; // a low-confidence guess reads worse than plain text
          code.className = '';
        }
      }
    } catch (e) {
      code.textContent = text; // stays plain, and is not retried on the next render
      shown = '';
    }
    cache.set(key, {html: shown ? code.innerHTML : null, cls: code.className, lang: shown || lang});
    if (cache.size > CACHE_MAX) cache.delete(cache.keys().next().value);
    return shown || lang;
  }

  function codeBlock(code, streaming) {
    var pre = code.parentNode;
    if (!pre || pre.nodeName !== 'PRE' || !pre.parentNode) return;
    if (pre.parentNode.classList && pre.parentNode.classList.contains('md-code')) return;
    var text = code.textContent.replace(/\n$/, '');
    var lang = highlight(code, langOf(code), streaming);
    var box = node('div', 'md-code');
    var head = node('div', 'md-head');
    var label = node('span', 'md-label');
    label.textContent = lang || 'text';
    var acts = node('span', 'md-acts');
    var copyBtn = iconButton('Copy code', ICON_COPY);
    var saveBtn = iconButton('Download code', ICON_DOWNLOAD);
    copyBtn.addEventListener('click', function () { copy(text, copyBtn); });
    saveBtn.addEventListener('click', function () { download(text, lang); });
    acts.appendChild(copyBtn);
    acts.appendChild(saveBtn);
    head.appendChild(label);
    head.appendChild(acts);
    pre.parentNode.insertBefore(box, pre);
    box.appendChild(head);
    box.appendChild(pre);
  }

  function tsv(table) {
    var rows = table.querySelectorAll('tr'), out = [];
    for (var i = 0; i < rows.length; i++) {
      var cells = rows[i].querySelectorAll('th,td'), line = [];
      for (var j = 0; j < cells.length; j++) line.push(cells[j].textContent.replace(/\s+/g, ' ').trim());
      out.push(line.join('\t'));
    }
    return out.join('\n');
  }

  function tableBox(table) {
    var parent = table.parentNode;
    if (!parent || (parent.classList && parent.classList.contains('md-table-scroll'))) return;
    var box = node('div', 'md-table');
    var head = node('div', 'md-head');
    var label = node('span', 'md-label');
    label.textContent = 'Table';
    var copyBtn = iconButton('Copy table', ICON_COPY);
    copyBtn.addEventListener('click', function () { copy(tsv(table), copyBtn); });
    var scroll = node('div', 'md-table-scroll');
    head.appendChild(label);
    head.appendChild(copyBtn);
    parent.insertBefore(box, table);
    box.appendChild(head);
    box.appendChild(scroll);
    scroll.appendChild(table);
  }

  // ── Math: each mark becomes KaTeX's output; without KaTeX it stays code ──
  var MATH_MARKS = 'code.language-math, code.language-math-inline, code.language-math-display';
  var mathFailed = false;
  function isMathBlock(code) {
    return code.classList && code.classList.contains('language-math');
  }
  function renderMath(scope) {
    var marks = scope.querySelectorAll(MATH_MARKS);
    for (var i = 0; i < marks.length; i++) {
      var mark = marks[i], block = isMathBlock(mark) && mark.parentNode && mark.parentNode.nodeName === 'PRE';
      var display = block || mark.classList.contains('language-math-display');
      var out = texHtml(mark.textContent, display);
      if (out === null) continue;
      var el = document.createElement(block ? 'div' : 'span');
      el.className = block ? 'md-math md-math-block' : (display ? 'md-math md-math-display' : 'md-math');
      el.innerHTML = out; // KaTeX's output, through the math purifier (texHtml)
      var target = block ? mark.parentNode : mark;
      if (block && target.parentNode && target.parentNode.classList && target.parentNode.classList.contains('md-code')) {
        target = target.parentNode;
      }
      if (target.parentNode) target.parentNode.replaceChild(el, target);
    }
  }
  function mathIn(scope) {
    if (!scope.querySelector(MATH_MARKS)) return;
    if (window.katex && loading.katex) { renderMath(scope); return; }
    need('katex').then(function () {
      if (scope.isConnected !== false) renderMath(scope);
    }, function () {
      mathFailed = true; // formulas stay readable as code
      if (scope.isConnected === false) return;
      var codes = scope.querySelectorAll('pre > code.language-math');
      for (var c = 0; c < codes.length; c++) codeBlock(codes[c], false);
    });
  }

  // ── Diagrams: a finished reply's ```mermaid block becomes an image ──
  var diagramSeq = 0, mermaidTheme = null, diagramCache = new Map();
  var DIAGRAM_FONT = '-apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif';
  function diagramTheme() {
    return document.documentElement.getAttribute('data-theme') === 'light' ? 'default' : 'dark';
  }
  function codeBoxOf(pre) {
    return pre.parentNode && pre.parentNode.classList && pre.parentNode.classList.contains('md-code') ? pre.parentNode : pre;
  }
  function placeDiagram(pre, xml, src, theme) {
    var target = codeBoxOf(pre);
    if (target.isConnected === false || !target.parentNode) return;
    var box = node('div', 'md-diagram');
    var head = node('div', 'md-head');
    var label = node('span', 'md-label');
    label.textContent = 'Diagram';
    var acts = node('span', 'md-acts');
    var copyBtn = iconButton('Copy diagram source', ICON_COPY);
    var saveBtn = iconButton('Download diagram', ICON_DOWNLOAD);
    copyBtn.addEventListener('click', function () { copy(src, copyBtn); });
    saveBtn.addEventListener('click', function () { download(xml, 'svg', 'diagram.svg', 'image/svg+xml'); });
    acts.appendChild(copyBtn);
    acts.appendChild(saveBtn);
    head.appendChild(label);
    head.appendChild(acts);
    var body = node('div', 'md-diagram-body ' + (theme === 'dark' ? 'md-diagram-dark' : 'md-diagram-light'));
    var img = document.createElement('img');
    img.alt = 'Diagram';
    img.decoding = 'async';
    img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(xml);
    body.appendChild(img);
    box.appendChild(head);
    box.appendChild(body);
    target.parentNode.replaceChild(box, target);
  }
  function diagramFailed(pre) {
    var label = codeBoxOf(pre).querySelector && codeBoxOf(pre).querySelector('.md-label');
    if (label) label.textContent = 'mermaid: could not draw';
  }
  function drawDiagram(code) {
    if (code.__codecDiagram) return;
    code.__codecDiagram = true;
    var pre = code.parentNode;
    var src = code.textContent.replace(/\n$/, '');
    if (!src.trim() || src.length > DIAGRAM_MAX) return;
    var theme = diagramTheme(), key = theme + '\u0000' + src;
    if (diagramCache.has(key)) { placeDiagram(pre, diagramCache.get(key), src, theme); return; }
    var id = 'codec-mmd-' + (++diagramSeq);
    need('mermaid').then(function () {
      var m = window.mermaid;
      if (mermaidTheme !== theme) {
        m.initialize({startOnLoad: false, securityLevel: 'strict', theme: theme, suppressErrorRendering: true,
          htmlLabels: false, flowchart: {htmlLabels: false}, fontFamily: DIAGRAM_FONT, maxTextSize: DIAGRAM_MAX});
        mermaidTheme = theme;
      }
      return m.render(id, src);
    }).then(function (res) {
      var xml = safeSvg(res && res.svg, id);
      if (!xml) throw new Error('empty diagram');
      diagramCache.set(key, xml);
      if (diagramCache.size > 32) diagramCache.delete(diagramCache.keys().next().value);
      placeDiagram(pre, xml, src, theme);
    }).catch(function () {
      diagramFailed(pre);
    }).then(function () {
      // Mermaid measures in a scratch element; a failed render can leave it behind.
      ['d' + id, id].forEach(function (x) { var s = document.getElementById(x); if (s && !s.closest('.md')) s.remove(); });
    });
  }

  function enhance(root, streaming) {
    if (!root || !root.querySelectorAll) return;
    var scopes = Array.prototype.slice.call(root.querySelectorAll('.md'));
    if (root.classList && root.classList.contains('md')) scopes.unshift(root);
    for (var i = 0; i < scopes.length; i++) {
      mathIn(scopes[i]);
      var codes = scopes[i].querySelectorAll('pre > code');
      for (var c = 0; c < codes.length; c++) {
        if (isMathBlock(codes[c]) && !mathFailed) continue; // KaTeX draws it
        codeBlock(codes[c], !!streaming);
      }
      if (!streaming) {
        var diagrams = scopes[i].querySelectorAll('pre > code.language-mermaid');
        for (var g = 0; g < diagrams.length; g++) drawDiagram(diagrams[g]);
      }
      var tables = scopes[i].querySelectorAll('table');
      for (var t = 0; t < tables.length; t++) tableBox(tables[t]);
    }
  }

  function paint(el, text, streaming) {
    el.innerHTML = html(text);
    el.classList.add('md');
    enhance(el, streaming);
  }

  function render(el, text) {
    if (!el) return;
    if (el.__codecMdStream) el.__codecMdStream.cancel();
    paint(el, text, false);
  }

  function stream(el, opts) {
    if (el.__codecMdStream) return el.__codecMdStream;
    var onRender = opts && opts.onRender;
    var nextFrame = window.requestAnimationFrame ? window.requestAnimationFrame.bind(window) : function (f) { return setTimeout(f, 16); };
    var dropFrame = window.cancelAnimationFrame ? window.cancelAnimationFrame.bind(window) : clearTimeout;
    var pending = null, last = 0, frame = 0, live = true;
    function tick() {
      frame = 0;
      if (!live || pending === null) return;
      if (Date.now() - last < THROTTLE_MS) { frame = nextFrame(tick); return; }
      var text = pending;
      pending = null;
      last = Date.now();
      if (el.isConnected === false) return; // the finished reply replaced this one
      paint(el, text, true);
      if (onRender) { try { onRender(el); } catch (e) {} }
    }
    var api = {
      update: function (text) {
        if (!live) return;
        pending = String(text == null ? '' : text);
        if (!frame) frame = nextFrame(tick);
      },
      cancel: function () {
        live = false;
        pending = null;
        if (frame) dropFrame(frame);
        frame = 0;
        if (el.__codecMdStream === api) el.__codecMdStream = null;
      }
    };
    el.__codecMdStream = api;
    return api;
  }

  window.codecMarkdown = {
    html: html,
    render: render,
    enhance: function (root) { enhance(root, false); },
    stream: stream,
    texHtml: texHtml,
    safeSvg: safeSvg
  };
})();
