# PR-4G-2 — bound `codec_mcp_http._RATE_WINDOW`: evict idle IPs (H-5) (DESIGN)

**Status:** PROPOSED. Make `_rate_check` periodically evict source IPs idle for >60 s so `_RATE_WINDOW` can't grow unbounded as claude.ai rotates source IPs. The split-out of PR-4G's deferred H-5 (the other three bounded-dict findings shipped in PR-4G; this one needed an import-test strategy for `codec_mcp_http`). Touches `codec_mcp_http.py` only. New `tests/test_rate_window.py`.

**Finding:** H-5 (`_RATE_WINDOW` dict in `codec_mcp_http.py` grows unbounded per unique IP) [HIGH].

---

## 1. The defect
```python
_RATE_WINDOW: dict[str, deque] = defaultdict(deque)   # new deque per unique IP, never removed

def _rate_check(ip: str) -> bool:
    now = time.time(); cutoff = now - 60
    with _RATE_LOCK:
        q = _RATE_WINDOW[ip]
        while q and q[0] < cutoff: q.popleft()
        if len(q) >= _RATE_LIMIT: return False
        q.append(now); return True
```
A new deque is created per source IP and never deleted. Cloudflare forwards `cf-connecting-ip`; claude.ai's fleet rotates IPs, so `_RATE_WINDOW` grows steadily → `codec-mcp-http` hits `max_memory_restart: 512M` over days/weeks → claude.ai connections drop + reconnect (observable hiccup).

## 2. Fix — periodic idle-IP eviction inside `_rate_check`
```python
_RATE_LAST_EVICT = 0.0
_RATE_EVICT_INTERVAL = 60   # at most one O(N) sweep per minute

def _rate_check(ip: str) -> bool:
    global _RATE_LAST_EVICT
    now = time.time(); cutoff = now - 60
    with _RATE_LOCK:
        q = _RATE_WINDOW[ip]
        while q and q[0] < cutoff: q.popleft()
        # H-5: evict IPs idle >60s so the dict can't grow unbounded. Gated to
        # once/_RATE_EVICT_INTERVAL so the O(N) sweep isn't paid per request.
        if now - _RATE_LAST_EVICT > _RATE_EVICT_INTERVAL:
            for stale_ip in [k for k, dq in _RATE_WINDOW.items()
                             if k != ip and (not dq or dq[-1] < cutoff)]:
                del _RATE_WINDOW[stale_ip]
            _RATE_LAST_EVICT = now
        if len(q) >= _RATE_LIMIT: return False
        q.append(now); return True
```
Design points:
- **Evict on `not dq or dq[-1] < cutoff`** — not just *empty* deques (the audit's wording). A truly-silent IP is never re-checked, so its deque never drains to empty; checking the *newest* entry (`dq[-1] < cutoff`) catches "idle >60 s" whether or not the deque was drained. This fully bounds the dict; "empty only" would leak silent IPs forever.
- **Gated by `_RATE_LAST_EVICT`** — the sweep is O(N); running it every request under load is wasteful. Once per minute is plenty (the leak is slow).
- **Skip the current `ip`** (`k != ip`) — it's about to `q.append(now)`; deleting its (possibly-just-drained) deque here would drop the append (the local `q` ref would no longer be the dict's deque).
- Snapshot the keys to delete (list comprehension) — no "dict changed size during iteration".
- All inside the existing `_RATE_LOCK`; rate-limit semantics for the current IP are unchanged.

## 3. Test harness — contained `sys.modules` stub
`codec_mcp_http` imports `mcp.*`, `fastmcp.*` (via `codec_oauth_provider`, which **subclasses** `InMemoryOAuthProvider`), and `codec_mcp` (→ `fastmcp`) — none installed locally or in CI. `build_mcp()` is only called in `main()`, so the module imports fine once those names resolve. The fixture:
- Installs a `_Stub(ModuleType)` whose `__getattr__` returns a **real empty class** (`type(name, (), {})`) for each needed `mcp.*`/`fastmcp.*`/`codec_mcp` name — real classes so `class PersistentOAuthProvider(InMemoryOAuthProvider)` (subclassing) works where MagicMock would fail.
- Uses `monkeypatch.setitem(sys.modules, ...)` (auto-reverted) + pops `codec_mcp_http`/`codec_oauth_provider` from `sys.modules` before AND after, so each test imports fresh (clean `_RATE_WINDOW`) and **no stub leaks** to `test_mcp` / `test_oauth_provider` (whose baseline failures must stay exactly as-is — verified by the zero-new **and** zero-fixed stash-diff).
- The function under test (`_rate_check`) is **real code**; the stubs only unblock the import.

## 4. Test plan (`tests/test_rate_window.py`)
- **Regression:** under the limit returns True; appending `_RATE_LIMIT` timestamps then one more returns False (rate-limit still works).
- **Eviction:** seed `_RATE_WINDOW` with several idle IPs (timestamps > 60 s old) + reset `_RATE_LAST_EVICT = 0`; one `_rate_check("active")` evicts all idle IPs and keeps `"active"`.
- **Active kept:** an IP with a fresh (`now`) entry survives the sweep.
- **Gating:** with `_RATE_LAST_EVICT = now` (recent), a call does **not** sweep (idle IPs remain) — pins the once/interval gate.
- **Isolation:** full suite — exactly the 41 baseline failures, **zero new AND zero fixed** (the stub must not flip `test_mcp`/`test_oauth_provider`). `ruff` per-file delta vs `origin/main` clean.

## 5. Risk + rollback
- **Blast radius:** `codec_mcp_http._rate_check` (+ two module globals). Rate-limit behavior for any live IP is unchanged; only idle bookkeeping is reclaimed. All under the existing `_RATE_LOCK`.
- **Live service:** `codec-mcp-http` faces claude.ai. The change is additive (eviction of entries that are already past their 60 s window — they'd return the same allow/deny either way). No auth/OAuth/transport change.
- **Rollback:** single-commit revert; no persisted state.
