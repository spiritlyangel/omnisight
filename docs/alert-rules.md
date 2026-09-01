# Alert Rules — Camera Fleet Monitoring for Live Shoots

Ground truth for both Grafana alerting and the Gemini agent's reasoning layer.
Every rule has: a **trigger**, a **dwell time** (how long it must hold before
firing — this is what prevents alert spam), a **severity**, and a
**director-facing message**.

Design principle: a director mid-take can absorb roughly one sentence. The
number goes in the detail view; the *action* goes in the alert.

---

## 0. Global suppression — check this FIRST

No alert fires if any of these hold. This is the single most important rule
in the file: a monitoring tool that cries wolf during a planned convoy gets
switched off by lunch and never comes back.

| Condition | Why |
|---|---|
| `environment == 'transit'` | Cameras packed and powered down by design |
| Camera marked `standby` by operator | Deliberately parked |
| Setup change within last 8 min | Crew is repositioning; signal is expected to be chaotic |
| Camera powered on < 3 min ago | Warm-up / link negotiation |

**Implementation note:** transit windows come from the shoot schedule, not
inferred from the data. Infer it and you *will* misclassify a real
simultaneous failure as a planned move.

---

## 1. Signal degradation (predictive) — THE flagship rule

The whole pitch rests on this one: catch it before the feed is lost.

| Level | Trigger | Dwell | Severity |
|---|---|---|---|
| Watch | `signal_dbm` < -70 | 90 s | info |
| Warn | `signal_dbm` < -76 **and** falling ≥ 3 dBm over last 5 min | 120 s | warning |
| Critical | `signal_dbm` < -82 | 60 s | critical |

The **trend** term in the Warn rule is what makes this predictive rather than
reactive. A camera steady at -77 is fine — that's just a far unit. A camera
that *was* -66 and is now -77 is on its way out.

Director-facing:
- Watch → *(no interruption — dashboard only)*
- Warn → "Cam 3 signal is dropping — likely to lose feed in ~10 min. Move the relay or reposition."
- Critical → "Cam 3 about to drop. Hold the take or switch coverage."

**Validate against the mock data:** CAM-03 crosses Warn around min 130-140 and
drops at min 170 — roughly 30-40 min of lead time. That gap is the demo.

---

## 2. Battery — report TIME, never percentage

A director cannot act on "39%." Degraded packs collapse non-linearly, so the
percentage actively misleads late in a pack's life.

```
drain_rate = (batt_15min_ago - batt_now) / 15        # %/min, rolling
minutes_remaining = batt_now / max(drain_rate, 0.01)
```

| Level | Trigger | Dwell | Severity |
|---|---|---|---|
| Watch | `minutes_remaining` < 45 | 2 min | info |
| Warn | `minutes_remaining` < 25 | 2 min | warning |
| Critical | `minutes_remaining` < 12 | 1 min | critical |
| Fade | drain_rate > 1.6× this camera's baseline rate | 5 min | warning |

The **Fade** rule is what catches a dying pack that still reads healthy.
CAM-03 at night is exactly this case — mid-30s percentage, but draining at
double rate, so it has far less time left than the number implies.

Director-facing:
- Warn → "Cam 3 has about 20 minutes of battery. Swap at the next reset."
- Fade → "Cam 3's pack is draining faster than normal — treat 35% as ~15 min, not 35."

---

## 3. Feed loss (reactive)

| Level | Trigger | Dwell | Severity |
|---|---|---|---|
| Blip | `link_state == DOWN` | 20 s | info |
| Down | `link_state == DOWN` | 60 s | critical |
| Fleet | ≥ 2 cameras DOWN simultaneously | 45 s | critical |

The Blip/Down split matters: CAM-04's rooftop dropout self-recovers in ~15 min
of shoot time but only a fraction of that in link terms. Firing "CRITICAL" on
every momentary blip destroys trust in the system.

**Fleet rule** is separate because two cameras dropping at once is almost never
two coincidental camera faults — it's the base station, the power, or the
uplink. Different problem, different person to wake up.

Director-facing:
- Down → "Lost Cam 3. Still recording locally — the take is safe, but you're blind on that angle."
- Fleet → "Multiple cameras down — likely base station, not the cameras. Check the uplink."

The "still recording locally" line matters enormously and is worth confirming
with Direk: if cameras record to card regardless of transmission, a dropout is
an *inconvenience*, not a lost take. If they don't, it's a catastrophe. This
single fact changes the severity of half this file.

---

## 4. Thermal

| Level | Trigger | Dwell | Severity |
|---|---|---|---|
| Watch | `temperature_c` > 38 | 3 min | info |
| Warn | `temperature_c` > 42 | 3 min | warning |
| Critical | `temperature_c` > 46, or rising > 0.4 °C/min for 10 min | 2 min | critical |

Deliberately independent of signal — CAM-02 at the market overheats while its
signal stays perfectly fine. A system that only watches signal misses it
entirely.

Director-facing:
- Warn → "Cam 2 is heating up in the sun. Shade it or plan a cooldown before the next setup."

---

## 5. Quality degradation (feed is up, but the picture isn't usable)

| Level | Trigger | Dwell | Severity |
|---|---|---|---|
| Warn | `dropped_frames` > 40/sample for 3 consecutive samples | 90 s | warning |
| Warn | `latency_ms` > 600 | 60 s | warning |
| Critical | `bitrate_mbps` < 8 while `transmitting == true` | 60 s | critical |

The latency rule is the sneaky one: the director is watching a feed that's a
full second behind, so their notes land on the wrong moment. Worth flagging
even when the link nominally reads "OK."

Director-facing:
- Latency → "Cam 5's feed is running ~1s behind. What you're seeing already happened."

---

## 6. Pre-shoot location assessment (not live alerting)

Runs before the crew moves, from historical data at that location.

| Output | Logic |
|---|---|
| Risk score per camera position | Historical mean signal at similar distance + environment |
| Predicted dead zones | Positions where past signal < -76 |
| Relay recommendation | If any planned camera > 60 m in interior, or > 90 m exterior |
| Battery plan | Setup duration ÷ per-camera observed drain rate → packs needed |
| Max supported cameras | Count before aggregate bitrate exceeds base station capacity |

Director-facing summary:
> "Location B, 3-hour block. Cam 5's planned position is a likely dead zone —
> add a relay or move it 20 m closer. Bring 2 spare packs for Cam 3; its
> battery won't finish the block."

---

## 7. Severity → delivery routing

Ask Direk about this. Educated guess until then:

| Severity | Delivery |
|---|---|
| info | Dashboard only, no notification |
| warning | Notify the AD / technical crew — **not** the director mid-take |
| critical | Director sees it, but between takes if at all possible |

The instinct to route warnings to the AD rather than the director is worth
testing with him directly. Some directors want everything; most want a working
system that only interrupts them when the answer is "stop shooting."

---

## Questions this file raises for the interview

1. Do cameras record locally as well as transmit? (Changes severity everywhere.)
2. How much warning is actually useful — is 10 minutes enough to act, or do you need 30?
3. Who should receive alerts: you, the AD, or the technical crew?
4. Would an alert mid-take be useful or unacceptable?
5. When a camera drops now, how do you find out — does someone tell you?
6. Is a 1-second-delayed feed something you'd notice or care about?
7. How many spare battery packs do you normally carry, and has that ever run out?
