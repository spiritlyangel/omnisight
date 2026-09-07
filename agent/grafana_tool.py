"""
Omnisight — Grafana telemetry tool.

This is the tool the Gemini Enterprise agent calls at runtime. It queries the
Grafana Cloud HTTP API (the Grafana Labs partner integration), pulls camera
telemetry, and returns a compact derived summary the agent can reason over.

Why it summarises rather than returning raw rows: a shoot day is ~6,200 rows.
The agent does not need rows, it needs rates of change. Trend, drain rate and
minutes-remaining are computed here in arithmetic, then handed to the model to
translate into director language.

Environment variables required:
    GRAFANA_URL     e.g. https://omnisighttelemetry.grafana.net
    GRAFANA_TOKEN   service account token (Viewer role)
    GRAFANA_DS_UID  UID of the grafanacloud-infinity data source
    DATA_URL        raw telemetry JSON URL the data source reads

Local test:
    python3 grafana_tool.py --at 2026-09-01T09:15:00
"""

import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta

GRAFANA_URL = os.environ.get("GRAFANA_URL", "").rstrip("/")
GRAFANA_TOKEN = os.environ.get("GRAFANA_TOKEN", "")
GRAFANA_DS_UID = os.environ.get("GRAFANA_DS_UID", "")
DATA_URL = os.environ.get(
    "DATA_URL",
    "https://raw.githubusercontent.com/spiritlyangel/omnisight/refs/heads/main/shoot_day.json",
)

# Columns must mirror the data source config so Grafana returns typed fields.
COLUMNS = [
    {"selector": "timestamp", "text": "Time", "type": "timestamp"},
    {"selector": "cam_id", "text": "cam_id", "type": "string"},
    {"selector": "operator", "text": "operator", "type": "string"},
    {"selector": "setup", "text": "setup", "type": "string"},
    {"selector": "environment", "text": "environment", "type": "string"},
    {"selector": "signal_dbm", "text": "signal_dbm", "type": "number"},
    {"selector": "battery_pct", "text": "battery_pct", "type": "number"},
    {"selector": "temperature_c", "text": "temperature_c", "type": "number"},
    {"selector": "link_state", "text": "link_state", "type": "string"},
    {"selector": "dropped_frames", "text": "dropped_frames", "type": "number"},
    {"selector": "latency_ms", "text": "latency_ms", "type": "number"},
    {"selector": "bitrate_mbps", "text": "bitrate_mbps", "type": "number"},
    {"selector": "transmitting", "text": "transmitting", "type": "string"},
    {"selector": "recording_local", "text": "recording_local", "type": "string"},
]


class GrafanaError(RuntimeError):
    pass


def _epoch_ms(dt):
    return int(dt.timestamp() * 1000)


