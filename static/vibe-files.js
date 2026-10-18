/* Vibe: file helpers with no page in them (docs/VIBE-PROJECTS-DESIGN.md).

   Reads the files out of an AI reply, names the ones the model did not name, picks the
   editor language for a path and inlines local css and js into an html file for the
   preview. Pure functions on strings, so Node can test them (tests/test_vibe_files.py).
   Loaded by codec_vibe.html before static/vibe-projects.js. */
(function (root) {
  'use strict';

  var EXT = { python: '.py', javascript: '.js', typescript: '.ts', html: '.html', css: '.css', json: '.json',
    bash: '.sh', go: '.go', rust: '.rs', java: '.java', cpp: '.cpp', swift: '.swift', ruby: '.rb', sql: '.sql',
    yaml: '.yml', markdown: '.md', plaintext: '.txt' };
  var ALIAS = { js: 'javascript', jsx: 'javascript', mjs: 'javascript', ts: 'typescript', tsx: 'typescript',
    py: 'python', sh: 'bash', shell: 'bash', zsh: 'bash', yml: 'yaml', md: 'markdown', rs: 'rust', rb: 'ruby',
    'c++': 'cpp', htm: 'html', text: 'plaintext', txt: 'plaintext', plain: 'plaintext' };
  var NAME = { html: 'index.html', css: 'style.css', javascript: 'script.js', typescript: 'main.ts', python: 'main.py',
    bash: 'script.sh', json: 'data.json', markdown: 'README.md', go: 'main.go', rust: 'main.rs', java: 'Main.java',
    cpp: 'main.cpp', swift: 'main.swift', ruby: 'main.rb', sql: 'query.sql', yaml: 'config.yml', plaintext: 'notes.txt' };
  var MONACO = { py: 'python', js: 'javascript', mjs: 'javascript', jsx: 'javascript', ts: 'typescript', tsx: 'typescript',
    html: 'html', htm: 'html', css: 'css', json: 'json', sh: 'shell', go: 'go', rs: 'rust', java: 'java', cpp: 'cpp',
    cc: 'cpp', c: 'c', h: 'c', swift: 'swift', rb: 'ruby', sql: 'sql', yml: 'yaml', yaml: 'yaml', md: 'markdown',
    txt: 'plaintext', svg: 'xml', xml: 'xml' };

  function ext(path) {
    var m = /\.([A-Za-z0-9]{1,8})$/.exec(path || '');
    return m ? m[1].toLowerCase() : '';
  }

  function canonLang(l) {
    l = String(l || '').toLowerCase().replace(/^language-/, '');
    if (ALIAS[l]) return ALIAS[l];
    return EXT[l] ? l : (l ? 'plaintext' : '');
  }

  function langOfPath(path) {
    var e = ext(path);
    return canonLang(e) === 'plaintext' && e !== 'txt' ? '' : canonLang(e);
  }

  function monacoLang(path) { return MONACO[ext(path)] || 'plaintext'; }

  function extFor(lang) { return EXT[canonLang(lang)] || ''; }

  function defaultName(lang) { return NAME[canonLang(lang)] || NAME.plaintext; }

  function looksLikePath(tok) {
    if (!tok || tok.length > 200 || tok.indexOf('..') === 0) return false;
    return /^[\w@~.+\/-]+\.[A-Za-z0-9]{1,8}$/.test(tok) || /^[\w@~.+-]+\/[\w@~.+\/-]+$/.test(tok);
  }

  function cleanPath(p) {
    p = String(p || '').trim().replace(/^[`'"]+|[`'",;]+$/g, '').replace(/^\.\//, '');
    return p;
  }

  // "html file:index.html", "file:index.html", "index.html", "html:index.html",
  // 'js title="app.js"', "css filename=style.css"  ->  { lang, path }
  function parseInfo(info) {
    var out = { lang: '', path: '' };
    var toks = String(info || '').trim().split(/\s+/).filter(Boolean);
    for (var i = 0; i < toks.length; i++) {
      var t = toks[i], m;
      if ((m = /^(?:file|filename|path|title|name)\s*[:=]\s*(.+)$/i.exec(t))) { out.path = cleanPath(m[1]); continue; }
      if (i === 0 && (m = /^([A-Za-z0-9_+#-]+):(.+)$/.exec(t)) && looksLikePath(cleanPath(m[2]))) {
        out.lang = canonLang(m[1]); out.path = cleanPath(m[2]); continue;
      }
      if (looksLikePath(cleanPath(t)) && !out.path) { out.path = cleanPath(t); continue; }
      if (!out.lang && /^[A-Za-z0-9_+#-]+$/.test(t)) out.lang = canonLang(t);
    }
    if (!out.lang && out.path) out.lang = langOfPath(out.path);
    return out;
  }

  var MARKER = /^\s*(?:\/\/|#|<!--|\/\*|--|;)\s*(?:file(?:name)?|path)\s*[:=]\s*([^\s*>]+)\s*(?:-->|\*\/)?\s*$/i;

  // Every fenced block in a (possibly still streaming) reply:
  //   [{ lang, path, code, closed }]   path is '' when the model did not name the file.
  function parseBlocks(text) {
    text = String(text || '');
    var lines = text.split('\n');
    var blocks = [], cur = null, fence = '';
    for (var i = 0; i < lines.length; i++) {
      var ln = lines[i];
      if (!cur) {
        var m = /^\s*(`{3,}|~{3,})\s*(.*)$/.exec(ln);
        if (m && (m[2].indexOf('`') === -1 || m[1][0] === '~')) {
          // The opening line is still arriving ("```ht"): its language and name are not known yet.
          if (i === lines.length - 1 && text.charAt(text.length - 1) !== '\n') break;
          fence = m[1];
          var info = parseInfo(m[2]);
          cur = { lang: info.lang, path: info.path, lines: [], closed: false };
        }
      } else {
        var c = /^\s*(`{3,}|~{3,})\s*$/.exec(ln);
        if (c && c[1][0] === fence[0] && c[1].length >= fence.length) {
          cur.closed = true; blocks.push(finish(cur)); cur = null;
        } else {
          cur.lines.push(ln);
        }
      }
    }
    if (cur) {
      // a partial closing fence ("`" or "``") on the last line is not code yet
      if (cur.lines.length && /^\s*`{1,2}$/.test(cur.lines[cur.lines.length - 1])) cur.lines.pop();
      blocks.push(finish(cur));
    }
    return blocks;
  }

  function finish(b) {
    var lines = b.lines.slice();
    var path = b.path;
    if (!path && lines.length) {
      var mk = MARKER.exec(lines[0]);
      if (mk && looksLikePath(cleanPath(mk[1]))) { path = cleanPath(mk[1]); lines.shift(); }
    }
    var lang = b.lang || (path ? langOfPath(path) : '');
    return { lang: lang, path: path, code: lines.join('\n') + (b.closed && lines.length ? '\n' : ''), closed: b.closed };
  }

  function sameLang(path, lang) {
    if (!lang) return true;
    var pl = langOfPath(path);
    return !pl || pl === lang;
  }

  function unique(name, taken) {
    if (!taken[name]) return name;
    var dot = name.lastIndexOf('.');
    var base = dot > 0 ? name.slice(0, dot) : name, e = dot > 0 ? name.slice(dot) : '';
    for (var n = 2; n < 100; n++) { if (!taken[base + '-' + n + e]) return base + '-' + n + e; }
    return base + '-' + Date.now() + e;
  }

  // Give every block a path. A named block keeps its name. An unnamed block that matches the
  // language of the open file replaces that file (the model sent the whole file back);
  // anything else gets a name from its language.
  function resolvePaths(blocks, activePath) {
    var taken = {};
    blocks.forEach(function (b) { if (b.path) taken[b.path] = true; });
    var activeUsed = false;
    return blocks.map(function (b) {
      var path = b.path;
      if (!path) {
        var lang = b.lang || '';
        if (activePath && !activeUsed && !taken[activePath] && sameLang(activePath, lang) && (lang || blocks.length === 1)) {
          path = activePath; activeUsed = true;
        } else {
          path = unique(defaultName(lang), taken);
        }
        taken[path] = true;
      }
      return { lang: b.lang || langOfPath(path), path: path, code: b.code, closed: b.closed };
    });
  }

  // The explanation around the code: the reply with its fenced blocks removed.
  function textOutsideBlocks(text) {
    var lines = String(text || '').split('\n'), out = [], fence = '';
    lines.forEach(function (ln) {
      if (!fence) {
        var m = /^\s*(`{3,}|~{3,})/.exec(ln);
        if (m) { fence = m[1]; return; }
        out.push(ln);
      } else {
        var c = /^\s*(`{3,}|~{3,})\s*$/.exec(ln);
        if (c && c[1][0] === fence[0] && c[1].length >= fence.length) fence = '';
      }
    });
    return out.join('\n').replace(/\n{3,}/g, '\n\n').trim();
  }

  function normRef(ref) {
    ref = String(ref || '').trim().split('#')[0].split('?')[0];
    if (!ref || /^([a-z][a-z0-9+.-]*:|\/\/)/i.test(ref)) return '';
    return ref.replace(/^\.\//, '').replace(/^\//, '');
  }

  // Preview of a multi-file page: local stylesheets and scripts that exist in the project are
  // placed into the page itself (the preview frame loads a document, not a folder).
  function inlineAssets(html, files) {
    html = String(html || '');
    html = html.replace(/<link\b[^>]*>/gi, function (tag) {
      if (!/\brel\s*=\s*["']?stylesheet/i.test(tag)) return tag;
      var m = /\bhref\s*=\s*["']([^"']+)["']/i.exec(tag);
      var p = m ? normRef(m[1]) : '';
      if (!p || typeof files[p] !== 'string') return tag;
      return '<style>\n' + files[p].replace(/<\/style/gi, '<\\/style') + '\n</style>';
    });
    html = html.replace(/<script\b([^>]*)>\s*<\/script>/gi, function (tag, attrs) {
      var m = /\bsrc\s*=\s*["']([^"']+)["']/i.exec(attrs);
      var p = m ? normRef(m[1]) : '';
      if (!p || typeof files[p] !== 'string') return tag;
      var rest = attrs.replace(/\s*\bsrc\s*=\s*["'][^"']*["']/i, '');
      return '<script' + rest + '>\n' + files[p].replace(/<\/script/gi, '<\\/script') + '\n</script>';
    });
    return html;
  }

  var api = { parseBlocks: parseBlocks, parseInfo: parseInfo, resolvePaths: resolvePaths,
    textOutsideBlocks: textOutsideBlocks, inlineAssets: inlineAssets, defaultName: defaultName,
    extFor: extFor, monacoLang: monacoLang, langOfPath: langOfPath, canonLang: canonLang };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.VibeFiles = api;
})(typeof window !== 'undefined' ? window : globalThis);
