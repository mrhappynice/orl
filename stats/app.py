import os
import time
import json
import math
from datetime import datetime
from collections import Counter
from flask import Flask, Response

app = Flask(__name__)

HLS_DIR = os.getenv("HLS_DIR", "/data/hls")
ACCESS_LOG = os.getenv("ACCESS_LOG", "/data/logs/hls_access.log")
SEGMENT_SECONDS = float(os.getenv("SEGMENT_SECONDS", "4"))
WINDOW_SECONDS = int(os.getenv("WINDOW_SECONDS", "60"))
LONG_WINDOW_SECONDS = int(os.getenv("LONG_WINDOW_SECONDS", "300"))
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "21600"))
MAX_TAIL_BYTES = int(os.getenv("MAX_TAIL_BYTES", "256000"))

PLAYLIST = os.path.join(HLS_DIR, "live.m3u8")

LAST_SEEN = {}
LAST_SHORT_COUNT = None
LAST_CLEANUP = 0.0


def file_mtime(path: str) -> float | None:
    try:
        return os.path.getmtime(path)
    except FileNotFoundError:
        return None


def tail_lines(path: str, max_bytes: int = 256_000) -> list[str]:
    # Read up to the last chunk of the log to keep this light
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes), os.SEEK_SET)
            data = f.read().decode("utf-8", errors="ignore")
        return data.splitlines()
    except FileNotFoundError:
        return []


def parse_timestamp(line: str) -> float | None:
    try:
        start = line.index("[") + 1
        end = line.index("]", start)
        ts_str = line[start:end]
        dt = datetime.strptime(ts_str, "%d/%b/%Y:%H:%M:%S %z")
        return dt.timestamp()
    except Exception:
        return None


def parse_request(line: str) -> dict | None:
    if '"' not in line:
        return None
    parts = line.split('"')
    if len(parts) < 3:
        return None

    request_part = parts[1]
    req_bits = request_part.split()
    if len(req_bits) < 2:
        return None
    method, path = req_bits[0], req_bits[1]
    if method.upper() != "GET":
        return None

    status = None
    after = parts[2].strip().split()
    if after and after[0].isdigit():
        status = int(after[0])

    ua = ""
    if len(parts) >= 6:
        ua = parts[5]

    ip = line.split(" ", 1)[0]
    ts = parse_timestamp(line)

    kind = "other"
    if path.startswith("/hls/") and path.endswith(".ts"):
        kind = "segment"
    elif path.startswith("/hls/") and path.endswith(".m3u8"):
        kind = "playlist"

    return {
        "ts": ts,
        "ip": ip,
        "ua": ua,
        "path": path,
        "status": status,
        "kind": kind,
        "key": f"{ip}|{ua}",
    }


