"""Standard Philips Hue bridge discovery + self-healing for CODEC.

Why this exists: a hard-coded bridge IP breaks whenever DHCP moves the bridge, and the
usual fixes don't generalise — router DHCP reservations aren't available on every router
(e.g. stock Starlink), and cloud discovery (discovery.meethue.com) returns empty behind
CGNAT. So CODEC discovers the bridge the standard, router-independent way and heals itself
when the IP changes. See docs/HUE-DISCOVERY-DESIGN.md.

Discovery ladder (first verified bridge wins): mDNS (_hue._tcp via macOS `dns-sd`) →
cloud (discovery.meethue.com) → local /24 scan. Every candidate is confirmed by reading
`GET http://<ip>/api/config` and (optionally) matching the stored bridge id.

All network I/O is injectable (`_get` / `methods` / `_discover`) so the logic is unit-tested
without a live network.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time

import requests

CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
HTTP_TIMEOUT = 3  # seconds for the per-candidate /api/config probe


# ── candidate verification ──────────────────────────────────────────────────
def verify_bridge(ip, expected_id=None, *, _get=None):
    """Return the bridge id if a Hue bridge answers at *ip* (and matches *expected_id*
    when given, case-insensitively). Never raises — returns None on any failure."""
    get = _get or requests.get
    try:
        data = get(f"http://{ip}/api/config", timeout=HTTP_TIMEOUT).json()
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    bid = data.get("bridgeid")
    if not bid:
        return None
    if expected_id and str(bid).upper() != str(expected_id).upper():
        return None
    return bid


# ── discovery methods (best-effort; [] on any failure) ──────────────────────
def _mdns_browse(timeout):
    """Browse _hue._tcp via dns-sd; return instance names like 'Hue Bridge - 9A05E2'."""
    try:
        subprocess.run(["dns-sd", "-B", "_hue._tcp", "local."],
                       capture_output=True, text=True, timeout=timeout)
        return []  # dns-sd -B streams forever; we only get output on the timeout path
    except subprocess.TimeoutExpired as e:
        out = e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode("utf-8", "replace")
        names = []
        for line in out.splitlines():
            if "_hue._tcp." in line and " Add " in f" {line} ":
                inst = line.split("_hue._tcp.", 1)[1].strip()
                if inst:
                    names.append(inst)
        return names
    except Exception:
        return []


def _mdns_resolve_host(instance, timeout):
    """Resolve a browsed instance to its target hostname via `dns-sd -L`."""
    try:
        subprocess.run(["dns-sd", "-L", instance, "_hue._tcp", "local."],
                       capture_output=True, text=True, timeout=timeout)
        return None
    except subprocess.TimeoutExpired as e:
        out = e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode("utf-8", "replace")
        for line in out.splitlines():
            if "can be reached at" in line:
                seg = line.split("can be reached at", 1)[1].strip()
                host = seg.split(":")[0].strip().rstrip(".")
                if host:
                    return host
        return None
    except Exception:
        return None


def _discover_mdns(timeout=4):
    """Standard local discovery: browse _hue._tcp, resolve each instance to an IP."""
    ips = []
    for inst in _mdns_browse(timeout):
        host = _mdns_resolve_host(inst, min(timeout, 3))
        if not host:
            continue
        try:
            ips.append(socket.gethostbyname(host))  # macOS resolves *.local via mDNS
        except Exception:
            continue
    return ips


def _discover_cloud(_get=None):
    """Official N-UPnP cloud discovery (empty behind CGNAT/Starlink — that's expected)."""
    get = _get or requests.get
    try:
        data = get("https://discovery.meethue.com", timeout=6).json()
        return [b["internalipaddress"] for b in data if isinstance(b, dict) and b.get("internalipaddress")]
    except Exception:
        return []


def _local_subnet():
    """Return this host's primary /24 prefix (e.g. '192.168.1'), or None."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))  # no packets sent; just picks the egress interface
            ip = s.getsockname()[0]
        finally:
            s.close()
        return ".".join(ip.split(".")[:3])
    except Exception:
        return None


def _discover_scan(_get=None):
    """Reliable LAN fallback: probe every host on the local /24 for a Hue /api/config."""
    import concurrent.futures
    get = _get or requests.get
    subnet = _local_subnet()
    if not subnet:
        return []

    def probe(i):
        ip = f"{subnet}.{i}"
        try:
            data = get(f"http://{ip}/api/config", timeout=1).json()
            if isinstance(data, dict) and data.get("bridgeid"):
                return ip
        except Exception:
            return None
        return None

    found = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as ex:
        for res in ex.map(probe, range(1, 255)):
            if res:
                found.append(res)
    return found


# ── the ladder ──────────────────────────────────────────────────────────────
def discover_bridge(expected_id=None, *, methods=None, _get=None):
    """Run mDNS → cloud → scan; return {"ip","id"} for the first bridge that verifies
    (and matches *expected_id* when given), or None."""
    methods = methods if methods is not None else [_discover_mdns, _discover_cloud, _discover_scan]
    for method in methods:
        try:
            candidates = method() or []
        except Exception:
            continue
        for ip in candidates:
            bid = verify_bridge(ip, expected_id, _get=_get)
            if bid:
                return {"ip": ip, "id": bid}
    return None


# ── self-heal: persist the recovered IP ─────────────────────────────────────
def _atomic_write(path, cfg):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def rediscover_and_update_config(path=CONFIG_PATH, *, _discover=None):
    """Re-find the bridge (matching the stored hue_bridge_id when present) and persist the
    new hue_bridge_ip. Backfills hue_bridge_id if it was missing. Returns the new IP, or
    None if no bridge was found (config left untouched)."""
    discover = _discover or discover_bridge
    try:
        with open(path) as f:
            cfg = json.load(f)
    except Exception:
        return None
    found = discover(cfg.get("hue_bridge_id"))
    if not found:
        return None
    cfg["hue_bridge_ip"] = found["ip"]
    if not cfg.get("hue_bridge_id") and found.get("id"):
        cfg["hue_bridge_id"] = found["id"]
    _atomic_write(path, cfg)
    return found["ip"]
