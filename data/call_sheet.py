"""
Call sheet parser.

Every production already produces a call sheet the night before: locations,
setups, times, camera assignments, operators. That is exactly the configuration
a monitoring system needs, in a document someone is already responsible for
keeping accurate. Nobody should have to type it in twice.

This module reads a call sheet and returns the shoot day structure:

    day = parse_call_sheet("call_sheet.txt")
    day.call_time      -> "07:00"
    day.setups         -> [Setup(...), ...]   minutes offset from call time
    day.cameras        -> [Camera(...), ...]

Stdlib only. Tolerant of spacing and case; raises with a useful message when a
required block is missing rather than failing silently on a half-read sheet.
"""

import re
from dataclasses import dataclass, field


@dataclass
class Setup:
    """One location setup within the shoot day."""
    name: str
    start_min: int
    end_min: int
    environment: str        # 'interior', 'exterior', 'transit'
    interference: float     # 0.0-1.0, read from the RF note
    notes: str = ""


@dataclass
class Camera:
    cam_id: str
    model: str
    operator: str
    base_distance_m: float
    battery_health: float
    notes: str = ""


@dataclass
class ShootDay:
    production: str = ""
    shoot_date: str = ""
    call_time: str = "07:00"
    setups: list = field(default_factory=list)
    cameras: list = field(default_factory=list)


# INT./EXT. is standard slugline notation; TRANSIT is our own addition for the
# stretch where the unit is in a van and nothing is transmitting.
ENVIRONMENTS = {
    "INT": "interior",
    "EXT": "exterior",
    "TRANSIT": "transit",
}

# The 1st AD writes an RF note in plain language. Map it to a noise floor.
INTERFERENCE = [
    ("heavy mobile congestion", 0.55),
    ("heavy attenuation", 0.25),
    ("congestion", 0.50),
    ("clear", 0.20),
]

# Camera department rates packs by condition, not by percentage.
PACK_CONDITION = {
    "good": 0.95,
    "fair": 0.85,
    "aging": 0.61,
    "poor": 0.45,
}


class CallSheetError(ValueError):
    pass


def _to_minutes(clock):
    h, m = clock.split(":")
    return int(h) * 60 + int(m)


def _interference_for(text):
    low = text.lower()
    for phrase, value in INTERFERENCE:
        if phrase in low:
            return value
    return 0.30


def _parse_header(text, day):
    m = re.search(r"PRODUCTION:\s*(.+?)\s{2,}", text)
    if m:
        day.production = m.group(1).strip()

    m = re.search(r"DATE:\s*(.+)", text)
    if m:
        day.shoot_date = m.group(1).strip()

    m = re.search(r"GENERAL CALL:\s*(\d{1,2}:\d{2})", text)
    if not m:
        raise CallSheetError("No GENERAL CALL time found in call sheet.")
    day.call_time = m.group(1)


def _section(text, title):
    """Return the body of a dashed-underline section, or '' if absent."""
    pattern = rf"^{title}\s*\n-+\n(.*?)(?=\n-{{5,}}|\n={{5,}}|\Z)"
    m = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    return m.group(1) if m else ""


def _parse_setups(text, call_min):
    body = _section(text, "SETUPS")
    if not body:
        raise CallSheetError("No SETUPS section found in call sheet.")

    setups = []
    # A setup starts with a time range, then INT./EXT./TRANSIT, then a name.
    blocks = re.split(r"\n(?=\d{1,2}:\d{2}-\d{1,2}:\d{2}\s)", body.strip())

    for block in blocks:
        head = re.match(
            r"(\d{1,2}:\d{2})-(\d{1,2}:\d{2})\s+(INT\.?|EXT\.?|TRANSIT)\s+(.+)",
            block.strip(),
        )
        if not head:
            continue

        start, end, env_token, name = head.groups()
        env_key = env_token.rstrip(".").upper()

        setups.append(
            Setup(
                name=name.strip(),
                start_min=_to_minutes(start) - call_min,
                end_min=_to_minutes(end) - call_min,
                environment=ENVIRONMENTS.get(env_key, "exterior"),
                interference=_interference_for(block),
                notes=" ".join(line.strip() for line in block.splitlines()[1:]).strip(),
            )
        )

    if not setups:
        raise CallSheetError("SETUPS section found but no setups could be read.")
    return setups


def _parse_cameras(text):
    body = _section(text, "CAMERA DEPARTMENT")
    if not body:
        raise CallSheetError("No CAMERA DEPARTMENT section found in call sheet.")

    cameras = []
    for line in body.splitlines():
        if "|" not in line:
            continue

        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3 or not re.match(r"^CAM-\d+$", parts[0], re.I):
            continue

        cam_id, model, operator = parts[0].upper(), parts[1], parts[2]
        rest = " ".join(parts[3:])

        # "POSITION: roaming, 68m from base"
        dist_m = re.search(r"(\d+(?:\.\d+)?)\s*m\b", rest)
        distance = float(dist_m.group(1)) if dist_m else 40.0

        # "PACK: aging"
        pack_m = re.search(r"PACK:\s*(\w+)", rest, re.I)
        health = PACK_CONDITION.get(
            pack_m.group(1).lower() if pack_m else "", 0.90
        )

        position = ""
        pos_m = re.search(r"POSITION:\s*([^|]+)", rest, re.I)
        if pos_m:
            position = pos_m.group(1).strip()

        cameras.append(
            Camera(
                cam_id=cam_id,
                model=model,
                operator=operator,
                base_distance_m=distance,
                battery_health=health,
                notes=position,
            )
        )

    if not cameras:
        raise CallSheetError("CAMERA DEPARTMENT section found but no cameras could be read.")
    return cameras


def parse_call_sheet(path):
    """Read a call sheet file and return a ShootDay."""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    day = ShootDay()
    _parse_header(text, day)
    call_min = _to_minutes(day.call_time)
    day.setups = _parse_setups(text, call_min)
    day.cameras = _parse_cameras(text)
    return day


def summarize(day):
    """One-screen summary, printed after parsing so mistakes are obvious."""
    lines = [
        f"Production : {day.production}",
        f"Date       : {day.shoot_date}",
        f"Call time  : {day.call_time}",
        "",
        "Setups:",
    ]
    for s in day.setups:
        lines.append(
            f"  {s.start_min:>4}-{s.end_min:<4} min  {s.environment:<9} "
            f"interference {s.interference:.2f}  {s.name}"
        )
    lines.append("")
    lines.append("Cameras:")
    for c in day.cameras:
        lines.append(
            f"  {c.cam_id}  {c.model:<11} {c.operator:<18} "
            f"{c.base_distance_m:>5.0f}m  pack {c.battery_health:.2f}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "call_sheet.txt"
    print(summarize(parse_call_sheet(path)))
