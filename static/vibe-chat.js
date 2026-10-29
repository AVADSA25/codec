/* Vibe chat on the page (docs/VIBE-CHAT-ROUTE-DESIGN.md, D-22).

   Which route a chat message takes, what the request looks like on each, and what the page saves.
   "own" is POST /api/vibe/chat: the server writes the prompt, uses the local model unless the owner confirmed the cloud
   model for this message, and runs no skill, memory or web search. "chat" is the old /api/chat, kept for rollback:
   config.json vibe.chat_route = "chat" (read through GET /api/vibe/chat/status), or a 404 from the new route.

   Plain functions on values, so Node can test them (tests/test_vibe_chat_page.py). Loaded by codec_vibe.html after
   static/vibe-agent.js, before the page's own script. Nothing here builds HTML. */
(function (root) {
  'use strict';

  var S = { route: null, pending: null };

  // The turns as vibe.db keeps them: what the owner typed, not the [PROJECT FILES] / [CURRENT CODE] block that went
  // to the model with it. Assistant turns are kept as they are.
  function stored(hist) {
    return (hist || []).map(function (m) {
      var typed = m.role === 'user' && typeof m.plain === 'string' && m.plain !== '';
      return { role: m.role, content: typed ? m.plain : (m.content == null ? '' : m.content) };
    });
  }

  function turns(messages) {
    return (messages || []).filter(function (m) { return m.role === 'user' || m.role === 'assistant'; })
      .map(function (m) { return { role: m.role, content: m.content }; });
  }

  // o: { messages: [{role, content}], system: text, project: bool, workdir: text, cloud: bool }
  // cloud is true only after the owner said yes to the dialog for this message.
  function request(route, o) {
    var workdir = o.workdir || '';
    if (route === 'chat') {
      // The old path. tools:false and temporary:true stop the skill hijack, memory recall and observer text (P-042
      // step 1); web:false stops the link fetch and web search (D-37). It follows whatever model chat is on.
      return { url: '/api/chat', body: { messages: [{ role: 'system', content: o.system }].concat(turns(o.messages)),
        thinking: false, stream: true, workdir: workdir, web: false, tools: false, temporary: true } };
    }
    var body = { messages: turns(o.messages), stream: true, project: !!o.project, workdir: workdir,
      model: o.cloud === true ? 'cloud' : 'local' };
    if (o.cloud === true) body.confirm_cloud = true;
    return { url: '/api/vibe/chat', body: body };
  }

  // What GET /api/vibe/chat/status said. A server without the route (404) is an older server: use the old path.
  function routeFromStatus(httpStatus, json) {
    if (httpStatus === 404) return 'chat';
    if (httpStatus >= 200 && httpStatus < 300 && json && json.route === 'chat') return 'chat';
    return 'own';
  }

  function route() {
    if (S.route) return Promise.resolve(S.route);
    if (!S.pending) {
      S.pending = fetch('/api/vibe/chat/status', { credentials: 'same-origin' }).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) { return routeFromStatus(r.status, j); });
      }).catch(function () { return 'own'; }).then(function (rt) { S.route = rt; return rt; });
    }
    return S.pending;
  }

  function post(req, signal) {
    return fetch(req.url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
      body: JSON.stringify(req.body), signal: signal });
  }

  // Sends one chat message; resolves to the fetch Response of whichever route took it. A 404 from the new route
  // (the switch was flipped, or an older server) sends this message to the old route and remembers it for the page.
  function send(o, signal) {
    return route().then(function (rt) {
      return post(request(rt, o), signal).then(function (r) {
        if (rt === 'own' && r.status === 404) { S.route = 'chat'; return post(request('chat', o), signal); }
        return r;
      });
    });
  }

  var api = { stored: stored, request: request, routeFromStatus: routeFromStatus, route: route, send: send, _state: S };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.VibeChat = api;
  if (typeof document !== 'undefined') route();     // learn the switch while the page loads
})(typeof window !== 'undefined' ? window : globalThis);
