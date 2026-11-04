/* Vibe coding agent on the page (docs/VIBE-AGENT-DESIGN.md, step 3b).

   Agent mode: the owner types a goal, the server runs the agent (codec_vibe_agent.py) and this
   file shows it. A run card in the chat column lists every step as it happens, asks the owner's
   questions inline, and ends with the report and "Undo run". The page follows the run by asking
   the server for news (long polling), so closing the tab loses nothing: opening the project again
   picks the run up.

   The top half is plain functions on events and text, so Node can test them. The bottom half draws
   the card. Nothing from a run (goal, file names, file text, replies) is ever put on the page as
   HTML: everything goes in with textContent. Loaded after static/vibe-projects.js.

   The composer's own commands, @file list, key table, selection block and the chips after a run live here too
   (P-069, docs/VIBE-COMPOSER-KEYS-DESIGN.md). */
(function (root) {
  'use strict';

  var STATUS_TEXT = { running: 'Working', waiting: 'Waiting for you', stopping: 'Stopping', done: 'Done', stopped: 'Stopped',
    limit: 'Stopped at a limit', failed: 'Failed', interrupted: 'Interrupted' };
  var ACTIVE = { running: 1, waiting: 1, stopping: 1 };
  var CONTINUABLE = { interrupted: 1, stopped: 1, limit: 1, failed: 1 };     // as codec_vibe_agent.CONTINUABLE (P-044)
  var LIVE_CHARS = 220;
  var HISTORY_TURNS = 4;
  var MAX_NOTE = 300;                                                       // as codec_vibe_agent_proto.MAX_NOTE_CHARS
  var MAX_PLAN = 4000;                                                      // as codec_vibe_agent_proto.MAX_PLAN_CHARS (P-047)
  var MAX_PLAN_NOTE = 600;                                                  // as codec_vibe_agent_proto.MAX_PLAN_NOTE_CHARS

  // ── what a run looks like, from its events ─────────────────────────────

  function newRun(pid) {
    return { pid: pid, id: null, status: 'running', goal: '', model: '', maxSteps: 30, step: 0, since: 0, startedAt: 0,
      startCp: null, steps: {}, checkpoints: [], changed: [], live: '', liveStep: 0, question: null, approval: null, canRun: true,
      ranCommands: false, end: null, finished: false, offset: 0, modelKind: 'local', continues: null, continuable: false,
      ownerStop: false, notes: [], todo: [], mode: 'default', planFrom: null, planReady: false };
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
        run.modelKind = (ev.model && ev.model.kind) || run.modelKind;
        run.continues = ev.continues || null;
        run.offset = ev.continues ? (ev.earlier_steps || 0) : 0;      // a continued run counts its own steps from 1
        run.notes = Array.isArray(ev.notes) ? ev.notes : [];          // the project notes it read (P-046)
        run.mode = ev.mode === 'plan' ? 'plan' : 'default';           // a plan run, or a run that follows a plan (P-047)
        run.planFrom = ev.plan_from || null;
        return [{ t: 'meta' }].concat(notesLine(run.notes) ? [{ t: 'note', step: 0, text: notesLine(run.notes) }] : [])
          .concat(planNote(run, ev) ? [{ t: 'note', step: 0, text: planNote(run, ev) }] : [])
          .concat(run.continues ? [{ t: 'note', step: 0, text: 'This run goes on from where the last one stopped' +
            (run.offset ? ' (' + run.offset + (run.offset === 1 ? ' step' : ' steps') + ' done before).' : '.') }] : [])
          .concat(run.canRun ? [] : [{ t: 'note', step: 0, text: 'Commands are off for this run: it was started away from this Mac.' }]);
      case 'message':
        return [{ t: 'message', id: ev.id, text: ev.text || '', at: readAt(run, ev.step) }];
      case 'todo':                                                    // the agent's own list (P-046)
        run.todo = todoItems(ev.items);
        return [{ t: 'todo', items: run.todo }];
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
        run.steps[ev.step] = { step: ev.step, name: ev.name, arg: ev.arg, ok: null, summary: '', output: '', cp: null, began: ev.t || 0, ended: 0 };
        if (ev.name === 'run_check') run.ranCommands = true;
        return [{ t: 'live', text: '' }, { t: 'meta' }, { t: 'tool', step: ev.step, name: ev.name, arg: ev.arg, began: ev.t || 0 }];
      case 'result': {
        var s = run.steps[ev.step] || (run.steps[ev.step] = { step: ev.step, name: ev.tool || '', arg: '', cp: null, began: 0 });
        s.ok = ev.ok !== false; s.summary = ev.summary || ''; s.output = ev.output || ''; s.name = ev.tool || s.name; s.ended = ev.t || 0;
        return [{ t: 'result', step: ev.step, ok: s.ok, summary: s.summary, output: s.output, name: s.name, began: s.began, ended: s.ended }];
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
      case 'context': {                                               // how full the last prompt was (P-049)
        var m = contextMeter(ev);
        if (!m) return [];
        run.context = m;
        return [{ t: 'context', meter: m }];
      }
      case 'compact': {                                               // old steps folded into a note (P-049)
        var k = compactText(ev);
        return k ? [{ t: 'compact', head: k.head, summary: k.summary }] : [];
      }
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
    if (at !== -1) {                                // the tool after a todo block, once it is written (P-046)
      var re = /@@[ \t]*([A-Za-z_]+):?[ \t]*([^\n]*)/g, rest = text.slice(at), m, pick = null;
      while ((m = re.exec(rest))) { pick = m; if (m[1].toLowerCase() !== 'todo') break; }
      if (pick) tool = toolLabel(pick[1].toLowerCase(), (pick[2] || '').trim());
    }
    var out = sentence.slice(-LIVE_CHARS);
    return tool ? (out ? out + ' ' : '') + tool + '...' : out;
  }

  function toolLabel(name, arg) {
    var a = arg ? ' ' + arg : '';
    switch (name) {
      case 'list_files': return 'Listing files' + (arg ? ' in ' + arg : '');
      case 'read_file': return 'Reading' + a;
      case 'read_output': return 'Reading kept output' + (arg ? ' of result ' + arg : '');
      case 'search': return 'Searching' + a;
      case 'edit_file': return 'Editing' + a;
      case 'write_file': return 'Writing' + a;
      case 'delete_file': return 'Deleting' + a;
      case 'run_check': return 'Running' + (arg ? ' ' + arg : ' a check');
      case 'ask': return 'Asking you a question';
      case 'finish': return 'Finishing';
      case 'todo': return 'Updating its todo list';
      default: return name;
    }
  }

  function statusText(run) { return STATUS_TEXT[run.status] || run.status; }
  function stepText(run) {
    var own = Math.max(0, run.step - (run.offset || 0));
    if (run.finished) return own + (own === 1 ? ' step' : ' steps');
    return own ? 'Step ' + Math.min(own, run.maxSteps) + ' of ' + run.maxSteps : 'Starting';
  }

  // The owner's note follows the result of step `step`, so the agent reads it at the next one (counted in this run).
  function readAt(run, step) { return Math.max(1, (step || 0) + 1 - (run.offset || 0)); }

  // Continue is offered when the server says so (the project's newest run, ended without reaching its goal).
  function canContinue(run) { return !!(run && run.finished && run.continuable && CONTINUABLE[run.status]); }

  // While a run works, a message of 1 to 300 characters is queued for it; a longer one waits in the box.
  function noteFits(text) { var t = String(text || '').trim(); return t.length > 0 && t.length <= MAX_NOTE; }

  // What Continue adds to the chat, so a reload still finds the run (see unreported()).
  function continueLabel(goal) { return 'Continue: ' + String(goal || '').trim(); }
  function isActive(run) { return !!run && !run.finished && !!ACTIVE[run.status]; }

  // ── plan mode (P-047, docs/VIBE-AGENT-PLAN-MODE-DESIGN.md) ──
  // The line a run's card starts with when it plans, or follows a plan; '' for any other run.
  function planNote(run, ev) {
    if (run.mode === 'plan') return 'Plan mode: the agent reads the project and writes a plan. It changes no file except PLAN.md.';
    if (ev && ev.plan) return ev.plan_edited ? 'This run follows your version of the plan.' : 'This run follows the plan you chose.';
    return '';
  }

  // The plan card's choices are offered when the server says so: a plan run that ended done and is the project's newest.
  function canUsePlan(run) { return !!(run && run.finished && run.mode === 'plan' && run.planReady && run.status === 'done'); }

  // The owner's text for a choice: his own version of the plan (build) or what should change (again).
  function planTextFits(decision, text) {
    var t = String(text || '').trim();
    return t.length > 0 && t.length <= (decision === 'again' ? MAX_PLAN_NOTE : MAX_PLAN);
  }

  // The body of POST .../agent/{run}/plan. 'build' with no text builds the plan as written. confirm_cloud is true only
  // for the cloud model and an explicit yes from its dialog.
  function planRequest(decision, text, model, confirmed) {
    var cloud = model === 'cloud', again = decision === 'again', t = String(text || '').trim();
    var body = { decision: again ? 'again' : 'build', model: cloud ? 'cloud' : 'local', confirm_cloud: cloud && confirmed === true };
    if (again) body.note = t;
    else if (t) body.plan = t;
    return body;
  }

  // What a choice adds to the chat, so a reload still finds the new run (see unreported()).
  function planLabel(decision, goal) { return (decision === 'again' ? 'Plan again: ' : 'Build the plan: ') + String(goal || '').trim(); }

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

  // The checkpoint the report's chat row stores (P-077): the run's newest one, or its start when it changed nothing.
  // Rewind to here on that row goes back to the files as the run left them.
  function endCheckpoint(run) {
    var cps = (run && run.checkpoints) || [];
    if (cps.length) return cps[cps.length - 1].id || null;
    return (run && ((run.end && run.end.startCp) || run.startCp)) || null;
  }

  // The project notes a run read at its start, each with its size, or why it was not read (P-046).
  function fmtSize(n) {
    n = Math.max(0, n || 0);
    return n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(1) + ' KB' : (n / 1048576).toFixed(1) + ' MB';
  }
  function notesLine(notes) {
    var parts = (notes || []).filter(function (f) { return f && typeof f.path === 'string' && f.path; }).map(function (f) {
      if (f.skipped && !f.shown) return f.path + ' (not read: ' + f.skipped + ')';
      return f.path + ' (' + fmtSize(f.size) + (f.cut ? ', the first ' + f.shown + ' characters' : '') + ')';
    });
    return parts.length ? 'Project notes read: ' + parts.join(', ') + '.' : '';
  }

  // The agent's todo list, as the server sends it: text and done only, at most 12 items.
  function todoItems(items) {
    return (Array.isArray(items) ? items : []).filter(function (i) { return i && typeof i.text === 'string' && i.text.trim(); })
      .slice(0, 12).map(function (i) { return { text: i.text, done: i.done === true }; });
  }

  // The context meter (P-049): how full the last prompt was, against the cap, in the model's tokens. The server sends
  // the model's own count when it has one, else its estimate, marked. Display only: nothing on the page decides on it.
  function fmtCount(n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ','); }
  function compactText(ev) {
    var a = ev && ev.first, b = ev && ev.last;
    if (typeof a !== 'number' || typeof b !== 'number' || b < a) return null;
    return { head: 'Steps ' + a + ' to ' + b + ' folded into a short note to save space',
      summary: String(ev.summary || '').slice(0, 600) };
  }
  function contextMeter(ev) {
    var used = ev && ev.used, cap = ev && ev.cap;
    if (typeof used !== 'number' || typeof cap !== 'number' || !(cap > 0) || !(used >= 0)) return null;
    used = Math.round(used); cap = Math.round(cap);
    var pct = Math.min(100, Math.round(used * 100 / cap)), est = ev.estimate !== false;
    return { used: used, cap: cap, pct: pct, estimate: est, high: pct >= 85,
      text: 'Context ' + fmtCount(used) + ' of ' + fmtCount(cap) + ' tokens' + (est ? ' (estimate)' : '') };
  }

  // A run read from its log after a dashboard restart has no end event: the server says it is over and calls it interrupted.
  function synthEnd(run, view) {
    if (!view || !view.finished || run.finished) return null;
    var status = !view.status || view.status === 'running' || view.status === 'waiting' ? 'interrupted' : view.status;
    return { seq: run.since, type: 'end', status: status, steps: run.step, start_checkpoint: run.startCp,
      report: status === 'interrupted' ? 'The agent stopped before it finished (CODEC was restarted). Your files are as the last step left them.' : '' };
  }

  // ── the composer: commands, @file, keys, links (P-069, docs/VIBE-COMPOSER-KEYS-DESIGN.md) ──

  // Our own commands. `needs` names the feature that must exist here for the command to be listed and to run.
  var COMMANDS = [
    { name: 'help', needs: '', text: 'List these commands' },
    { name: 'stop', needs: '', text: 'Stop the agent, or the answer being written' },
    { name: 'undo', needs: 'projects', text: 'Undo the last AI change' },
    { name: 'clear', needs: '', text: 'Start a new chat and project (this one stays in Projects)' },
    { name: 'agent', needs: 'agent', text: 'Agent mode: the agent reads, edits and checks files step by step' },
    { name: 'chat', needs: 'agent', text: 'Chat mode: one answer, its files written behind a checkpoint' },
    { name: 'model', needs: 'cloud', text: 'Pick the model for the next message: /model local or /model cloud' }
  ];

  function commandList(caps) {
    caps = caps || {};
    return COMMANDS.filter(function (c) { return !c.needs || !!caps[c.needs]; });
  }

  // A message that is exactly /word or "/word text" and names a command listed here; anything else is sent as text.
  function parseCommand(text, caps) {
    var m = /^\/([A-Za-z]+)(?:\s+([\s\S]*))?$/.exec(String(text || '').trim());
    if (!m) return null;
    var name = m[1].toLowerCase();
    if (!commandList(caps).some(function (c) { return c.name === name; })) return null;
    return { name: name, arg: String(m[2] || '').trim() };
  }

  function helpText(caps) {
    return 'Commands (type one alone in the box):\n' + commandList(caps).map(function (c) { return '/' + c.name + ' - ' + c.text; }).join('\n') +
      '\nType @ to add the path of one of the project\'s files. Any other /word is sent as it is.';
  }

  // /model only selects: what it picks is asked about again (the cloud dialog) before anything is sent.
  function modelChoice(arg, models) {
    var a = String(arg || '').trim().toLowerCase(), cloud = models && models.cloud;
    if (!a) return { show: true };
    if (a === 'local') return { model: 'local' };
    if (a !== 'cloud') return { error: 'Use /model local or /model cloud.' };
    if (!cloud) return { error: 'No cloud model is set up.' };
    if (!cloud.ready) return { error: 'The cloud model is not ready: ' + (cloud.reason || 'not ready') + '.' };
    return { model: 'cloud' };
  }

  function modelsText(current, models) {
    var cloud = models && models.cloud;
    return 'Model for the next message: ' + (current === 'cloud' && cloud ? cloud.label + ' (cloud)' : 'Local model') +
      '.\n/model local - this Mac' + (cloud ? '\n/model cloud - ' + cloud.label + ' (you are asked before anything is sent)' : '');
  }

  // Runs one parsed command through the page's own functions (calls): say, setMode, keep, model, stop, undo, clear.
  function runCommand(cmd, calls, caps, models, current) {
    switch (cmd.name) {
      case 'help': calls.say(helpText(caps)); return true;
      case 'stop': calls.stop(); return true;
      case 'undo': calls.undo(); return true;
      case 'clear': calls.clear(); return true;
      case 'agent': case 'chat':
        calls.setMode(cmd.name);
        if (cmd.arg) calls.keep(cmd.arg);
        return true;
      case 'model': {
        var ch = modelChoice(cmd.arg, models);
        if (ch.model) calls.model(ch.model);
        else calls.say(ch.error || modelsText(current, models));
        return true;
      }
      default: return false;
    }
  }

  // The body of POST .../agent/run. confirm_cloud is true only for the cloud model and an explicit yes from its dialog.
  function runRequest(goal, model, confirmed, history) {
    var cloud = model === 'cloud';
    return { goal: goal, model: cloud ? 'cloud' : 'local', confirm_cloud: cloud && confirmed === true, history: history || [] };
  }

  // The @word being typed at the caret: { start (the @), query } or null. It starts at the start or after a space.
  function mentionAt(text, caret) {
    var before = String(text || '').slice(0, typeof caret === 'number' ? caret : String(text || '').length);
    var m = /(^|\s)@([^\s@]{0,120})$/.exec(before);
    return m ? { start: before.length - m[2].length - 1, query: m[2] } : null;
  }

  // The project's paths that hold the query: names starting with it first, then paths starting with it, then the rest.
  function mentionMatches(paths, query, max) {
    var q = String(query || '').toLowerCase();
    var out = [];
    (paths || []).forEach(function (p) {
      var low = String(p).toLowerCase(), name = low.split('/').pop();
      if (q && low.indexOf(q) === -1) return;
      out.push({ p: p, r: !q ? 0 : name.indexOf(q) === 0 ? 0 : low.indexOf(q) === 0 ? 1 : 2 });
    });
    out.sort(function (a, b) { return a.r - b.r || (a.p < b.p ? -1 : a.p > b.p ? 1 : 0); });
    return out.slice(0, max || 8).map(function (x) { return x.p; });
  }

  // Puts a picked path in place of the "@query". Only a path of the open project (paths) goes in.
  function insertMention(text, at, caret, path, paths) {
    if (!at || (paths || []).indexOf(path) === -1) return null;
    text = String(text || '');
    var before = text.slice(0, at.start), after = text.slice(caret);
    var put = path + (/^\s/.test(after) ? '' : ' ');
    return { text: before + put + after, caret: before.length + put.length };
  }

  // What a key in the composer does: 'send', 'stop', a list key ('next', 'prev', 'pick', 'close') or ''.
  function composerKey(e, ctx) {
    ctx = ctx || {};
    if (!e || e.isComposing || e.keyCode === 229) return '';        // an IME is composing: its Enter confirms the word
    var k = e.key;
    if (ctx.mention) {
      if (k === 'ArrowDown') return 'next';
      if (k === 'ArrowUp') return 'prev';
      if ((k === 'Enter' && !e.shiftKey && !e.metaKey && !e.ctrlKey && !e.altKey) || (k === 'Tab' && !e.shiftKey)) return 'pick';
      if (k === 'Escape') return 'close';
    }
    if (k === 'Escape') return ctx.running ? 'stop' : '';
    if (k !== 'Enter') return '';
    if (e.metaKey || e.ctrlKey) return 'send';
    if (e.shiftKey || e.altKey) return '';
    return ctx.touch ? '' : 'send';                                // a touch keyboard's Enter is a new line; the button sends
  }

  var PROJECT_ID = /^[a-z0-9][a-z0-9-]{2,60}$/;      // as codec_vibe_projects._PID_RE

  // /vibe?project=<id>: the id when it is a project id, else null (the link is then ignored).
  function projectFromQuery(search) {
    var id = null;
    try { id = new URLSearchParams(String(search || '')).get('project'); } catch (e) { return null; }
    return typeof id === 'string' && PROJECT_ID.test(id) ? id : null;
  }

  // The check command a project's files point to, from our own short list, or ''.
  function checkCommand(paths, pkgText) {
    try {
      var pkg = JSON.parse(pkgText || 'null');
      var t = pkg && pkg.scripts && pkg.scripts.test;
      if (typeof t === 'string' && t.trim() && !/no test specified/i.test(t)) return 'npm test';
    } catch (e) { /* not valid JSON */ }
    return (paths || []).some(function (p) { return /(^|\/)(test_[^/]*|[^/]*_test)\.py$/.test(p); }) ? 'python3 -m pytest -q' : '';
  }

  // The chips shown after a run, on this Mac: each opens the Run dialog on its option.
  function afterRunChips(paths, pkgText) {
    var out = [];
    var check = checkCommand(paths, pkgText);
    if (check) out.push({ label: 'Run the checks', mode: 'run', command: check });
    if ((paths || []).some(function (p) { return /\.html?$/i.test(p); })) out.push({ label: 'Show it on localhost', mode: 'serve', command: '' });
    return out;
  }

  var MAX_SELECTION = 3000;

  // The editor's selection as a block for the message: whole lines from `start`, at most MAX_SELECTION characters.
  function selectionBlock(path, start, lines) {
    var kept = [], used = 0;
    (lines || []).some(function (ln) {
      ln = String(ln);
      if (kept.length && used + ln.length + 1 > MAX_SELECTION) return true;
      kept.push(ln.slice(0, MAX_SELECTION));
      used += ln.length + 1;
      return false;
    });
    var last = start + kept.length - 1;
    var left = (lines || []).length - kept.length;
    return '[SELECTION ' + path + ':' + (last > start ? start + '-' + last : start) + ']\n' + kept.join('\n') + '\n[END SELECTION]' +
      (left ? '\n(' + left + (left === 1 ? ' more line' : ' more lines') + ' of the selection left out)' : '');
  }

  function clock(t) {
    if (typeof t !== 'number' || !isFinite(t) || t <= 0) return '';
    var d = new Date(t * 1000);
    var two = function (n) { return (n < 10 ? '0' : '') + n; };
    return two(d.getHours()) + ':' + two(d.getMinutes()) + ':' + two(d.getSeconds());
  }

  // A step row's tooltip: when it started and, once its result is in, when it ended.
  function stepTitle(n, t0, t1) {
    var a = clock(t0), b = clock(t1);
    if (!a) return 'Step ' + n;
    return 'Step ' + n + ': ' + (b ? a + ' to ' + b + ' (' + Math.max(0, Math.round(t1 - t0)) + ' s)' : 'started ' + a);
  }

  var core = { newRun: newRun, synthEnd: synthEnd, applyEvent: applyEvent, describeLive: describeLive, toolLabel: toolLabel, statusText: statusText,
    stepText: stepText, isActive: isActive, fmtElapsed: fmtElapsed, canUndoRun: canUndoRun, historyFor: historyFor,
    reportMessage: reportMessage, endCheckpoint: endCheckpoint, readAt: readAt, canContinue: canContinue, noteFits: noteFits, continueLabel: continueLabel,
    commandList: commandList, parseCommand: parseCommand, helpText: helpText, modelChoice: modelChoice, runCommand: runCommand,
    runRequest: runRequest, mentionAt: mentionAt, mentionMatches: mentionMatches, insertMention: insertMention,
    composerKey: composerKey, projectFromQuery: projectFromQuery, checkCommand: checkCommand, afterRunChips: afterRunChips,
    selectionBlock: selectionBlock, clock: clock, stepTitle: stepTitle,
    notesLine: notesLine, todoItems: todoItems, planNote: planNote, canUsePlan: canUsePlan, planTextFits: planTextFits,
    planRequest: planRequest, planLabel: planLabel,
    contextMeter: contextMeter, compactText: compactText };

  if (typeof document === 'undefined') {            // Node: only the plain functions
    if (typeof module !== 'undefined' && module.exports) module.exports = core;
    return;
  }

  // ── the page ───────────────────────────────────────────────────────────

  var VP = root.VibeProj;
  var S = { available: false, mode: 'chat', model: 'local', models: null, run: null, pid: null, card: null, timer: null,
    clock: null, reloadTimer: null, reloading: false, lost: 0, queue: null, lines: {}, compact: false, editor: null, plan: false };
  var M = { open: false, at: null, items: [], sel: 0, query: null };      // the @file list under the composer (P-069)
  var RUN_HINT = 'Message the agent (it reads it at its next step). Esc stops it.';
  var KEY = 'vibeAgentMode';
  var COMPACT_KEY = 'vibeAgentCompact';

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
  var TOOL_ICON = { list_files: 'list', read_file: 'read', read_output: 'read', search: 'search', edit_file: 'edit', write_file: 'write', delete_file: 'trash',
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
    if (sel) sel.hidden = !(S.available && S.models && S.models.cloud);   // chat and agent share the choice (D-22)
    var plan = $('amPlan');                                  // Plan first: the next agent run plans before it builds (P-047)
    if (plan) {
      plan.hidden = !(S.available && S.mode === 'agent');
      plan.classList.toggle('sel', S.plan);
      plan.setAttribute('aria-pressed', S.plan ? 'true' : 'false');
    }
    var vi = $('vi');
    if (vi) {
      if (!vi.dataset.chatHint) vi.dataset.chatHint = vi.placeholder;
      vi.placeholder = running() ? RUN_HINT : S.available && S.mode === 'agent' ? 'Tell the agent what to do...' : vi.dataset.chatHint;
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
    if (agent && !$('amPlan')) {                                  // P-047: built here, next to Agent
      var plan = el('button', 'amb', 'Plan first');
      plan.type = 'button'; plan.id = 'amPlan'; plan.hidden = true; plan.setAttribute('aria-pressed', 'false');
      plan.title = 'The next agent run reads the project and writes a plan. Nothing changes until you choose to build it.';
      plan.addEventListener('click', function () { S.plan = !S.plan; renderMode(); });
      agent.parentNode.insertBefore(plan, agent.nextSibling);
    }
    if (sel) sel.addEventListener('change', function () { S.model = sel.value === 'cloud' ? 'cloud' : 'local'; });
    var vi = $('vi');
    if (vi) vi.addEventListener('keydown', function (e) {        // Esc in the composer stops a working agent (P-044)
      if (e.key === 'Escape' && !e.isComposing && running() && !M.open) { e.preventDefault(); stopRun(); }
    });
    if (vi) {                                                     // the @file list follows the caret (P-069)
      vi.setAttribute('aria-autocomplete', 'list');
      vi.setAttribute('aria-controls', 'vmention');
      vi.setAttribute('aria-expanded', 'false');
      vi.addEventListener('input', updateMention);
      vi.addEventListener('click', updateMention);
      vi.addEventListener('keyup', function (e) { if (/^(ArrowLeft|ArrowRight|Home|End)$/.test(e.key)) updateMention(); });
      vi.addEventListener('blur', function () { setTimeout(function () { if (document.activeElement !== vi) closeMention(); }, 0); });
    }
    S.compact = lsGet(COMPACT_KEY) === '1';
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
    var views = el('div', 'agc-views');                      // how the steps are shown (P-069)
    var compact = el('button', 'agc-link agc-tog agc-cmp', 'Compact'); compact.type = 'button';
    compact.title = 'One line per step';
    var raw = el('button', 'agc-link agc-tog', 'Raw replies'); raw.type = 'button';
    raw.title = 'Show the model\'s own reply under each step, as the run log keeps it';
    views.appendChild(compact); views.appendChild(raw);
    head.appendChild(goal); head.appendChild(meta); head.appendChild(views); head.appendChild(stop);
    var steps = el('ol', 'agc-steps');
    var liveLine = el('div', 'agc-live'); liveLine.hidden = true;
    liveLine.setAttribute('aria-live', 'polite');
    var end = el('div', 'agc-end'); end.hidden = true;
    card.appendChild(head); card.appendChild(steps); card.appendChild(liveLine); card.appendChild(end);
    var c = { root: card, goal: goal, st: st, model: model, step: step, time: time, stop: stop, steps: steps, live: liveLine, end: end,
      rows: {}, atBottom: true, compactBtn: compact, rawBtn: raw, raw: false, replyStep: 0, replyBusy: false, run: run };
    compact.addEventListener('click', function () { setCompact(!S.compact); });
    raw.addEventListener('click', function () { setRaw(c, !c.raw); });
    showViews(c);
    return c;
  }

  function showViews(c) {
    c.root.classList.toggle('agc-compact', S.compact);
    c.compactBtn.setAttribute('aria-pressed', S.compact ? 'true' : 'false');
    c.root.classList.toggle('agc-raw-on', c.raw);
    c.rawBtn.setAttribute('aria-pressed', c.raw ? 'true' : 'false');
  }

  // Compact is remembered in this browser; it applies to every card on the page, an earlier run's card included.
  function setCompact(on) {
    S.compact = !!on;
    lsSet(COMPACT_KEY, S.compact ? '1' : '0');
    Array.prototype.forEach.call(document.querySelectorAll('.agc'), function (card) {
      card.classList.toggle('agc-compact', S.compact);
      var b = card.querySelector('.agc-cmp');
      if (b) b.setAttribute('aria-pressed', S.compact ? 'true' : 'false');
    });
  }

  // Raw replies are off at every load. On, the card asks the server for its own run's replies by name (?replies=N):
  // the normal event stream never carries them. An earlier run's card asks for that run.
  function setRaw(c, on) {
    c.raw = !!on;
    showViews(c);
    if (c.raw && c.run && c.run.id) fetchReplies(c, c.run);
  }

  function fetchReplies(c, run) {
    if (c.replyBusy) return;
    c.replyBusy = true;
    api('GET', base(run.pid, '/' + run.id + '?since=' + run.since + '&replies=' + c.replyStep)).then(function (d) {
      drawReplies(c, d.replies);
    }).catch(function (e) { toast('Could not load the replies: ' + e.message); })
      .then(function () { c.replyBusy = false; });
  }

  function drawReplies(c, replies) {
    (replies || []).forEach(function (r) {
      if (!r || typeof r.step !== 'number' || r.step <= c.replyStep) return;
      c.replyStep = r.step;
      var box = el('div', 'agc-raw');
      box.appendChild(el('div', 'agc-rawh', 'Reply at step ' + Math.max(1, r.step - ((c.run && c.run.offset) || 0)) +
        (r.cut ? ' (cut: ' + r.chars + ' characters in all)' : '') + (r.redacted ? ' (secrets hidden)' : '')));
      box.appendChild(el('pre', 'agc-out', r.text || ''));
      var row = c.rows[r.step];
      if (row) row.li.appendChild(box);
      else { var li = el('li', 'agc-rawli'); li.appendChild(box); c.steps.appendChild(li); }
    });
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
    li.title = stepTitle(ownStep(eff.step), eff.began, 0);        // hover: when the step ran (P-069)
    c.steps.appendChild(li);
    c.rows[eff.step] = { li: li, head: headBtn, sum: sum, badge: badge, body: body, out: out, acts: acts };
  }

  function ownStep(step) { return Math.max(1, (step || 0) - ((S.run && S.run.offset) || 0)); }

  function drawResult(eff) {
    var row = S.card && S.card.rows[eff.step];
    if (!row) return;
    row.li.title = stepTitle(ownStep(eff.step), eff.began, eff.ended);
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

  // Old steps folded into the model's note (P-049): one row; the note opens under it. Text only, never HTML.
  function drawCompact(eff) {
    if (!S.card) return;
    var li = el('li', 'agc-note agc-fold'), d = el('details'), sm = el('summary', null, eff.head);
    d.appendChild(sm);
    if (eff.summary) d.appendChild(el('div', 'agc-fold-t', eff.summary));
    li.appendChild(d);
    S.card.steps.appendChild(li);
  }

  // The context meter right under the card's head (P-049), made at the first context event. Display only.
  function drawContext(m) {
    var c = S.card;
    if (!c) return;
    if (!c.ctx) {
      var box = el('div', 'agc-ctx');
      box.setAttribute('role', 'meter');
      box.setAttribute('aria-label', 'How full the agent\'s context is');
      box.setAttribute('aria-valuemin', '0');
      var bar = el('span', 'agc-ctx-bar'), fill = el('span', 'agc-ctx-fill'), label = el('span', 'agc-ctx-t');
      bar.appendChild(fill); box.appendChild(bar); box.appendChild(label);
      c.root.insertBefore(box, c.goal.parentNode.nextSibling);
      c.ctx = { box: box, fill: fill, label: label };
    }
    c.ctx.box.setAttribute('aria-valuemax', String(m.cap));
    c.ctx.box.setAttribute('aria-valuenow', String(Math.min(m.used, m.cap)));
    c.ctx.box.setAttribute('aria-valuetext', m.text);
    c.ctx.box.classList.toggle('high', m.high);
    c.ctx.fill.style.width = m.pct + '%';
    c.ctx.label.textContent = m.text;
  }

  // The agent's todo list as a checklist under the card's head (P-046). Its boxes show the state; they cannot be clicked.
  function drawTodo(items) {
    var c = S.card;
    if (!c) return;
    if (!c.todo) {
      c.todo = el('ul', 'agc-todo');
      c.todo.setAttribute('aria-label', 'The agent\'s todo list');
      c.root.insertBefore(c.todo, c.steps);
    }
    c.todo.textContent = '';
    c.todo.hidden = !items.length;
    items.forEach(function (it) {
      var li = el('li', it.done ? 'done' : null);
      var box = el('input'); box.type = 'checkbox'; box.checked = it.done; box.disabled = true;
      box.setAttribute('aria-label', it.done ? 'Done' : 'Not done yet');
      li.appendChild(box); li.appendChild(el('span', null, it.text));
      c.todo.appendChild(li);
    });
  }

  function drawEnd(end) {
    var c = S.card, r = S.run;
    c.end.hidden = false;
    c.end.textContent = '';
    c.end.appendChild(el('p', 'agc-rep', end.report || statusText(r)));
    if (canUsePlan(r)) drawPlan(c, r);                      // the plan's three choices (P-047)
    if (canContinue(r)) {                                   // goes on from where it stopped, with a fresh step budget
      var more = el('div', 'agc-ea');
      var go = el('button', 'agc-btn', 'Continue'); go.type = 'button';
      go.setAttribute('aria-label', 'Continue the run from where it stopped');
      go.addEventListener('click', function () { continueRun(r, go); });
      more.appendChild(go);
      c.end.appendChild(more);
    }
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

  // The owner's message in the timeline, with a line that says where it is.
  function drawYou(text, state) {
    var li = el('li', 'agc-you');
    li.appendChild(el('div', 'agc-yt', 'You: ' + text));
    var st = el('div', 'agc-qa', state);
    li.appendChild(st);
    S.card.steps.appendChild(li);
    return st;
  }

  // The agent read one of the owner's messages. A line this page drew is updated; one sent from elsewhere (or
  // seen again after a reload) gets its own line.
  function drawMessage(eff) {
    var said = 'The agent read this at step ' + eff.at + '.';
    var q = S.queue;
    if (q && q.run === S.run.id && (eff.id === q.id || (!q.id && eff.text === q.text))) S.queue = null;
    else q = null;
    var line = (q && q.line) || S.lines[eff.id];
    delete S.lines[eff.id];
    if (line) line.textContent = said;
    else drawYou(eff.text, said);
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
      else if (eff.t === 'todo') drawTodo(eff.items);
      else if (eff.t === 'message') drawMessage(eff);
      else if (eff.t === 'context') drawContext(eff.meter);
      else if (eff.t === 'compact') drawCompact(eff);
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
    S.lines = {};
    renderMode();
    S.clock = setInterval(function () { if (S.run && isActive(S.run)) drawMeta(); }, 1000);
  }

  function unmount() {
    clearTimeout(S.timer); clearInterval(S.clock); clearTimeout(S.reloadTimer);
    S.timer = null; S.clock = null;
  }

  function poll() {
    var run = S.run;
    if (!run || run.finished) return;
    var card = S.card;
    var raw = card && card.raw ? '&replies=' + card.replyStep : '';         // only while Raw replies is on (P-069)
    api('GET', base(run.pid, '/' + run.id + '?since=' + run.since + '&wait=20' + raw)).then(function (d) {
      if (S.run !== run) return;
      S.lost = 0;
      var effects = [];
      (d.events || []).forEach(function (ev) { effects = effects.concat(applyEvent(run, ev)); });
      var cut = synthEnd(run, d);
      if (cut) effects = effects.concat(applyEvent(run, cut));
      if (d.finished) run.continuable = !!d.continuable;          // the server says whether Continue can work
      if (d.finished) run.planReady = !!d.plan_ready;             // and whether the plan card's choices can (P-047)
      draw(effects);
      if (raw && S.card === card && card.raw) drawReplies(card, d.replies);
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
    renderMode();
  }

  function finished(run) {
    releaseUi();
    drawMeta();
    VP.reload().then(function () {
      if (run.changed.length && VP.activate) VP.activate(run.changed[run.changed.length - 1]);
      showPreview(run.changed);
    }).catch(function () { /* the editor catches up when the project is opened again */ })
      .then(function () { drawNext(run); });
    if (root.hist) root.hist.push({ role: 'assistant', content: reportMessage(run), plain: reportMessage(run), checkpoint: endCheckpoint(run) });
    if (typeof root.doSaveSes === 'function') root.doSaveSes();
    var q = S.queue;                          // a message the agent never read becomes the next goal (P-044)
    S.queue = null;
    if (!q || q.run !== run.id) return;
    if (run.ownerStop) {
      q.line.textContent = 'Not sent: you stopped the run. It is back in the message box.';
      backToComposer(q.text);
    } else {
      q.line.textContent = 'The run ended before reading this: it starts as the next goal.';
      startRun(q.text, null);
    }
  }

  function clearInput(inp) { if (inp) { inp.value = ''; inp.style.height = 'auto'; inp.rows = 1; } }

  function backToComposer(text) {
    var vi = $('vi');
    if (!vi) return;
    vi.value = vi.value.trim() ? text + '\n' + vi.value : text;
  }

  function confirmCloud() {
    if (!(root.CodecShell && root.CodecShell.ask)) return Promise.resolve(false);      // no dialog, no cloud run (P-069)
    var c = S.models && S.models.cloud;
    return ask({ title: 'Send this project to ' + (c ? c.label : 'the cloud') + '?',
      message: 'For this run, the goal and every file the agent reads go to ' + (c && c.host ? c.host : 'the cloud provider') +
        ', not only to this Mac. It counts against your monthly cap.', confirm: 'Send' });
  }

  // Chat mode shares the model choice with the agent (docs/VIBE-CHAT-ROUTE-DESIGN.md). Local is the default; when the
  // owner picked the cloud model the page asks before every chat message and sends it only after "Send".
  function chatCloud() { return !!(S.available && S.model === 'cloud'); }

  function confirmChatCloud() {
    if (!(root.CodecShell && root.CodecShell.ask)) return Promise.resolve(false);      // no dialog, no cloud
    var c = S.models && S.models.cloud;
    return Promise.resolve(root.CodecShell.ask({ title: 'Send this message to ' + (c ? c.label : 'the cloud') + '?',
      message: 'For this message, your text, the earlier messages of this chat and the project files in it go to ' +
        (c && c.host ? c.host : 'the cloud provider') + ', not only to this Mac. It counts against your monthly cap.', confirm: 'Send' }));
  }

  function send(inp) {
    var text = inp.value.trim();
    if (!text) return;
    if (running()) return queueNote(inp, text);
    return startRun(text, inp);
  }

  // While a run works, Enter sends one short message that the agent reads at its next step; a newer one replaces
  // one not read yet. If the run ends before reading it, it becomes the next goal (P-044).
  function queueNote(inp, text) {
    var run = S.run;
    if (!noteFits(text)) {
      toast('A message to a working agent is at most ' + MAX_NOTE + ' characters. Shorten it, or wait for the run to end.');
      return;
    }
    if (!S.card) return;
    clearInput(inp);
    var old = S.queue && S.queue.run === run.id ? S.queue : null;
    var q = { run: run.id, text: text, id: null, line: drawYou(text, 'Sending...') };
    S.queue = q;
    keepInView();
    return api('POST', base(run.pid, '/' + run.id + '/message'), { text: text }).then(function (res) {
      if (old && res.replaced) { old.line.textContent = 'Replaced by your newer message.'; delete S.lines[old.id]; }
      if (S.queue !== q) return;
      q.id = res.id;
      S.lines[res.id] = q.line;
      q.line.textContent = 'The agent reads this at its next step.';
    }).catch(function (e) {
      if (S.queue !== q) return;
      q.line.textContent = e.status === 409 ? 'The run is ending: this starts as the next goal.'
        : 'Not sent (' + e.message + '). It starts as the next goal when the run ends.';
    });
  }

  // Start a run on a goal. inp is the composer, or null for a queued message (put back if the owner says no).
  function startRun(text, inp) {
    var model = S.model === 'cloud' ? 'cloud' : 'local';       // fixed now: a /model or a list change while asking does not count
    var plan = S.plan === true;                                 // Plan first, fixed now too (P-047)
    var go = model === 'cloud' ? confirmCloud() : Promise.resolve(true);
    return go.then(function (ok) {
      if (ok !== true) { if (!inp) backToComposer(text); return; }
      clearInput(inp);
      var h = root.hist || [];
      var earlier = historyFor(h);
      if (typeof root.addMsg === 'function') root.addMsg('user', text);
      h.push({ role: 'user', content: text, plain: text });
      return VP.begin(text).then(function (pm) {
        if (!pm) { if (root.addMsg) root.addMsg('assistant', 'The agent needs project mode, which is not available here.'); return; }
        S.pid = VP.id();
        var body = runRequest(text, model, ok, earlier);         // confirm_cloud only after the dialog's yes
        if (plan) body.mode = 'plan';
        if (root._vibePrivate === true) body.temporary = true;   // a private chat: the run keeps no log (P-077)
        return api('POST', base(S.pid, '/run'), body).then(function (res) {
          if (plan) { S.plan = false; renderMode(); }           // plan mode is chosen per run
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
    run.ownerStop = true;
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

  // True when the chat ends with the goal of this run (or its Continue line) and nothing after it: the page that
  // started it never saw it end.
  function unreported(last) {
    var h = root.hist || [];
    var m = h[h.length - 1];
    if (!m || m.role !== 'user') return false;
    var said = String(m.plain || m.content || '').trim().slice(0, 200);
    return said === String(last.goal || '').trim().slice(0, 200) || said === continueLabel(last.goal).slice(0, 200) ||
      said === planLabel('build', last.goal).slice(0, 200) || said === planLabel('again', last.goal).slice(0, 200);
  }

  // Continue: a new run goes on from where this one stopped, with a fresh step budget. A cloud run asks again,
  // and commands follow this page's own place (the server decides from the request).
  function continueRun(old, btn) {
    if (S.run !== old || running() || !canContinue(old)) return;
    var cloud = old.modelKind === 'cloud';
    var go = cloud ? confirmCloud() : Promise.resolve(true);
    return go.then(function (ok) {
      if (!ok || S.run !== old || running()) return;
      btn.disabled = true;
      return api('POST', base(old.pid, '/' + old.id + '/continue'), { confirm_cloud: cloud }).then(function (res) {
        btn.hidden = true;
        old.continuable = false;
        var label = continueLabel(old.goal);
        if (typeof root.addMsg === 'function') root.addMsg('user', label);
        if (root.hist) root.hist.push({ role: 'user', content: label, plain: label });
        var run = newRun(old.pid);
        run.id = res.run; run.goal = old.goal; run.model = (res.model && res.model.label) || '';
        run.modelKind = (res.model && res.model.kind) || old.modelKind;
        run.maxSteps = (res.limits && res.limits.max_steps) || 30;
        run.offset = res.earlier_steps || 0;
        run.startedAt = Date.now() / 1000;
        mount(run);
        poll();
        if (typeof root.doSaveSes === 'function') root.doSaveSes();
      }).catch(function (e) { btn.disabled = false; toast('Could not continue: ' + e.message); });
    });
  }

  // The plan card (P-047): build it as written, change it first, or plan again with a line on what should change.
  // The plan and the owner's text go in as text; nothing starts before a click.
  function drawPlan(c, run) {
    var box = el('div', 'agc-plan');
    box.setAttribute('role', 'group'); box.setAttribute('aria-label', 'What to do with this plan');
    var row = el('div', 'agc-ea');
    var build = el('button', 'agc-btn', 'Build this plan'), edit = el('button', 'agc-btn quiet', 'Change it first'),
      again = el('button', 'agc-btn quiet', 'Plan again');
    build.title = 'Start a run that follows this plan';
    edit.title = 'Edit the plan, then build your version';
    again.title = 'Say what should change; the agent writes a new plan';
    [build, edit, again].forEach(function (b) { b.type = 'button'; row.appendChild(b); });
    var editForm = el('form', 'agc-pf'); editForm.hidden = true;
    var area = el('textarea', 'agc-pe'); area.setAttribute('aria-label', 'Your version of the plan'); area.maxLength = MAX_PLAN;
    var editRow = el('div', 'agc-ea');
    var mine = el('button', 'agc-btn', 'Build my version'); mine.type = 'submit';
    var back1 = el('button', 'agc-btn quiet', 'Cancel'); back1.type = 'button';
    editRow.appendChild(mine); editRow.appendChild(back1);
    editForm.appendChild(area); editForm.appendChild(editRow);
    var againForm = el('form', 'agc-qf agc-pa'); againForm.hidden = true;
    var line = el('input'); line.type = 'text'; line.maxLength = MAX_PLAN_NOTE; line.placeholder = 'What should change?';
    line.setAttribute('aria-label', 'What should change in the plan');
    var send = el('button', 'agc-btn', 'Send'); send.type = 'submit';
    var back2 = el('button', 'agc-btn quiet', 'Cancel'); back2.type = 'button';
    againForm.appendChild(line); againForm.appendChild(send); againForm.appendChild(back2);
    box.appendChild(row); box.appendChild(editForm); box.appendChild(againForm);
    var all = [build, edit, again, area, mine, back1, line, send, back2];
    function show(form) {
      row.hidden = !!form; editForm.hidden = form !== editForm; againForm.hidden = form !== againForm;
      if (form === editForm) { area.value = (run.end && run.end.report) || ''; area.focus({ preventScroll: true }); }
      if (form === againForm) line.focus({ preventScroll: true });
    }
    function choose(decision, text) {
      if (decision === 'again' || text) {
        if (!planTextFits(decision, text)) {
          toast(decision === 'again' ? 'Say in 1 to ' + MAX_PLAN_NOTE + ' characters what should change.'
            : 'The plan must have 1 to ' + MAX_PLAN + ' characters.');
          return;
        }
      }
      usePlan(run, decision, text, all, box);
    }
    build.addEventListener('click', function () { choose('build', ''); });
    edit.addEventListener('click', function () { show(editForm); });
    again.addEventListener('click', function () { show(againForm); });
    back1.addEventListener('click', function () { show(null); });
    back2.addEventListener('click', function () { show(null); });
    editForm.addEventListener('submit', function (e) {
      e.preventDefault();
      var text = area.value.trim();
      if (!text) { toast('The plan must have 1 to ' + MAX_PLAN + ' characters.'); return; }     // an emptied box builds nothing
      choose('build', text === String((run.end && run.end.report) || '').trim() ? '' : text);   // unchanged: as written
    });
    againForm.addEventListener('submit', function (e) { e.preventDefault(); choose('again', line.value); });
    c.end.appendChild(box);
  }

  // A choice on the plan card starts a new run, as Continue does: a cloud model asks first, and commands follow this
  // page's own place (the server decides from the request).
  function usePlan(old, decision, text, controls, box) {
    if (S.run !== old || running() || !canUsePlan(old)) return;
    var model = S.model === 'cloud' ? 'cloud' : 'local';
    var go = model === 'cloud' ? confirmCloud() : Promise.resolve(true);
    return go.then(function (ok) {
      if (ok !== true || S.run !== old || running()) return;
      controls.forEach(function (n) { n.disabled = true; });
      return api('POST', base(old.pid, '/' + old.id + '/plan'), planRequest(decision, text, model, ok)).then(function (res) {
        box.hidden = true;
        old.planReady = false;
        var label = planLabel(decision, old.goal);
        if (typeof root.addMsg === 'function') root.addMsg('user', label);
        if (root.hist) root.hist.push({ role: 'user', content: label, plain: label });
        var run = newRun(old.pid);
        run.id = res.run; run.goal = old.goal; run.model = (res.model && res.model.label) || '';
        run.modelKind = (res.model && res.model.kind) || model;
        run.maxSteps = (res.limits && res.limits.max_steps) || 30;
        run.mode = res.mode === 'plan' ? 'plan' : 'default';
        run.startedAt = Date.now() / 1000;
        mount(run);
        poll();
        if (typeof root.doSaveSes === 'function') root.doSaveSes();
      }).catch(function (e) {
        controls.forEach(function (n) { n.disabled = false; });
        toast('Could not start it: ' + e.message);
      });
    });
  }

  function leave() {
    unmount();
    if (S.run && isActive(S.run) && VP) VP.setBusy(false);
    S.run = null; S.card = null; S.pid = null; S.queue = null; S.lines = {};
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

  // ── after a run: the next things to do, as chips (P-069) ───────────────
  // On this Mac only (the Run dialog works only here). Each chip opens the Run dialog on its option; nothing runs
  // before the owner presses Start there.
  function pkgText() { return VP.fileText ? VP.fileText('package.json') : null; }

  function drawNext(run) {
    var c = S.card;
    if (!c || S.run !== run || !VP.onMac || !VP.onMac() || !VP.on() || VP.id() !== run.pid) return;
    var chips = afterRunChips(VP.paths(), pkgText());
    if (!chips.length) return;
    var box = el('div', 'agc-ea agc-next');
    box.setAttribute('aria-label', 'Next');
    chips.forEach(function (chip) {
      var b = el('button', 'agc-btn quiet', chip.label); b.type = 'button';
      b.addEventListener('click', function () {
        var now = afterRunChips(VP.paths(), pkgText()).filter(function (x) { return x.label === chip.label; })[0];
        if (!now) { toast('The project no longer has what this needs.'); return; }
        VP.runDialog({ mode: now.mode, command: now.command });
      });
      box.appendChild(b);
    });
    c.end.appendChild(box);
  }

  // ── the composer: our own commands (P-069) ─────────────────────────────
  function caps() {
    return { projects: !!(VP && (!VP.available || VP.available())), agent: !!S.available,
      cloud: !!(S.available && S.models && S.models.cloud) };
  }

  function say(text) { if (typeof root.addMsg === 'function') root.addMsg('assistant', text); else toast(text); }

  function pickModel(m) {
    S.model = m === 'cloud' ? 'cloud' : 'local';
    var sel = $('amModel');
    if (sel) sel.value = S.model;
    var cloud = S.models && S.models.cloud;
    toast(S.model === 'cloud' ? 'Next message: ' + (cloud ? cloud.label : 'cloud') + '. You are asked before anything is sent.'
      : 'Next message: the local model.');
  }

  // A message that is one of our commands runs here and is not sent. busy: a chat answer is being written, so only
  // /stop runs (the rest waits in the box). Returns true when the message was a command.
  function command(inp, busy) {
    if (!inp) return false;
    var cmd = parseCommand(inp.value, caps());
    if (!cmd || (busy && cmd.name !== 'stop')) return false;
    clearInput(inp);
    closeMention();
    var calls = {
      say: say,
      setMode: setMode,
      keep: function (text) { inp.value = text; },
      model: pickModel,
      stop: function () {
        if (running()) stopRun();
        else if (busy && typeof root.doStop === 'function') root.doStop();
        else toast('Nothing is running.');
      },
      undo: function () {
        if (running()) toast('The agent is working on these files. Stop it first.');
        else if (!VP.on()) toast('There is nothing to undo: no project is open.');
        else VP.undo();
      },
      clear: function () {
        if (running()) { toast('The agent is working. Stop it before starting a new project.'); return; }
        if (typeof root.nS !== 'function' || !root.ed) { toast('A new project needs the editor, which did not load.'); return; }
        root.nS();
      }
    };
    runCommand(cmd, calls, caps(), S.models, S.model);
    return true;
  }

  // ── the composer: @file (P-069) ────────────────────────────────────────
  function mentionPaths() { return VP && VP.on() ? VP.paths() : []; }

  function updateMention() {
    var vi = $('vi');
    if (!vi) return;
    var paths = mentionPaths();
    var at = paths.length && vi.selectionStart === vi.selectionEnd ? mentionAt(vi.value, vi.selectionStart) : null;
    var items = at ? mentionMatches(paths, at.query, 8) : [];
    if (!items.length) { closeMention(); return; }
    if (!M.open || M.query !== at.query) M.sel = 0;
    M.open = true; M.at = at; M.items = items; M.query = at.query;
    renderMention();
  }

  function renderMention() {
    var box = $('vmention'), vi = $('vi');
    if (!box || !vi) return;
    box.textContent = '';
    M.items.forEach(function (p, i) {
      var o = el('div', 'vm-opt' + (i === M.sel ? ' sel' : ''), p);
      o.id = 'vmo' + i;
      o.setAttribute('role', 'option');
      o.setAttribute('aria-selected', i === M.sel ? 'true' : 'false');
      o.addEventListener('mousedown', function (e) { e.preventDefault(); pickMention(i); });   // keeps the focus in the box
      box.appendChild(o);
    });
    box.hidden = false;
    vi.setAttribute('aria-expanded', 'true');
    vi.setAttribute('aria-activedescendant', 'vmo' + M.sel);
  }

  function closeMention() {
    M.open = false; M.at = null; M.items = []; M.sel = 0; M.query = null;
    var box = $('vmention'), vi = $('vi');
    if (box) { box.hidden = true; box.textContent = ''; }
    if (vi) { vi.setAttribute('aria-expanded', 'false'); vi.removeAttribute('aria-activedescendant'); }
  }

  function pickMention(i) {
    var vi = $('vi');
    var path = M.items[i], at = M.at;
    closeMention();
    if (!vi || !path) return;
    var r = insertMention(vi.value, at, vi.selectionStart, path, mentionPaths());
    if (!r) return;
    vi.value = r.text;
    vi.focus();
    vi.setSelectionRange(r.caret, r.caret);
  }

  // ── the composer: keys (P-069) ─────────────────────────────────────────
  function touch() {
    try { return !!(root.matchMedia && root.matchMedia('(hover: none) and (pointer: coarse)').matches); } catch (e) { return false; }
  }

  // The page's keydown handler asks this what a key does. It moves and picks in the @ list itself and answers
  // 'send' or '' for the rest; Esc on a working run is left to the listener in init() (P-044).
  function keyAction(e) {
    var act = composerKey(e, { touch: touch(), mention: M.open, running: running() });
    if (act === 'next' || act === 'prev') {
      e.preventDefault();
      M.sel = (M.sel + (act === 'next' ? 1 : -1) + M.items.length) % M.items.length;
      renderMention();
      return '';
    }
    if (act === 'pick') { e.preventDefault(); pickMention(M.sel); return ''; }
    if (act === 'close') { e.preventDefault(); e.stopImmediatePropagation(); closeMention(); return ''; }   // Esc closes the list only
    if (act === 'send') { closeMention(); return 'send'; }       // Cmd+Enter with the list open: the list goes with the text
    return '';
  }

  // ── the composer: an editor selection as a block (P-069) ───────────────
  function attachEditor(editor) {
    if (!editor || S.editor || typeof editor.onDidChangeCursorSelection !== 'function') return;
    S.editor = editor;
    var btn = $('vselBtn');
    if (btn) btn.addEventListener('click', addSelection);
    editor.onDidChangeCursorSelection(renderSelection);
  }

  function currentSelection() {
    var ed = S.editor;
    if (!ed || !VP || !VP.on()) return null;
    var path = VP.active(), sel = ed.getSelection(), model = ed.getModel();
    if (!path || !sel || sel.isEmpty() || !model || VP.fileText(path) !== model.getValue()) return null;
    var a = sel.startLineNumber, b = sel.endLineNumber;
    if (b > a && sel.endColumn === 1) b -= 1;               // a selection that ends at the start of a line leaves it out
    var lines = [];
    for (var i = a; i <= b; i++) lines.push(model.getLineContent(i));
    return { path: path, start: a, end: b, lines: lines };
  }

  function renderSelection() {
    var bar = $('vsel'), info = $('vselInfo');
    if (!bar) return;
    var s = currentSelection();
    bar.hidden = !s;
    if (info) info.textContent = s ? s.path + ':' + (s.end > s.start ? s.start + '-' + s.end : s.start) : '';
  }

  function addSelection() {
    var s = currentSelection(), vi = $('vi');
    if (!s || !vi) return;
    var before = vi.value.replace(/\s+$/, '');
    vi.value = (before ? before + '\n\n' : '') + selectionBlock(s.path, s.start, s.lines) + '\n';
    vi.focus();
    vi.setSelectionRange(vi.value.length, vi.value.length);
    toast('Added the selection to your message');
  }

  root.VibeAgent = { active: active, running: running, send: send, stop: stopRun, adopt: adopt, leave: leave, setMode: setMode,
    chatCloud: chatCloud, confirmChatCloud: confirmChatCloud, command: command, keyAction: keyAction, attachEditor: attachEditor,
    core: core, _state: S };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})(typeof window !== 'undefined' ? window : globalThis);
