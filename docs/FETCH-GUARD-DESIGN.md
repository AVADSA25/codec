# One fetch guard for every URL fetch (P-008)

Owner decision D-18 (decision board, 29 Oct 2026): "block 100.64.0.0/10 and every
non-public address, refuse the 30 known bypass URLs, cap a fetch at 2 MB and 15 s,
one shared guard."

## What

- `codec_ssrf.validate_url` is the only URL check. `routes/chat.py:_url_host_is_public`
  now calls it instead of keeping its own copy of the rules.
- The guard refuses:
  - a URL containing a backslash, a space or a control character;
  - a URL whose host, as read by `urlparse`, differs from the host urllib3
    (`requests`) or `httpx` would dial (the backslash-before-`@` bypass: 30 URLs
    where the old guard read `example.com` and `requests` dialled `127.0.0.1`);
  - every address that is not global, including `100.64.0.0/10` (Tailscale and
    carrier NAT), and IPv6 addresses that carry an IPv4 address
    (`::ffff:0:0/96`, `64:ff9b::/96`, `2002::/16`, `::/96`), judged as the IPv4
    address they carry. Python 3.13 calls `64:ff9b::7f00:1` and `::7f00:1` global,
    so this unwrap is needed.
- `codec_ssrf.FetchBudget` + `read_capped`: one fetch, redirects included, stops near
  2 MB (`MAX_FETCH_BYTES`) and 15 s (`MAX_FETCH_SECONDS`), with at most 5 s per
  network step. All three fetchers stream the body:
  `skills/web_fetch.py` (requests, `stream=True`), `codec_agents._web_fetch` and
  `routes/chat.py:_fetch_url_content` (httpx `stream`).

## Not changed

- Redirects are still followed by hand with every hop re-checked.
- No new dependency, no switch from requests to httpx, `web_fetch` is not added to
  `_HTTP_CONSENT_REQUIRED` (separate approvals).
- The DNS-rebinding gap between the check and the connect stays as documented in
  `codec_ssrf.py`.

## Cost

A fetch the owner makes on purpose to a LAN or Tailscale address (the MacBook Air,
the phone) is now refused. Pages larger than 2 MB are cut; every reader already
trims to 8,000-12,000 characters.

## Tests

`tests/test_fetch_guard.py`: the 30 bypass URLs, the non-public and embedded
addresses, ordinary URLs still passing, and the 2 MB / 15 s caps in all three
fetchers with an endless stub body and a fake clock. `tests/test_ssrf.py` fakes
updated for the streamed reads.

## Rollback

`git revert` and regenerate `skills/.manifest.json`. Nothing stored changes. Takes
effect after restarts of `codec-dashboard`, `codec-mcp-http` and `codec-agent-runner`
(owner's action).
