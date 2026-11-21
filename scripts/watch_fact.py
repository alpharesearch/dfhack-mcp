#!/usr/bin/env python3
"""Watch a FortFacts counter until it reaches a threshold, then exit.

Same reason as watch_dig.py: a long wait must not live in the model's turns. Run this as a
background job; the harness delivers the completion notice when it exits.

Counters come from the ladder header line: food, drink, wood, stone, metal, miners,
pending dig, pop, soldiers, hostile.

Usage:
  ./scripts/watch_fact.py --fact wood --min 3 --every 20 --timeout 2400
Exit: 0 reached, 2 timed out, 1 read failure.
"""
import argparse
import json
import re
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000"

PATTERNS = {
    "pop": r"pop (\d+)",
    "food": r"food (\d+)",
    "drink": r"drink (\d+)",
    "wood": r"wood (\d+)",
    "stone": r"stone (\d+)",
    "metal": r"metal (\d+)",
    "miners": r"miners (\d+)",
    "pending_dig": r"pending dig (\d+)",
    "soldiers": r"soldiers (\d+)",
    "hostile": r"hostile (\d+)",
}


def get(path, params=None, timeout=90):
    url = BASE + path
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fact", required=True, choices=sorted(PATTERNS))
    ap.add_argument("--min", type=int, required=True, help="value at which to declare done")
    ap.add_argument("--every", type=int, default=20)
    ap.add_argument("--timeout", type=int, default=2400)
    args = ap.parse_args()
    t0 = time.time()
    last = None
    while True:
        try:
            txt = get("/fort-progress", {"max_tokens": 300})["text"]
        except Exception as e:
            print(f"read failed: {e}", file=sys.stderr)
            return 1
        m = re.search(PATTERNS[args.fact], txt)
        val = int(m.group(1)) if m else None
        stamp = int(time.time() - t0)
        if val is not None and val != last:
            print(f"[{stamp}s] {args.fact} = {val}", flush=True)
            last = val
        if val is not None and val >= args.min:
            print(f"FACT REACHED after {stamp}s: {args.fact} = {val} (>= {args.min})", flush=True)
            return 0
        if time.time() - t0 > args.timeout:
            print(f"TIMEOUT after {args.timeout}s; {args.fact} = {val}", flush=True)
            return 2
        time.sleep(args.every)


if __name__ == "__main__":
    sys.exit(main())
