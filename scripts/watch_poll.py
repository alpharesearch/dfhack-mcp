#!/usr/bin/env python3
"""Measure the df-observer plugin's poll cadence from the server's counters.

The plugin polls GET /observe once per tick; the server counts the calls
(/observe-stats, read-only). Acceptance (b): game PAUSED -> ~Config.poll_paused_ms
(60 s); game RUNNING -> Config.poll_running_ms (5-15 s). This script prints the
observed inter-call gaps with the pause state at each sample so the evidence is
a table, not a claim.

Usage: ./scripts/watch_poll.py --minutes 4
Exit codes: 0 finished, 2 server unreachable.
"""
import argparse
import json
import sys
import time
import urllib.request

sys.path.insert(0, __file__.rsplit('/', 1)[0])
import dfw

BASE = "http://127.0.0.1:8000"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=4.0)
    ap.add_argument("--every", type=float, default=10.0)
    a = ap.parse_args()
    t0 = time.time()
    last_count = None
    last_at = None
    paused_seen = set()
    gaps = []
    while time.time() - t0 < a.minutes * 60:
        try:
            d = dfw.get("/observe-stats", timeout=15, offline_grace=60)
        except dfw.GameOffline as e:
            print(f"[{int(time.time()-t0)}s] server offline: {e}", flush=True)
            time.sleep(a.every)
            continue
        count = d.get("count", 0)
        at = d.get("last_at")
        paused = d.get("last_paused")
        paused_seen.add(paused)
        if last_count is not None and count > last_count and at and last_at:
            n = count - last_count
            gap = (at - last_at) / n
            gaps.append((n, gap, paused))
            print(f"[{int(time.time()-t0)}s] +{n} polls, avg gap {gap:.1f}s, paused={paused}",
                  flush=True)
        elif last_count is not None and count == last_count:
            print(f"[{int(time.time()-t0)}s] no new polls (paused={paused})", flush=True)
        last_count, last_at = count, at
        time.sleep(a.every)
    print(f"done: {last_count} total polls; pause states seen: {sorted(str(p) for p in paused_seen)}")
    if gaps:
        running = [g for n, g, p in gaps if p is False]
        paused_g = [g for n, g, p in gaps if p is True]
        if running:
            print(f"running gaps: n={len(running)} min={min(running):.1f}s max={max(running):.1f}s")
        if paused_g:
            print(f"paused  gaps: n={len(paused_g)} min={min(paused_g):.1f}s max={max(paused_g):.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