def query_grafana(start, end):
    """POST to Grafana's /api/ds/query and return a list of row dicts."""
    if not (GRAFANA_URL and GRAFANA_TOKEN and GRAFANA_DS_UID):
        raise GrafanaError(
            "Missing config. Set GRAFANA_URL, GRAFANA_TOKEN and GRAFANA_DS_UID."
        )

    payload = {
        "from": str(_epoch_ms(start)),
        "to": str(_epoch_ms(end)),
        "queries": [
            {
                "refId": "A",
                "datasource": {
                    "type": "yesoreyeram-infinity-datasource",
                    "uid": GRAFANA_DS_UID,
                },
                "type": "json",
                "source": "url",
                "format": "table",
                "parser": "backend",
                "url": DATA_URL,
                "url_options": {"method": "GET"},
                "root_selector": "",
                "columns": COLUMNS,
                "maxDataPoints": 20000,
            }
        ],
    }

    req = urllib.request.Request(
        f"{GRAFANA_URL}/api/ds/query",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {GRAFANA_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise GrafanaError(
            f"Grafana returned {exc.code}: {exc.read().decode('utf-8')[:400]}"
        ) from exc

    return _frames_to_rows(body)


def _frames_to_rows(body):
    """Flatten Grafana's columnar frame response into row dicts."""
    try:
        frames = body["results"]["A"]["frames"]
    except (KeyError, TypeError):
        raise GrafanaError(f"Unexpected response shape: {str(body)[:400]}")

    rows = []
    for frame in frames:
        names = [f["name"] for f in frame["schema"]["fields"]]
        values = frame["data"]["values"]
        if not values:
            continue
        for i in range(len(values[0])):
            rows.append({name: values[c][i] for c, name in enumerate(names)})
    return rows


def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes")


def _to_dt(value):
    """Grafana returns epoch ms for timestamp fields; tolerate ISO strings too."""
    if isinstance(value, (int, float)):
        return datetime.utcfromtimestamp(value / 1000.0)
    return datetime.fromisoformat(str(value).replace("Z", ""))


def _slope_per_min(series):
    """Least-squares slope in units per minute. series = [(datetime, value)]."""
    pts = [(t, v) for t, v in series if v is not None]
    if len(pts) < 2:
        return None
    t0 = pts[0][0]
    xs = [(t - t0).total_seconds() / 60.0 for t, _ in pts]
    ys = [v for _, v in pts]
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom


def summarize(rows, at=None, trend_window_min=5, drain_window_min=15):
    """Turn raw rows into a per-camera derived summary."""
    parsed = []
    for r in rows:
        t = _to_dt(r.get("Time"))
        if t is None:
            continue
        parsed.append((t, r))
    parsed.sort(key=lambda p: p[0])

    if not parsed:
        return {"error": "no telemetry returned for this window"}

    now = at or parsed[-1][0]
    parsed = [(t, r) for t, r in parsed if t <= now]
    if not parsed:
        return {"error": "no telemetry at or before the requested time"}

    by_cam = {}
    for t, r in parsed:
        by_cam.setdefault(r.get("cam_id"), []).append((t, r))

    cameras = []
    for cam_id in sorted(by_cam):
        hist = by_cam[cam_id]
        t_last, last = hist[-1]

        sig_window = [
            (t, _as_float(r.get("signal_dbm")))
            for t, r in hist
            if t >= now - timedelta(minutes=trend_window_min)
        ]
        sig_trend = _slope_per_min(sig_window)

        bat_window = [
            (t, _as_float(r.get("battery_pct")))
            for t, r in hist
            if t >= now - timedelta(minutes=drain_window_min)
        ]
        # A pack swap shows as battery going up. Only measure since the swap.
        for i in range(len(bat_window) - 1, 0, -1):
            if (
                bat_window[i][1] is not None
                and bat_window[i - 1][1] is not None
                and bat_window[i][1] > bat_window[i - 1][1] + 5
            ):
                bat_window = bat_window[i:]
                break

        drain = _slope_per_min(bat_window)
        drain_per_min = -drain if drain is not None else None

        battery_now = _as_float(last.get("battery_pct"))
        minutes_left = None
        if battery_now is not None and drain_per_min and drain_per_min > 0.01:
            minutes_left = round(battery_now / drain_per_min)

        # Consecutive seconds with no transmission, looking backwards.
        offline_since = None
        for t, r in reversed(hist):
            if _as_bool(r.get("transmitting")):
                break
            offline_since = t
        offline_secs = (
            int((now - offline_since).total_seconds()) if offline_since else 0
        )

        cameras.append(
            {
                "cam_id": cam_id,
                "operator": last.get("operator"),
                "signal_dbm": round(_as_float(last.get("signal_dbm")) or 0, 1),
                "signal_trend_dbm_per_min": (
                    round(sig_trend, 2) if sig_trend is not None else None
                ),
                "battery_pct": round(battery_now, 1) if battery_now else None,
                "battery_drain_pct_per_min": (
                    round(drain_per_min, 3) if drain_per_min else None
                ),
                "battery_minutes_remaining": minutes_left,
                "temperature_c": _as_float(last.get("temperature_c")),
                "link_state": last.get("link_state"),
                "transmitting": _as_bool(last.get("transmitting")),
                "recording_local": _as_bool(last.get("recording_local")),
                "offline_seconds": offline_secs,
                "dropped_frames": _as_float(last.get("dropped_frames")),
                "latency_ms": _as_float(last.get("latency_ms")),
                "bitrate_mbps": _as_float(last.get("bitrate_mbps")),
            }
        )

    last_row = parsed[-1][1]
    offline = [c for c in cameras if not c["transmitting"]]

    return {
        "as_of": now.isoformat(),
        "setup": last_row.get("setup"),
        "environment": last_row.get("environment"),
        "camera_count": len(cameras),
        "offline_count": len(offline),
        "whole_fleet_offline": len(offline) == len(cameras) and cameras != [],
        "cameras": cameras,
    }


def get_fleet_status(at_iso=None, lookback_minutes=30):
    """Entry point the agent calls. Returns the derived summary as a dict."""
    at = datetime.fromisoformat(at_iso) if at_iso else datetime.utcnow()
    rows = query_grafana(at - timedelta(minutes=lookback_minutes), at)
    return summarize(rows, at=at)


def handler(request):
    """Google Cloud Function entry point."""
    args = request.get_json(silent=True) or {}
    try:
        result = get_fleet_status(
            at_iso=args.get("at"),
            lookback_minutes=int(args.get("lookback_minutes", 30)),
        )
    except GrafanaError as exc:
        return ({"error": str(exc)}, 502)
    return (result, 200)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", help="ISO timestamp to evaluate, e.g. 2026-09-01T09:15:00")
    ap.add_argument("--lookback", type=int, default=30)
    a = ap.parse_args()
    print(json.dumps(get_fleet_status(a.at, a.lookback), indent=2))