def classify_ua(ua: str) -> str:
    if not ua:
        return "Unknown"
    u = ua.lower()
    if "iphone" in u or "ipad" in u or "ios" in u or "applecoremedia" in u:
        return "iOS"
    if "android" in u or "okhttp" in u or "exoplayer" in u:
        return "Android"
    if "windows" in u:
        return "Windows"
    if "mac os" in u or "macintosh" in u:
        return "macOS"
    if "linux" in u:
        return "Linux"
    if "curl" in u or "wget" in u:
        return "CLI"
    if "bot" in u or "spider" in u:
        return "Bot"
    return "Other"


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    if len(values) == 1:
        return values[0]
    k = (len(values) - 1) * (pct / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return values[int(k)]
    return values[f] + (values[c] - values[f]) * (k - f)


def compute_window_stats(events: list[dict], window_seconds: int, now: float) -> dict:
    window_start = now - window_seconds
    events_w = [e for e in events if e["ts"] is not None and e["ts"] >= window_start]

    if events_w:
        min_ts = min(e["ts"] for e in events_w)
        coverage_seconds = max(0.0, now - min_ts)
    else:
        coverage_seconds = 0.0

    effective_window = window_seconds
    if coverage_seconds > 0:
        effective_window = min(window_seconds, coverage_seconds)
    if effective_window < 1.0:
        effective_window = 1.0

    segment_events = [e for e in events_w if e["kind"] == "segment"]
    playlist_events = [e for e in events_w if e["kind"] == "playlist"]

    segment_requests = len(segment_events)
    playlist_requests = len(playlist_events)

    segment_per_sec = segment_requests / effective_window if effective_window > 0 else 0.0
    playlist_per_sec = playlist_requests / effective_window if effective_window > 0 else 0.0

    errors_4xx = sum(1 for e in events_w if e["status"] and 400 <= e["status"] < 500)
    errors_5xx = sum(1 for e in events_w if e["status"] and 500 <= e["status"] < 600)

    seg_ok = sum(1 for e in segment_events if e["status"] is None or e["status"] < 400)
    seg_fail = sum(1 for e in segment_events if e["status"] is not None and e["status"] >= 400)
    seg_denom = seg_ok + seg_fail
    segment_success_rate = (seg_ok / seg_denom) if seg_denom > 0 else None

    pl_ok = sum(1 for e in playlist_events if e["status"] is None or e["status"] < 400)
    pl_fail = sum(1 for e in playlist_events if e["status"] is not None and e["status"] >= 400)
    pl_denom = pl_ok + pl_fail
    playlist_success_rate = (pl_ok / pl_denom) if pl_denom > 0 else None

    active_keys = set()
    sessions = {}
    for e in segment_events:
        if e["status"] is not None and e["status"] >= 400:
            continue
        key = e["key"]
        active_keys.add(key)
        if key not in sessions:
            sessions[key] = {"first": e["ts"], "last": e["ts"]}
        else:
            sessions[key]["first"] = min(sessions[key]["first"], e["ts"])
            sessions[key]["last"] = max(sessions[key]["last"], e["ts"])

    session_durations = []
    for v in sessions.values():
        if v["first"] is not None and v["last"] is not None:
            session_durations.append(max(0.0, v["last"] - v["first"]))

    ua_counts = Counter(e["ua"] or "Unknown" for e in events_w)
    cat_counts = Counter(classify_ua(e["ua"]) for e in events_w)

    last_seen_by_key = {}
    for e in events_w:
        key = e["key"]
        ts = e["ts"]
        if ts is None:
            continue
        prev = last_seen_by_key.get(key)
        if prev is None or ts > prev:
            last_seen_by_key[key] = ts

    coverage_ratio = None
    if window_seconds > 0:
        coverage_ratio = min(1.0, coverage_seconds / window_seconds) if coverage_seconds > 0 else 0.0

    return {
        "window_start": window_start,
        "coverage_seconds": coverage_seconds,
        "coverage_ratio": coverage_ratio,
        "segment_requests": segment_requests,
        "playlist_requests": playlist_requests,
        "segment_per_sec": segment_per_sec,
        "playlist_per_sec": playlist_per_sec,
        "errors_4xx": errors_4xx,
        "errors_5xx": errors_5xx,
        "segment_success_rate": segment_success_rate,
        "playlist_success_rate": playlist_success_rate,
        "active_keys": active_keys,
        "session_durations": session_durations,
        "ua_counts": ua_counts,
        "cat_counts": cat_counts,
        "last_seen_by_key": last_seen_by_key,
    }


def top_items(counter: Counter, max_items: int = 5, max_label: int = 42) -> list[dict]:
    out = []
    for label, count in counter.most_common(max_items):
        lab = label.strip() if isinstance(label, str) else str(label)
        if not lab:
            lab = "Unknown"
        if len(lab) > max_label:
            lab = f"{lab[:max_label - 1]}~"
        out.append({"label": lab, "count": count})
    return out


def estimate_live_latency(playlist_age: float | None, segment_seconds: float) -> float | None:
    if playlist_age is None:
        return None
    return max(0.0, playlist_age + segment_seconds)


@app.get("/api/stats")
def stats():
    global LAST_SHORT_COUNT, LAST_CLEANUP

    now = time.time()
    playlist_mtime = file_mtime(PLAYLIST)
    playlist_age = None if playlist_mtime is None else max(0.0, now - playlist_mtime)

    ingest_up = (playlist_age is not None and playlist_age < (SEGMENT_SECONDS * 3.5))

    lines = tail_lines(ACCESS_LOG, MAX_TAIL_BYTES)
    events = []
    for ln in lines:
        info = parse_request(ln)
        if not info:
            continue
        if info["kind"] == "other":
            continue
        events.append(info)

    long_start = now - LONG_WINDOW_SECONDS
    events = [e for e in events if e["ts"] is not None and e["ts"] >= long_start]

    short_stats = compute_window_stats(events, WINDOW_SECONDS, now)
    long_stats = compute_window_stats(events, LONG_WINDOW_SECONDS, now)

    new_count = 0
    returning_count = 0
    for key in short_stats["active_keys"]:
        last = LAST_SEEN.get(key)
        if last is None or last < short_stats["window_start"]:
            new_count += 1
        else:
            returning_count += 1

    for key, ts in long_stats["last_seen_by_key"].items():
        prev = LAST_SEEN.get(key)
        if prev is None or ts > prev:
            LAST_SEEN[key] = ts

    if now - LAST_CLEANUP > 60.0:
        cutoff = now - SESSION_TTL_SECONDS
        for key in list(LAST_SEEN.keys()):
            if LAST_SEEN[key] < cutoff:
                del LAST_SEEN[key]
        LAST_CLEANUP = now

    short_listener_count = len(short_stats["active_keys"])
    delta_short = None
    delta_short_pct = None
    if LAST_SHORT_COUNT is not None:
        delta_short = short_listener_count - LAST_SHORT_COUNT
        if LAST_SHORT_COUNT > 0:
            delta_short_pct = delta_short / LAST_SHORT_COUNT
    LAST_SHORT_COUNT = short_listener_count

    sess = short_stats["session_durations"]
    sess_avg = (sum(sess) / len(sess)) if sess else None
    sess_p25 = percentile(sess, 25)
    sess_p50 = percentile(sess, 50)
    sess_p75 = percentile(sess, 75)

    total_req = short_stats["segment_requests"] + short_stats["playlist_requests"]
    rate_4xx = (short_stats["errors_4xx"] / total_req) if total_req > 0 else 0.0
    rate_5xx = (short_stats["errors_5xx"] / total_req) if total_req > 0 else 0.0

    payload = {
        "stream": {
            "hls_url": "https://openradio.live/hls/live.m3u8",
            "ingest_up": ingest_up,
            "playlist_age_seconds": playlist_age,
            "segment_seconds": SEGMENT_SECONDS,
            "window_seconds": WINDOW_SECONDS,
            "long_window_seconds": LONG_WINDOW_SECONDS,
        },
        "windows": {
            "short": {
                "window_seconds": WINDOW_SECONDS,
                "coverage_seconds": short_stats["coverage_seconds"],
                "coverage_ratio": short_stats["coverage_ratio"],
            },
            "long": {
                "window_seconds": LONG_WINDOW_SECONDS,
                "coverage_seconds": long_stats["coverage_seconds"],
                "coverage_ratio": long_stats["coverage_ratio"],
            },
        },
        "listeners": {
            "active_short": short_listener_count,
            "active_long": len(long_stats["active_keys"]),
            "delta_short": delta_short,
            "delta_short_pct": delta_short_pct,
            "new_short": new_count,
            "returning_short": returning_count,
            "session": {
                "avg_seconds": sess_avg,
                "p25_seconds": sess_p25,
                "p50_seconds": sess_p50,
                "p75_seconds": sess_p75,
                "sample_count": len(sess),
            },
        },
        "traffic": {
            "segment": {
                "requests": short_stats["segment_requests"],
                "per_sec": short_stats["segment_per_sec"],
                "success_rate": short_stats["segment_success_rate"],
            },
            "playlist": {
                "requests": short_stats["playlist_requests"],
                "per_sec": short_stats["playlist_per_sec"],
                "success_rate": short_stats["playlist_success_rate"],
            },
            "errors": {
                "total_4xx": short_stats["errors_4xx"],
                "total_5xx": short_stats["errors_5xx"],
                "rate_4xx": rate_4xx,
                "rate_5xx": rate_5xx,
            },
        },
        "clients": {
            "top_agents": top_items(short_stats["ua_counts"], 5, 46),
            "top_categories": top_items(short_stats["cat_counts"], 5, 20),
        },
        "qos": {
            "live_latency_seconds_est": estimate_live_latency(playlist_age, SEGMENT_SECONDS),
        },
        "server_time_unix": now,
    }

    return Response(json.dumps(payload, indent=2), mimetype="application/json")


@app.get("/api/health")
def health():
    return {"ok": True}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
