#!/usr/bin/env python3
"""
Mock telemetry generator for a teleserye shoot day.

Simulates a fleet of wireless cameras transmitting to a base station across
multiple location setups in a single shooting day. Emits one row per camera
per sampling interval.

The fleet roster and the day's setups are NOT hardcoded here. They are read
from the production's call sheet - the document every unit already distributes
the night before. See call_sheet.py.

Stdlib only - no dependencies. Run:
    python3 generate_telemetry.py                      # reads call_sheet.txt
    python3 generate_telemetry.py --call-sheet other.txt
    python3 generate_telemetry.py --seed 7             # reproducible variation
    python3 generate_telemetry.py --interval 15        # sample every 15 seconds

Designed so the data contains realistic, *engineered* failure moments the
agent can be shown catching. See SCENARIO_NOTES at the bottom of the file.
"""

import argparse
import csv
import json
import math
import os
import random
from datetime import datetime, timedelta

from call_sheet import parse_call_sheet, summarize

# Setup and Camera now come from the call sheet. These module-level names are
# populated in main() so the model functions below can stay simple.
SETUPS = []
CAMERAS = []

# --------------------------------------------------------------------------
# Engineered incidents - these are what make the demo compelling.
# The agent should catch each of these BEFORE the hard failure lands.
# --------------------------------------------------------------------------

INCIDENTS = [
    # (cam_id, start_min, end_min, kind, severity)
    ("CAM-03", 120, 165, "signal_decay",   0.75),  # slow degradation -> dropout
    ("CAM-03", 165, 180, "dropout",        1.00),  # actual loss of feed
    ("CAM-05", 300, 340, "interference",   0.60),  # market congestion spike
    ("CAM-02", 380, 420, "thermal",        0.55),  # overheating in the sun
    ("CAM-03", 520, 620, "battery_fade",   0.85),  # degraded pack dies early
    ("CAM-04", 545, 560, "dropout",        1.00),  # brief rooftop dropout
]


def active_incident(cam_id, minute):
    """Return (kind, severity, progress) for any incident active at this moment."""
    for cid, start, end, kind, sev in INCIDENTS:
        if cid == cam_id and start <= minute < end:
            progress = (minute - start) / max(1, (end - start))
            return kind, sev, progress
    return None, 0.0, 0.0


def current_setup(minute):
    for s in SETUPS:
        if s.start_min <= minute < s.end_min:
            return s
    return SETUPS[-1]


# --------------------------------------------------------------------------
# Signal model
# --------------------------------------------------------------------------

def signal_dbm(cam, setup, minute, rng):
    """
    Approximate received signal strength in dBm.
    -40 excellent, -65 good, -75 marginal, -85 unusable.
    """
    if setup.environment == "transit":
        return None  # cameras packed, not transmitting

    # Free-space-ish path loss against nominal distance
    dist = cam.base_distance_m
    if setup.environment == "exterior":
        dist *= 1.35          # longer sightlines on exteriors
    if setup.environment == "interior":
        dist *= 0.8           # closer, but walls attenuate

    base = -30 - (16 * math.log10(max(1.0, dist)))

    # Wall / obstruction penalty
    if setup.environment == "interior":
        base -= 9 if cam.base_distance_m > 60 else 3

    # Ambient RF interference at this location
    base -= setup.interference * 9

    # Operator movement wander
    base += rng.gauss(0, 2.2)

    # Incident effects
    kind, sev, prog = active_incident(cam.cam_id, minute)
    if kind == "signal_decay":
        base -= 20 * sev * prog          # gets worse over time
    elif kind == "interference":
        base -= 14 * sev * (0.5 + 0.5 * math.sin(prog * math.pi * 4))
    elif kind == "dropout":
        return None

    return round(base, 1)


def battery_pct(cam, minute, rng):
    """Battery drains over the day; degraded packs drain faster and non-linearly."""
    # Battery swaps at transit and at start of the night setup
    if minute < 200:
        swap_start, elapsed = 100.0, minute
    elif minute < 460:
        swap_start, elapsed = 100.0, minute - 260
    else:
        swap_start, elapsed = 100.0, minute - 460

    # Healthy pack ~ 0.22%/min (~7.5h/pack under light load); degraded worse
    rate = 0.22 / max(0.35, cam.battery_health)
    pct = swap_start - (rate * elapsed)

    kind, sev, prog = active_incident(cam.cam_id, minute)
    if kind == "battery_fade":
        pct -= 22 * sev * (prog ** 1.6)   # accelerating collapse

    pct += rng.gauss(0, 0.4)
    return round(max(0.0, min(100.0, pct)), 1)


def temperature_c(cam, setup, minute, rng):
    base = 31.0 if setup.environment == "exterior" else 26.0
    base += rng.gauss(0, 0.8)
    kind, sev, prog = active_incident(cam.cam_id, minute)
    if kind == "thermal":
        base += 16 * sev * prog
    return round(base, 1)


