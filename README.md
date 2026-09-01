# Omnisight

**An AI monitoring agent for live television production.** It watches a wireless
camera fleet in real time and tells the director what to do about it — in plain
language, before something breaks.

Built for the [Agentic Cinema: The Blockbuster Hackathon](https://devpost.com/)
— Grafana Labs partner track.

---

## The problem

On a Philippine teleserye set, a director may shoot across two or three
locations in a single day, with cameras transmitting wirelessly back to a base
station. When a camera's signal degrades, the director loses their monitor feed
and shoots blind — or loses the take entirely.

Today they find out when the picture disappears.

## What Omnisight does

Omnisight ingests live camera telemetry — signal strength, battery, temperature,
latency, dropped frames — and layers a Gemini-powered agent on top of Grafana's
observability stack to answer the questions a director actually has:

**Before the shoot**
- Will the signal hold at this location?
- Where are the likely dead zones?
- How many spare batteries do we need for this block?

**During the shoot**
- Which camera is about to drop, and how long do I have?
- Is any camera recording but not transmitting?
- Do I need to pause the take, or can we push through?

**After the shoot**
- Where and when did we lose signal today?
- What should change at this location next time?

## Why an agent, not a dashboard

A dashboard reports that Camera 3 is at -81 dBm and 26% battery.

Omnisight says:

> *"Cam 3 will lose feed in about 10 minutes — move the relay or reposition.
> Its pack is draining at double rate, so treat 26% as 15 minutes, not 40."*

The translation is the product. A director mid-take cannot read graphs; they can
act on one sentence.

### Validated lead time

Against a simulated shoot day, Omnisight's predictive signal rule flagged a
failing camera **31 minutes before** the feed was lost — enough time to move a
relay, reposition an operator, or reorder the shot list.

```
CAM-03   Warning fires: min 134   Feed lost: min 165   Lead time: 31 min
```

The battery rule shows the same gap between a number and its meaning. Late in a
degraded pack's life, 26% remaining is roughly 37 minutes — while earlier in the
same block, 72% was 174 minutes. Percentages mislead; Omnisight reports time.

---

## Architecture

```
Camera telemetry  ──▶  Grafana (time-series + alerting)  ──▶  Gemini agent  ──▶  Director
   (simulated)          thresholds, dwell times,              translation,
                        suppression rules                     recommendation
```

| Layer | Technology |
|---|---|
| Agent orchestration | Gemini Enterprise Agent Platform (Google Cloud) |
| Observability, alerting | Grafana Labs |
| Telemetry source | Simulated fleet (see `data/`) |

## Repository layout

```
data/generate_telemetry.py   Mock telemetry generator for a full shoot day
docs/alert-rules.md          Alert thresholds, dwell times, suppression logic
```

## Running the telemetry generator

Requires Python 3 only — no dependencies.

```bash
cd data
python3 generate_telemetry.py
```

Writes `shoot_day.csv` and `shoot_day.json`: 6,200 rows across 5 cameras at
30-second intervals.

Options:

```bash
python3 generate_telemetry.py --seed 7        # different variation
python3 generate_telemetry.py --interval 15   # sample every 15 seconds
python3 generate_telemetry.py --call-time 09:00
```

### What the simulated day contains

Four setups: interior ancestral house → convoy transit → public market →
night rooftop. Five cameras with different operators, distances from base, and
battery health.

Failure scenarios are deliberately engineered into the data so the agent's
behaviour can be tested against known events:

| When | What | Tests |
|---|---|---|
| min 120–165 | CAM-03 signal decays, then drops | Predictive warning lead time |
| min 200–260 | Convoy transit, all cameras offline | That the agent does **not** false-alarm |
| min 300–340 | CAM-05 hit by 4G congestion at the market | Oscillating vs. steady degradation |
| min 380–420 | CAM-02 overheats while signal stays fine | Watching more than one metric |
| min 520–620 | CAM-03's degraded pack collapses early | Time-remaining vs. percentage |
| min 545–560 | CAM-04 brief rooftop blip, self-recovers | Transient vs. real failure |

## Alert rules

See [`docs/alert-rules.md`](docs/alert-rules.md) for the full specification.

The most important rule is the suppression logic that runs first: no alert fires
during a planned convoy, a setup change, or camera warm-up. A monitoring tool
that cries wolf during a scheduled move gets switched off by lunch and never
comes back.

---

## Status

Telemetry is currently simulated pending hardware integration. The pipeline is
real: data flows into Grafana, and the agent queries and reasons over it live.

Developed in consultation with an award-winning Philippine teleserye director — a film graduate of Los Angeles City College whose debut feature became one of the most decorated films in Philippine cinema history, winning Best Picture and Best Director honors from both the Star Awards and FAMAS. The workflow described here comes from his daily experience directing across multiple location shoots.

## License

MIT — see [LICENSE](LICENSE).
