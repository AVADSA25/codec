/* Vibe coding agent on the page (docs/VIBE-AGENT-DESIGN.md, step 3b).

   Agent mode: the owner types a goal, the server runs the agent (codec_vibe_agent.py) and this
   file shows it. A run card in the chat column lists every step as it happens, asks the owner's
   questions inline, and ends with the report and "Undo run". The page follows the run by asking
   the server for news (long polling), so closing the tab loses nothing: opening the project again
   picks the run up.

   The top half is plain functions on events and text, so Node can test them. The bottom half draws
   the card. Nothing from a run (goal, file names, file text, replies) is ever put on the page as
   HTML: everything goes in with textContent. Loaded after static/vibe-projects.js. */
(function (root) {
  'use strict';

  var STATUS_TEXT = { running: 'Working', waiting: 'Waiting for you', stopping: 'Stopping', done: 'Done', stopped: 'Stopped',
    limit: 'Stopped at a limit', failed: 'Failed', interrupted: 'Interrupted' };
  var ACTIVE = { running: 1, waiting: 1, stopping: 1 };
  var LIVE_CHARS = 220;
  var HISTORY_TURNS = 4;

  // ── what a run looks like, from its events ─────────────────────────────

  function newRun(pid) {
    return { pid: pid, id: null, status: 'running', goal: '', model: '', maxSteps: 30, step: 0, since: 0, startedAt: 0,
      startCp: null, steps: {}, checkpoints: [], changed: [], live: '', liveStep: 0, question: null, approval: null, canRun: true,
      ranCommands: false, end: null, finished: false };
  }

  function addUnique(list, items) {
    (items || []).forEach(function (p) { if (list.indexOf(p) === -1) list.push(p); });
  }

  // Takes one event, updates the run, and says what to draw.
  function applyEvent(run, ev) {
    if (typeof ev.seq === 'number' && ev.seq > run.since) run.since = ev.seq;
    switch (ev.type) {
      case 'start':
        run.goal = ev.goal || run.goal;
        run.model = (ev.model && ev.model.label) || run.model;
        run.maxSteps = (ev.limits && ev.limits.max_steps) || run.maxSteps;
        run.startCp = ev.start_checkpoint || null;
        run.startedAt = ev.t || run.startedAt;
        run.status = 'running';
        run.canRun = ev.can_run !== false;
        return [{ t: 'meta' }].concat(run.canRun ? [] : [{ t: 'note', step: 0, text: 'Commands are off for this run: it was started away from this Mac.' }]);
      case 'note':
        run.live = ''; run.liveStep = 0;
        return [{ t: 'live', text: '' }, { t: 'note', step: ev.step, text: ev.text }];
      case 'text': {
        if (ev.step !== run.liveStep) { run.live = ''; run.liveStep = ev.step; }
        run.live = (run.live + (ev.chunk || '')).slice(-4000);
        return [{ t: 'live', text: describeLive(run.live) }];
      }
      case 'tool':
        run.step = Math.max(run.step, ev.step || 0);
        run.live = ''; run.liveStep = 0;
        run.steps[ev.step] = { step: ev.step, name: ev.name, arg: ev.arg, ok: null, summary: '', output: '', cp: null };
        if (ev.name === 'run_check') run.ranCommands = true;
        return [{ t: 'live', text: '' }, { t: 'meta' }, { t: 'tool', step: ev.step, name: ev.name, arg: ev.arg }];
      case 'result': {
        var s = run.steps[ev.step] || (run.steps[ev.step] = { step: ev.step, name: ev.tool || '', arg: '', cp: null });
        s.ok = ev.ok !== false; s.summary = ev.summary || ''; s.output = ev.output || ''; s.name = ev.tool || s.name;
        return [{ t: 'result', step: ev.step, ok: s.ok, summary: s.summary, output: s.output, name: s.name }];
      }
      case 'checkpoint': {
        var st = run.steps[ev.step];
        var cp = { step: ev.step, id: ev.id, parent: ev.parent || null, label: ev.label || '', added: ev.lines_added || 0,
          removed: ev.lines_removed || 0, changed: ev.changed || [] };
        if (st) st.cp = cp;
        run.checkpoints.push(cp);
        addUnique(run.changed, cp.changed);
        return [{ t: 'checkpoint', cp: cp }];
      }
      case 'question':
        run.status = 'waiting';
        run.question = { id: ev.id, text: ev.text, choices: ev.choices || [] };
        return [{ t: 'meta' }, { t: 'question', id: ev.id, text: ev.text, choices: ev.choices || [] }];
      case 'answer':
        run.status = 'running';
        run.question = null;
        return [{ t: 'meta' }, { t: 'answer', id: ev.id }];
      case 'approval':
        run.status = 'waiting';
        run.approval = { id: ev.id, command: ev.command };
        return [{ t: 'meta' }, { t: 'approval', id: ev.id, command: ev.command }];
      case 'decision':
        run.status = 'running';
        run.approval = null;
        return [{ t: 'meta' }, { t: 'decision', id: ev.id, decision: ev.decision, network: !!ev.network }];
      case 'end':
        run.status = ev.status || 'done';
        run.finished = true;
        run.end = { status: run.status, report: ev.report || '', steps: ev.steps, seconds: ev.seconds, startCp: ev.start_checkpoint || run.startCp };
        run.live = '';
        return [{ t: 'live', text: '' }, { t: 'meta' }, { t: 'end', end: run.end }];
      default:
        return [];
    }
  }

  // The model's words while it writes: its sentence, then which tool it is starting. Never its raw code.
  function describeLive(raw) {
    var text = String(raw || '').replace(/<think>[\s\S]*?(<\/think>|$)/g, '');
    var at = text.search(/(^|\n)[ \t]*@@/);
    var sentence = (at === -1 ? text : text.slice(0, at)).replace(/\s+/g, ' ').trim();
    var tool = '';
    if (at !== -1) {
      var m = /@@[ \t]*([A-Za-z_]+):?[ \t]*([^\n]*)/.exec(text.slice(at));
      if (m) tool = toolLabel(m[1].toLowerCase(), (m[2] || '').trim());
    }
    var out = sentence.slice(-LIVE_CHARS);
    return tool ? (out ? out + ' ' : '') + tool + '...' : out;
  }

  function toolLabel(name, arg) {
    var a = arg ? ' ' + arg : '';
    switch (name) {
      case 'list_files': return 'Listing files' + (arg ? ' in ' + arg : '');
      case 'read_file': return 'Reading' + a;
      case 'search': return 'Searching' + a;
      case 'edit_file': return 'Editing' + a;
      case 'write_file': return 'Writing' + a;
      case 'delete_file': return 'Deleting' + a;
      case 'run_check': return 'Running' + (arg ? ' ' + arg : ' a check');
      case 'ask': return 'Asking you a question';
      case 'finish': return 'Finishing';
      default: return name;
    }
  }

  function statusText(run) { return STATUS_TEXT[run.status] || run.status; }
  function stepText(run) {
    if (run.finished) return run.step + (run.step === 1 ? ' step' : ' steps');
    return run.step ? 'Step ' + Math.min(run.step, run.maxSteps) + ' of ' + run.maxSteps : 'Starting';
  }
  function isActive(run) { return !!run && !run.finished && !!ACTIVE[run.status]; }

  function fmtElapsed(seconds) {
    var s = Math.max(0, Math.floor(seconds || 0));
    return Math.floor(s / 60) + ':' + (s % 60 < 10 ? '0' : '') + (s % 60);
  }

  // Undo run makes sense when the run saved a start point and changed something.
  function canUndoRun(run) { return !!(run && run.finished && run.end && run.end.startCp && run.checkpoints.length); }

  // The chat so far, as plain text, for the agent to read (the newest messages, without the one just typed).
  function historyFor(hist) {
    var out = [];
    (hist || []).forEach(function (m) {
      var text = String(m.plain || m.content || '').trim();
      if (m && (m.role === 'user' || m.role === 'assistant') && text) out.push({ role: m.role, content: text.slice(0, 1500) });
    });
    return out.slice(-HISTORY_TURNS);
  }

  // What is kept in the chat history when a run ends.
  function reportMessage(run) {
    var e = run.end || {};
    var text = e.report || (STATUS_TEXT[run.status] || '');
    return text + (run.changed.length ? '\n[Files changed: ' + run.changed.join(', ') + ']' : '');
  }

  // A run read from its log after a dashboard restart has no end event: the server says it is over and calls it interrupted.
  function synthEnd(run, view) {
    if (!view || !view.finished || run.finished) return null;
    var status = !view.status || view.status === 'running' || view.status === 'waiting' ? 'interrupted' : view.status;
    return { seq: run.since, type: 'end', status: status, steps: run.step, start_checkpoint: run.startCp,
      report: status === 'interrupted' ? 'The agent stopped before it finished (CODEC was restarted). Your files are as the last step left them.' : '' };
  }

  var core = { newRun: newRun, synthEnd: synthEnd, applyEvent: applyEvent, describeLive: describeLive, toolLabel: toolLabel, statusText: statusText,
    stepText: stepText, isActive: isActive, fmtElapsed: fmtElapsed, canUndoRun: canUndoRun, historyFor: historyFor,
    reportMessage: reportMessage };

  if (typeof document === 'undefined') {            // Node: only the plain functions
    if (typeof module !== 'undefined' && module.exports) module.exports = core;
    return;
  }

  // ── the page ───────────────────────────────────────────────────────────

  var VP = root.VibeProj;
  var S = { available: false, mode: 'chat', model: 'local', models: null, run: null, pid: null, card: null, timer: null,
    clock: null, reloadTimer: null, reloading: false, lost: 0 };
  var KEY = 'vibeAgentMode';

  function $(id) { return document.getElementById(id); }
  function toast(m) { if (root.CodecShell && root.CodecShell.toast) root.CodecShell.toast(m); }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private window: forget it */ } }

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
  function base(pid, path) { return '/api/vibe/projects/' + pid + '/agent' + (path || ''); }

  // Small line icons, written here and never built from data.
  var ICONS = {
    list: 'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',
    read: 'M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9zM14 3v6h6M8 13h8M8 17h8',
    search: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM21 21l-4.3-4.3',
    edit: 'M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z',
    write: 'M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9zM14 3v6h6M12 12v6M9 15h6',
    trash: 'M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14',
    ask: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7M12 17h.01',
    done: 'M20 6 9 17l-5-5',
    fail: 'M18 6 6 18M6 6l12 12',
    step: 'M12 12m-3 0a3 3 0 1 0 6 0 3 3 0 1 0-6 0'
  };
  function icon(name) {
    var ns = 'http://www.w3.org/2000/svg';
    var svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('width', '15'); svg.setAttribute('height', '15'); svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none'); svg.setAttribute('stroke', 'currentColor'); svg.setAttribute('stroke-width', '1.75');
    svg.setAttribute('stroke-linecap', 'round'); svg.setAttribute('stroke-linejoin', 'round'); svg.setAttribute('aria-hidden', 'true');
    var p = document.createElementNS(ns, 'path');
    p.setAttribute('d', ICONS[name] || ICONS.step);
    svg.appendChild(p);
    return svg;
  }
  var TOOL_ICON = { list_files: 'list', read_file: 'read', search: 'search', edit_file: 'edit', write_file: 'write', delete_file: 'trash',
    ask: 'ask', finish: 'done', run_check: 'step' };

  function ask(opts) {
    if (root.CodecShell && root.CodecShell.ask) return Promise.resolve(root.CodecShell.ask(opts));
    return Promise.resolve(true);
  }

  // ── the mode switch above the composer ─────────────────────────────────
  function modeKey() { return S.pid ? KEY + ':' + S.pid : KEY; }

  function renderMode() {
    var box = $('amode');
    if (!box) return;
    box.hidden = !S.available;
    var chat = $('amChat'), agent = $('amAgent'), sel = $('amModel');
    if (chat) { chat.classList.toggle('sel', S.mode === 'chat'); chat.setAttribute('aria-pressed', S.mode === 'chat' ? 'true' : 'false'); }
    if (agent) { agent.classList.toggle('sel', S.mode === 'agent'); agent.setAttribute('aria-pressed', S.mode === 'agent' ? 'true' : 'false'); }
    if (sel) sel.hidden = !(S.available && S.mode === 'agent' && S.models && S.models.cloud);
    var vi = $('vi');
    if (vi) {
      if (!vi.dataset.chatHint) vi.dataset.chatHint = vi.placeholder;
      vi.placeholder = S.available && S.mode === 'agent' ? 'Tell the agent what to do...' : vi.dataset.chatHint;
    }
  }

  function setMode(mode) {
    S.mode = mode === 'agent' ? 'agent' : 'chat';
    lsSet(KEY, S.mode);
    if (S.pid) lsSet(modeKey(), S.mode);
    renderMode();
  }

  function loadMode() {
    var own = S.pid ? lsGet(modeKey()) : null;
    S.mode = (own || lsGet(KEY)) === 'agent' ? 'agent' : 'chat';
    renderMode();
  }

  function setupModels(info) {
    S.models = info;
    var sel = $('amModel');
    if (!sel) return;
    sel.textContent = '';
    var local = el('option', null, 'Local model'); local.value = 'local'; sel.appendChild(local);
    if (info.cloud) {
      var c = el('option', null, info.cloud.label + ' (cloud)');
      c.value = 'cloud'; c.disabled = !info.cloud.ready;
      if (!info.cloud.ready) c.title = info.cloud.reason || 'Not ready';
      sel.appendChild(c);
    }
    sel.value = S.model = 'local';
  }

  function init() {
    if (!VP) return;
    var chat = $('amChat'), agent = $('amAgent'), sel = $('amModel');
    if (chat) chat.addEventListener('click', function () { setMode('chat'); });
    if (agent) agent.addEventListener('click', function () { setMode('agent'); });
    if (sel) sel.addEventListener('change', function () { S.model = sel.value === 'cloud' ? 'cloud' : 'local'; });
    api('GET', '/api/vibe/agent/models').then(function (info) {
      S.available = true;
      setupModels(info);
      loadMode();
    }).catch(function () { S.available = false; renderMode(); });
  }

  // Agent mode, or a run still going: either way a message belongs to the agent, never to the one-shot chat.
  function active() { return S.available && (S.mode === 'agent' || running()); }
  function running() { return isActive(S.run); }

  // ── the run card ───────────────────────────────────────────────────────
  function buildCard(run) {
    var card = el('section', 'agc');
    card.setAttribute('aria-label', 'Agent run');
    var head = el('div', 'agc-h');
    var goal = el('div', 'agc-goal', run.goal || '');
    var meta = el('div', 'agc-meta');
    var st = el('span', 'agc-st', ''); st.setAttribute('role', 'status');
    var model = el('span', 'agc-model', '');
    var step = el('span', 'agc-step', '');
    var time = el('span', 'agc-time', '');
    meta.appendChild(st); meta.appendChild(model); meta.appendChild(step); meta.appendChild(time);
    var stop = el('button', 'agc-stop', 'Stop');
    stop.type = 'button'; stop.setAttribute('aria-label', 'Stop the agent');
    stop.addEventListener('click', stopRun);
    head.appendChild(goal); head.appendChild(meta); head.appendChild(stop);
    var steps = el('ol', 'agc-steps');
    var liveLine = el('div', 'agc-live'); liveLine.hidden = true;
    var end = el('div', 'agc-end'); end.hidden = true;
    card.appendChild(head); card.appendChild(steps); card.appendChild(liveLine); card.appendChild(end);
    return { root: card, goal: goal, st: st, model: model, step: step, time: time, stop: stop, steps: steps, live: liveLine, end: end,
      rows: {}, atBottom: true };
  }

  function drawMeta() {
    var c = S.card, r = S.run;
    if (!c || !r) return;
    c.st.textContent = statusText(r);
    c.root.setAttribute('data-status', r.status);
    c.model.textContent = r.model || '';
    c.step.textContent = stepText(r);
    var t0 = r.startedAt || Date.now() / 1000;
    var secs = r.finished && r.end && typeof r.end.seconds === 'number' ? r.end.seconds : (Date.now() / 1000 - t0);
    c.time.textContent = fmtElapsed(secs);
    c.stop.hidden = !isActive(r);
    c.stop.disabled = r.status === 'stopping';
  }

  function keepInView() {
    var cm = $('cm');
    if (cm && S.card && S.card.atBottom) cm.scrollTop = cm.scrollHeight;
  }

  function addRow(eff) {
    var c = S.card;
    var li = el('li', 'agc-row run');
    var headBtn = el('button', 'agc-rh');
    headBtn.type = 'button'; headBtn.setAttribute('aria-expanded', 'false'); headBtn.disabled = true;
    var ic = el('span', 'agc-ic'); ic.appendChild(icon(TOOL_ICON[eff.name] || 'step'));
    var sum = el('span', 'agc-sum', toolLabel(eff.name, eff.arg) + '...');
    var badge = el('span', 'agc-badge');
    headBtn.appendChild(ic); headBtn.appendChild(sum); headBtn.appendChild(badge);
    var body = el('div', 'agc-rb'); body.hidden = true;
    var out = el('pre', 'agc-out'); body.appendChild(out);
    var acts = el('div', 'agc-ra'); acts.hidden = true;
    headBtn.addEventListener('click', function () {
      body.hidden = !body.hidden;
      headBtn.setAttribute('aria-expanded', body.hidden ? 'false' : 'true');
    });
    li.appendChild(headBtn); li.appendChild(body); li.appendChild(acts);
    c.steps.appendChild(li);
    c.rows[eff.step] = { li: li, head: headBtn, sum: sum, badge: badge, body: body, out: out, acts: acts };
  }

  function drawResult(eff) {
    var row = S.card && S.card.rows[eff.step];
    if (!row) return;
    row.li.className = 'agc-row ' + (eff.ok ? 'ok' : 'bad');
    row.sum.textContent = eff.summary || (eff.ok ? 'Done' : 'Failed');
    row.badge.textContent = '';
    row.badge.appendChild(icon(eff.ok ? 'done' : 'fail'));
    row.out.textContent = eff.output || '';
    row.head.disabled = !eff.output;
  }

  function drawCheckpoint(cp) {
    var row = S.card && S.card.rows[cp.step];
    if (!row) return;
    row.acts.hidden = false;
    row.acts.textContent = '';
    row.acts.appendChild(el('span', 'agc-chip', '+' + cp.added + ' −' + cp.removed));
    var show = el('button', 'agc-link', 'Show change');
    show.type = 'button';
    var diffBox = el('div', 'agc-diff'); diffBox.hidden = true;
    var loaded = false;
    show.addEventListener('click', function () {
      diffBox.hidden = !diffBox.hidden;
      if (diffBox.hidden || loaded) return;
      loaded = true;
      diffBox.textContent = 'Loading...';
      api('GET', '/api/vibe/projects/' + S.run.pid + '/diff?from=' + encodeURIComponent(cp.parent || '') + '&to=' + encodeURIComponent(cp.id))
        .then(function (d) {
          diffBox.textContent = '';
          (d.files || []).forEach(function (f) {
            diffBox.appendChild(el('div', 'agc-dfile', f.path));
            diffBox.appendChild(VP.diffNode(f.diff));
          });
          if (!(d.files || []).length) diffBox.textContent = 'No file differs.';
        }).catch(function (e) { diffBox.textContent = 'Could not load: ' + e.message; });
    });
    row.acts.appendChild(show);
    if (cp.parent) {
      var back = el('button', 'agc-link', 'Back to before this step');
      back.type = 'button';
      back.addEventListener('click', function () { backTo(cp, back); });
      row.acts.appendChild(back);
    }
    row.acts.appendChild(diffBox);
  }

  function drawQuestion(eff) {
    var c = S.card;
    var li = el('li', 'agc-q');
    li.setAttribute('data-q', eff.id);
    li.appendChild(el('div', 'agc-qt', eff.text));
    var opts = el('div', 'agc-qc');
    var form = el('form', 'agc-qf');
    var input = el('input'); input.type = 'text'; input.placeholder = eff.choices.length ? 'Or write your own answer' : 'Your answer';
    input.setAttribute('aria-label', 'Your answer');
    var go = el('button', 'agc-btn', 'Send'); go.type = 'submit';
    form.appendChild(input); form.appendChild(go);
    var all = [];
    function send(text) {
      if (!text.trim()) return;
      all.forEach(function (n) { n.disabled = true; });
      api('POST', base(S.run.pid, '/' + S.run.id + '/answer'), { id: eff.id, text: text.trim() }).then(function () {
        opts.hidden = true; form.hidden = true;
        li.appendChild(el('div', 'agc-qa', 'You answered: ' + text.trim()));
      }).catch(function (e) {
        all.forEach(function (n) { n.disabled = false; });
        toast('Could not send the answer: ' + e.message);
      });
    }
    eff.choices.forEach(function (ch) {
      var b = el('button', 'agc-btn', ch); b.type = 'button';
      b.addEventListener('click', function () { send(ch); });
      opts.appendChild(b); all.push(b);
    });
    form.addEventListener('submit', function (e) { e.preventDefault(); send(input.value); });
    all.push(input); all.push(go);
    li.appendChild(opts); li.appendChild(form);
    c.steps.appendChild(li);
    input.focus({ preventScroll: true });
  }

  // The agent asks to run a command. It runs only when the owner clicks, and only this exact text.
  function drawApproval(eff) {
    var li = el('li', 'agc-q agc-ap');
    li.setAttribute('data-a', eff.id);
    li.appendChild(el('div', 'agc-qt', 'The agent wants to run this command:'));
    li.appendChild(el('code', 'agc-cmd', eff.command));
    var label = el('label', 'agc-net');
    var net = el('input'); net.type = 'checkbox';
    label.appendChild(net);
    label.appendChild(document.createTextNode(' Allow the web for this command (npm install, downloads)'));
    li.appendChild(label);
    li.appendChild(el('div', 'agc-qa', 'It runs in a sandbox: it reads this project, writes only inside it, has no network unless you tick the box, and is stopped after 60 seconds.'));
    var row = el('div', 'agc-qc');
    var once = el('button', 'agc-btn', 'Allow once'), always = el('button', 'agc-btn', 'Always allow here'), no = el('button', 'agc-btn quiet', 'Decline');
    [once, always, no].forEach(function (b) { b.type = 'button'; row.appendChild(b); });
    li.appendChild(row);
    function decide(decision, b) {
      var all = [once, always, no, net];
      all.forEach(function (n) { n.disabled = true; });
      api('POST', base(S.run.pid, '/' + S.run.id + '/approve'), { id: eff.id, decision: decision, network: net.checked }).then(function () {
        row.hidden = true; label.hidden = true;
        li.setAttribute('data-done', '1');                      // the log's own "decision" event must not say it twice
        li.appendChild(el('div', 'agc-qa', decision === 'decline' ? 'You declined.' : (decision === 'always' ? 'You allowed it here, always' : 'You allowed it once') + (net.checked ? ', with the web.' : '.')));
      }).catch(function (e) {
        all.forEach(function (n) { n.disabled = false; });
        toast(e.status === 403 ? 'Commands are approved only at this Mac.' : 'Could not send your decision: ' + e.message);
      });
    }
    once.addEventListener('click', function () { decide('once'); });
    always.addEventListener('click', function () { decide('always'); });
    no.addEventListener('click', function () { decide('decline'); });
    S.card.steps.appendChild(li);
    once.focus({ preventScroll: true });
  }

  // A decision seen in the log (the page was away, or another tab decided): settle the card.
  function drawDecision(eff) {
    var li = S.card && S.card.steps.querySelector('[data-a="' + eff.id + '"]');
    if (!li || li.getAttribute('data-done')) return;
    li.setAttribute('data-done', '1');
    Array.prototype.forEach.call(li.querySelectorAll('.agc-qc, .agc-net'), function (n) { n.hidden = true; });
    li.appendChild(el('div', 'agc-qa', eff.decision === 'decline' ? 'Declined.' : 'Allowed' + (eff.decision === 'always' ? ' here, always' : ' once') + (eff.network ? ', with the web.' : '.')));
  }

  // Commands the owner allowed for good in this project, each with a way to take it back.
  function drawAllowed() {
    var c = S.card, run = S.run;
    if (!c || !run || !run.ranCommands) return;
    api('GET', base(run.pid, '/commands')).then(function (list) {
      if (S.run !== run || !list.length) return;
      var box = el('div', 'agc-allow');
      box.appendChild(el('div', 'agc-qa', 'Commands allowed here without asking:'));
      list.forEach(function (item) {
        var row = el('div', 'agc-arow');
        row.appendChild(el('code', 'agc-cmd', item.cmd + (item.network ? '  (web)' : '')));
        var forget = el('button', 'agc-link', 'Forget'); forget.type = 'button';
        forget.addEventListener('click', function () {
          forget.disabled = true;
          api('POST', base(run.pid, '/commands/forget'), { command: item.cmd }).then(function () { row.remove(); toast('That command will ask again'); })
            .catch(function (e) { forget.disabled = false; toast('Could not forget it: ' + e.message); });
        });
        row.appendChild(forget); box.appendChild(row);
      });
      c.end.appendChild(box);
    }).catch(function () { /* away from the Mac, or the list is empty */ });
  }

  function drawNote(eff) {
    if (!S.card || !eff.text) return;
    S.card.steps.appendChild(el('li', 'agc-note', eff.text));
  }

  function drawEnd(end) {
    var c = S.card, r = S.run;
    c.end.hidden = false;
    c.end.textContent = '';
    c.end.appendChild(el('p', 'agc-rep', end.report || statusText(r)));
    if (canUndoRun(r)) {
      var acts = el('div', 'agc-ea');
      var undo = el('button', 'agc-btn', 'Undo run'); undo.type = 'button';
      undo.addEventListener('click', function () { undoRun(undo); });
      var keep = el('button', 'agc-btn quiet', 'Keep'); keep.type = 'button';
      keep.addEventListener('click', function () { acts.hidden = true; });
      acts.appendChild(undo); acts.appendChild(keep);
      c.end.appendChild(acts);
    }
  }

  function draw(effects) {
    var c = S.card;
    if (!c) return;
    var cm = $('cm');
    c.atBottom = !cm || cm.scrollHeight - cm.scrollTop - cm.clientHeight < 80;
    effects.forEach(function (eff) {
      if (eff.t === 'meta') drawMeta();
      else if (eff.t === 'tool') addRow(eff);
      else if (eff.t === 'result') drawResult(eff);
      else if (eff.t === 'checkpoint') { drawCheckpoint(eff.cp); scheduleReload(eff.cp.changed); }
      else if (eff.t === 'question') drawQuestion(eff);
      else if (eff.t === 'approval') drawApproval(eff);
      else if (eff.t === 'decision') drawDecision(eff);
      else if (eff.t === 'note') drawNote(eff);
      else if (eff.t === 'live') { c.live.textContent = eff.text; c.live.hidden = !eff.text; }
      else if (eff.t === 'end') { drawEnd(eff.end); drawAllowed(); }
    });
    keepInView();
  }

  // The preview follows the work like it does after a chat answer: a changed page opens it, other changes refresh it.
  function showPreview(paths) {
    var html = (paths || []).some(function (p) { return /\.html?$/i.test(p); });
    if (html && typeof root.doPrev === 'function') root.doPrev(true);
    else if (typeof root.refreshPreview === 'function') root.refreshPreview();
  }

  // The editor follows the agent's work: after a changing step the files are read again and the changed one shown.
  function scheduleReload(paths) {
    clearTimeout(S.reloadTimer);
    S.reloadTimer = setTimeout(function () {
      if (S.reloading) { scheduleReload(paths); return; }
      S.reloading = true;
      VP.reload().then(function () { if (paths && paths[0] && VP.activate) VP.activate(paths[0]); showPreview(paths); })
        .catch(function () { /* the next step tries again */ }).then(function () { S.reloading = false; });
    }, 250);
  }

  // ── following a run ────────────────────────────────────────────────────
  function mount(run) {
    unmount();
    S.run = run; S.pid = run.pid;
    S.card = buildCard(run);
    var cm = $('cm');
    if (cm) cm.appendChild(S.card.root);
    drawMeta();
    VP.setBusy(true);
    var sb = $('sndb'), xb = $('stopb');
    if (sb) sb.style.display = 'none';
    if (xb) xb.style.display = '';
    S.clock = setInterval(function () { if (S.run && isActive(S.run)) drawMeta(); }, 1000);
  }

  function unmount() {
    clearTimeout(S.timer); clearInterval(S.clock); clearTimeout(S.reloadTimer);
    S.timer = null; S.clock = null;
  }

  function poll() {
    var run = S.run;
    if (!run || run.finished) return;
    api('GET', base(run.pid, '/' + run.id + '?since=' + run.since + '&wait=20')).then(function (d) {
      if (S.run !== run) return;
      S.lost = 0;
      var effects = [];
      (d.events || []).forEach(function (ev) { effects = effects.concat(applyEvent(run, ev)); });
      var cut = synthEnd(run, d);
      if (cut) effects = effects.concat(applyEvent(run, cut));
      draw(effects);
      if (run.finished) return finished(run);
      S.timer = setTimeout(poll, 100);
    }).catch(function (e) {
      if (S.run !== run) return;
      S.lost += 1;
      if (e.status === 404 || S.lost > 5) {
        toast('Lost touch with the agent. It may still be working: reload the page to see it.');
        return releaseUi();
      }
      S.timer = setTimeout(poll, 2000);
    });
  }

  function releaseUi() {
    unmount();
    VP.setBusy(false);
    var sb = $('sndb'), xb = $('stopb');
    if (sb) sb.style.display = '';
    if (xb) xb.style.display = 'none';
  }

  function finished(run) {
    releaseUi();
    drawMeta();
    VP.reload().then(function () {
      if (run.changed.length && VP.activate) VP.activate(run.changed[run.changed.length - 1]);
      showPreview(run.changed);
    }).catch(function () { /* the editor catches up when the project is opened again */ });
    if (root.hist) root.hist.push({ role: 'assistant', content: reportMessage(run), plain: reportMessage(run) });
    if (typeof root.doSaveSes === 'function') root.doSaveSes();
  }

  function confirmCloud() {
    var c = S.models && S.models.cloud;
    return ask({ title: 'Send this project to ' + (c ? c.label : 'the cloud') + '?',
      message: 'For this run, the goal and every file the agent reads go to ' + (c && c.host ? c.host : 'the cloud provider') +
        ', not only to this Mac. It counts against your monthly cap.', confirm: 'Send' });
  }

  function send(inp) {
    var text = inp.value.trim();
    if (!text) return;
    if (running()) { toast('The agent is still working. Stop it first, or wait for it to finish.'); return; }
    var go = S.model === 'cloud' ? confirmCloud() : Promise.resolve(true);
    return go.then(function (ok) {
      if (!ok) return;
      inp.value = ''; inp.style.height = 'auto'; inp.rows = 1;
      var h = root.hist || [];
      var earlier = historyFor(h);
      if (typeof root.addMsg === 'function') root.addMsg('user', text);
      h.push({ role: 'user', content: text, plain: text });
      return VP.begin(text).then(function (pm) {
        if (!pm) { if (root.addMsg) root.addMsg('assistant', 'The agent needs project mode, which is not available here.'); return; }
        S.pid = VP.id();
        var body = { goal: text, model: S.model, confirm_cloud: S.model === 'cloud', history: earlier };
        return api('POST', base(S.pid, '/run'), body).then(function (res) {
          var run = newRun(S.pid);
          run.id = res.run; run.goal = text; run.model = (res.model && res.model.label) || ''; run.maxSteps = (res.limits && res.limits.max_steps) || 30;
          run.startedAt = Date.now() / 1000;
          mount(run);
          poll();
          if (typeof root.doSaveSes === 'function') root.doSaveSes();      // the chat and its project are kept from the start
        });
      }).catch(function (e) {
        VP.setBusy(false);
        if (root.addMsg) root.addMsg('assistant', 'Could not start the agent: ' + e.message);
      });
    });
  }

  function stopRun() {
    var run = S.run;
    if (!isActive(run)) return Promise.resolve();
    run.status = 'stopping';
    drawMeta();
    return api('POST', base(run.pid, '/' + run.id + '/stop')).catch(function (e) { toast('Could not stop: ' + e.message); });
  }

  // A project was opened. A run that is still going is followed again. A run that ended while the page was away,
  // and whose report the chat does not have yet, is shown with its whole timeline.
  function adopt(pid) {
    S.pid = pid;
    loadMode();
    if (!S.available) return;
    api('GET', base(pid, '/runs')).then(function (runs) {
      if (S.pid !== pid || S.run) return;
      var last = (runs || [])[0];
      if (!last) return;
      var live = last.status === 'running' || last.status === 'waiting';
      if (!live && !(unreported(last))) return;
      var run = newRun(pid);
      run.id = last.run; run.goal = last.goal; run.startedAt = last.started || run.startedAt;
      mount(run);
      poll();
    }).catch(function () { /* no runs, or the agent is switched off */ });
  }

  // True when the chat ends with the goal of this run and nothing after it: the page that started it never saw it end.
  function unreported(last) {
    var h = root.hist || [];
    var m = h[h.length - 1];
    if (!m || m.role !== 'user') return false;
    return String(m.plain || m.content || '').trim().slice(0, 200) === String(last.goal || '').trim().slice(0, 200);
  }

  function leave() {
    unmount();
    if (S.run && isActive(S.run) && VP) VP.setBusy(false);
    S.run = null; S.card = null; S.pid = null;
  }

  function undoRun(btn) {
    var run = S.run;
    if (!canUndoRun(run)) return;
    return ask({ title: 'Undo the whole run?', message: 'Your files go back to how they were before the agent started. Every version stays in Versions.', confirm: 'Undo run' })
      .then(function (ok) {
        if (!ok) return;
        btn.disabled = true;
        return VP.restoreId(run.end.startCp).then(function () { toast('Undid the run'); btn.parentNode.hidden = true; })
          .catch(function (e) { btn.disabled = false; toast('Could not undo the run: ' + e.message); });
      });
  }

  function backTo(cp, btn) {
    return ask({ title: 'Go back to before this step?', message: 'Later steps are taken out of your files too. Every version stays in Versions.', confirm: 'Go back' })
      .then(function (ok) {
        if (!ok) return;
        btn.disabled = true;
        return VP.restoreId(cp.parent).then(function () { toast('Went back to before that step'); })
          .catch(function (e) { btn.disabled = false; toast('Could not go back: ' + e.message); });
      });
  }

  root.VibeAgent = { active: active, running: running, send: send, stop: stopRun, adopt: adopt, leave: leave, setMode: setMode,
    core: core, _state: S };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})(typeof window !== 'undefined' ? window : globalThis);
