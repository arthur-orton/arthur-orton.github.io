#!/usr/bin/env python3
"""Pulls current vote snapshots from the upstream source and appends them
to the per-activity history files under data/.

Two modes:
  python collect.py            normal run: fetch current standings, append
                                one point per activity (no-op if the last
                                stored point is less than MIN_GAP_SECONDS old)
  python collect.py --backfill fetch as much minute-level history as the
                                source still has and seed data/ with it,
                                downsampled to MIN_GAP_SECONDS spacing
"""
import json
import os
import sys
import time
import urllib.request
from urllib.error import URLError, HTTPError

SOURCE_VOTES = "https://fubon-vote-live.brienjohn.chatgpt.site/api/votes/{id}"
SOURCE_HISTORY = "https://fubon-vote-live.brienjohn.chatgpt.site/api/history?activityId={id}&limit=8000"

ACTIVITIES = [28, 29, 30, 31, 32, 33, 34]

MIN_GAP_SECONDS = 4 * 60 + 30  # ~5 minutes between stored points
MAX_POINTS = 3000  # generous safety cap; this event needs well under 1000

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def fetch_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "vote-monitor-collector/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_points(activity_id):
    path = os.path.join(DATA_DIR, f"history-{activity_id}.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("points", [])
    except (json.JSONDecodeError, OSError):
        return []


def save_points(activity_id, points):
    path = os.path.join(DATA_DIR, f"history-{activity_id}.json")
    points = points[-MAX_POINTS:]
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"points": points}, f, separators=(",", ":"), ensure_ascii=False)


def items_from_votes_payload(payload):
    items = payload["activity"]["items"]
    items_sorted = sorted(items, key=lambda it: -it["votes"])
    return [[it["name"], it["votes"]] for it in items_sorted]


def run_normal():
    now = int(time.time())
    changed = []
    for aid in ACTIVITIES:
        points = load_points(aid)
        if points and now - points[-1][0] < MIN_GAP_SECONDS:
            continue
        try:
            payload = fetch_json(SOURCE_VOTES.format(id=aid))
        except (URLError, HTTPError, TimeoutError, ValueError) as e:
            print(f"[warn] activity {aid}: fetch failed: {e}", file=sys.stderr)
            continue
        try:
            items = items_from_votes_payload(payload)
        except (KeyError, TypeError) as e:
            print(f"[warn] activity {aid}: unexpected payload shape: {e}", file=sys.stderr)
            continue
        points.append([now, items])
        save_points(aid, points)
        changed.append(aid)
    print(f"updated activities: {changed}")


def run_backfill():
    for aid in ACTIVITIES:
        try:
            payload = fetch_json(SOURCE_HISTORY.format(id=aid), timeout=40)
        except (URLError, HTTPError, TimeoutError, ValueError) as e:
            print(f"[warn] activity {aid}: history fetch failed: {e}", file=sys.stderr)
            continue
        rows = payload.get("rows", [])
        if not rows:
            print(f"[warn] activity {aid}: no history rows returned")
            continue
        by_minute = {}
        for row in rows:
            ts = row["captured_at"]
            by_minute.setdefault(ts, []).append((row["candidate_name"], row["votes"]))
        minute_keys = sorted(by_minute.keys())

        def epoch(ts):
            import datetime
            return int(datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ")
                       .replace(tzinfo=datetime.timezone.utc).timestamp())

        points = []
        last_kept = None
        for ts in minute_keys:
            e = epoch(ts)
            if last_kept is not None and e - last_kept < MIN_GAP_SECONDS:
                continue
            items = sorted(by_minute[ts], key=lambda p: -p[1])
            points.append([e, [[n, v] for n, v in items]])
            last_kept = e
        save_points(aid, points)
        print(f"activity {aid}: seeded {len(points)} points spanning "
              f"{(points[-1][0]-points[0][0])/3600:.1f}h" if points else f"activity {aid}: no points")


if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    if "--backfill" in sys.argv:
        run_backfill()
    else:
        run_normal()