def derive(sig, temp, batt, rng):
    """Downstream metrics that follow from signal quality."""
    if sig is None:
        return dict(link_state="DOWN", bitrate_mbps=0.0, dropped_frames=0,
                    latency_ms=None, transmitting=False, recording_local=True)

    # Map dBm -> usable bitrate
    if sig > -60:
        quality = 1.0
    elif sig > -75:
        quality = (sig + 75) / 15.0
    else:
        quality = max(0.0, (sig + 88) / 13.0 * 0.35)

    bitrate = round(max(0.0, 48.0 * quality + rng.gauss(0, 1.5)), 1)
    dropped = int(max(0, (1.0 - quality) * rng.uniform(0, 140)))
    latency = int(35 + (1.0 - quality) * rng.uniform(60, 900))
    state = "OK" if quality > 0.65 else ("DEGRADED" if quality > 0.2 else "CRITICAL")

    return dict(link_state=state, bitrate_mbps=bitrate, dropped_frames=dropped,
                latency_ms=latency, transmitting=True, recording_local=True)


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------

def generate(interval_s=30, seed=42, call_time="07:00", shoot_date=None):
    rng = random.Random(seed)
    base_date = shoot_date or datetime.now()
    day = base_date.replace(hour=int(call_time[:2]), minute=int(call_time[3:]),
                            second=0, microsecond=0)
    total_min = SETUPS[-1].end_min
    rows = []

    steps = int((total_min * 60) / interval_s)
    for step in range(steps):
        elapsed_s = step * interval_s
        minute = elapsed_s / 60.0
        ts = day + timedelta(seconds=elapsed_s)
        setup = current_setup(minute)

        for cam in CAMERAS:
            sig = signal_dbm(cam, setup, minute, rng)
            batt = battery_pct(cam, minute, rng)
            temp = temperature_c(cam, setup, minute, rng)
            d = derive(sig, temp, batt, rng)

            rows.append({
                "timestamp": ts.isoformat(timespec="seconds"),
                "minutes_elapsed": round(minute, 2),
                "cam_id": cam.cam_id,
                "model": cam.model,
                "operator": cam.operator,
                "setup": setup.name,
                "environment": setup.environment,
                "distance_m": cam.base_distance_m,
                "signal_dbm": sig,
                "battery_pct": batt,
                "battery_health": cam.battery_health,
                "temperature_c": temp,
                **d,
            })
    return rows


SCENARIO_NOTES = """
ENGINEERED MOMENTS FOR THE DEMO
-------------------------------
~min 120-165  CAM-03 signal decays steadily in the far bedroom at Location A.
              The agent should flag this ~20+ minutes before the feed is lost.
~min 165-180  CAM-03 drops out entirely. This is the failure that should have
              been prevented - good "before/after" beat for the video.
~min 200-260  Convoy transit. All cameras down by design (not a fault).
              Good test that the agent does NOT false-alarm here.
~min 300-340  CAM-05 hit by 4G congestion at the public market. Intermittent,
              oscillating signal rather than a clean decay.
~min 380-420  CAM-02 overheats in direct sun. Temperature climbs while signal
              stays fine - tests whether the agent watches more than one metric.
~min 520-620  CAM-03's degraded battery pack collapses faster than its
              percentage would naively suggest. The agent should translate
              this into "roughly N minutes left", not just report a number.
~min 545-560  CAM-04 brief rooftop dropout, recovers on its own. Tests whether
              the agent distinguishes transient blips from real failures.
"""


def main():
    global SETUPS, CAMERAS

    here = os.path.dirname(os.path.abspath(__file__))

    ap = argparse.ArgumentParser()
    ap.add_argument("--call-sheet", default=os.path.join(here, "call_sheet.txt"),
                    help="path to the production's call sheet")
    ap.add_argument("--interval", type=int, default=30, help="sample interval, seconds")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--call-time", default=None,
                    help="override the call sheet's general call time")
    ap.add_argument("--today", action="store_true",
                    help="stamp the data with today's date instead of the call sheet's")
    ap.add_argument("--prefix", default="shoot_day")
    args = ap.parse_args()

    day = parse_call_sheet(args.call_sheet)
    SETUPS = day.setups
    CAMERAS = day.cameras

    print(f"Read call sheet: {args.call_sheet}\n")
    print(summarize(day))
    print()

    call_time = args.call_time or day.call_time

    shoot_date = None
    if not args.today:
        # "Tuesday, 01 September 2026" -> a real date, so the generated day
        # matches the sheet rather than whenever the script happened to run.
        cleaned = day.shoot_date.split(",")[-1].strip()
        for fmt in ("%d %B %Y", "%d %b %Y", "%B %d %Y", "%Y-%m-%d"):
            try:
                shoot_date = datetime.strptime(cleaned, fmt)
                break
            except ValueError:
                continue

    rows = generate(args.interval, args.seed, call_time, shoot_date)

    csv_path = f"{args.prefix}.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    json_path = f"{args.prefix}.json"
    with open(json_path, "w") as f:
        json.dump(rows, f, indent=2)

    cams = len({r["cam_id"] for r in rows})
    print(f"Wrote {len(rows)} rows ({cams} cameras) -> {csv_path}, {json_path}")
    print(SCENARIO_NOTES)


if __name__ == "__main__":
    main()
