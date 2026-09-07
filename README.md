# Omnisight

**Know which camera is about to fail — before it does.**

A monitoring agent for live television production. Omnisight watches wireless
camera telemetry across a shoot and tells the director what is about to break,
in the language they actually work in.

- **Live dashboard:** https://omnisighttelemetry.grafana.net/public-dashboards/38c7596404964e1bbf2b68602f88ea0d
- **Partner track:** Grafana Labs

---

## The problem

A teleserye unit shoots two or three locations a day. The cameras are wireless,
transmitting to a base station so the director can watch the shot on a monitor
while it happens.

When a link degrades, the director finds out the way everyone else does: the
monitor goes black. Mid-take. The picture was fine and then it wasn't, and now
they are directing a scene they cannot see.

Everything needed to predict that failure was already in the telemetry ten
minutes earlier. Nobody was reading it, because reading it is not a job anyone
on a set has time to do.

The problem addressed here was identified by a working Philippine teleserye
director — an award-winning filmmaker whose debut feature won Best Picture and
Best Director honors from both the Star Awards and FAMAS. He described the three
phases of a shoot day where camera reliability decides whether a take survives,
and named the failure that costs the most: losing the monitor feed mid-take,
with no warning. The workflow modelled here follows his account of directing
across multiple location shoots.

## What it does

Omnisight answers three questions, before, during and after the shoot.

**What is my fleet status?** A plain read of every camera, leading with whatever
needs attention. If nothing does, it says so in one line and stops.

**What is going to fail next?** The predictive question. It reasons from trend
rather than current value — a camera at -76 dBm and falling matters more than
one sitting steady at -79 dBm.

**How long do I actually have?** Battery percentage is close to meaningless on a
set. Omnisight converts it to minutes using the pack's measured drain rate, and
flags packs that are fading faster than the fleet.

### Why an agent rather than a dashboard

A dashboard shows five wobbly lines and leaves the reading to you. A director
does not have time to read, and should not need to learn what dBm means.

The product is the translation:

> Not: *"CAM-03 is at -81 dBm with a trend of -0.4 dBm/min and 26% battery."*
>
> But: *"Cam 3 is about ten minutes from losing your feed. Its pack is draining
> at double the normal rate, so treat that 26% as fifteen minutes, not forty."*

Knowing when to stay quiet matters just as much. When the whole fleet drops off
at once, the unit is in a van between locations — that is a convoy, not five
failures. A tool that cries wolf during a planned transit gets switched off by
lunch, so the suppression rules are as carefully specified as the alerts.

## Architecture

| Layer | What it does |
|---|---|
| Telemetry | Per-camera samples at 30s intervals: signal, battery, temperature, link state, bitrate, dropped frames, latency, local-recording flag |
| **Grafana Cloud** | Stores and visualises the fleet; public dashboard; queried live by the agent over the HTTP API |
| Cloud Function | Calls Grafana's `/api/ds/query`, computes derived rates — signal trend, drain rate, minutes remaining, pack-swap detection |
| MCP server | Exposes that function as a Model Context Protocol tool |
| **Gemini agent** | Calls the tool and translates telemetry into director language |

The Grafana integration is live, not decorative: the agent does not read the
data file directly. Every answer it gives comes from a runtime query against the
Grafana Cloud API.

## Repository

```
agent/
  agent.py            ADK agent definition — model, instructions, MCP toolset
  system_prompt.md    The translation logic: thresholds, suppression, severity
  grafana_tool.py     Queries Grafana Cloud; computes derived rates
  mcp_server.py       MCP wrapper (JSON-RPC over HTTP)
data/
  generate_telemetry.py   Shoot-day telemetry generator
docs/
  alert-rules.md      Full alert specification and suppression rules
shoot_day.json        Generated sample day, 6,200 rows
```

## Running it

Generate a shoot day:

```bash
python3 data/generate_telemetry.py --call-time 07:00
```

Deploy the tool and MCP endpoints:

```bash
gcloud functions deploy omnisight-mcp \
  --gen2 --runtime=python311 --region=asia-southeast1 \
  --source=agent --entry-point=mcp \
  --trigger-http --allow-unauthenticated \
  --set-env-vars GRAFANA_URL=...,GRAFANA_DS_UID=...,GRAFANA_TOKEN=...
```

Then attach the MCP endpoint to the agent and give it `system_prompt.md` as its
instructions.

## The simulated shoot day

`shoot_day.json` covers 07:00 to 17:19 across four setups — an ancestral house
interior, a convoy, a public market exterior, and a night rooftop — with five
cameras and named operators.

Seven incidents are engineered into it, each testing a different judgement:

| Time | Camera | Event | What it tests |
|---|---|---|---|
| 120–165 | CAM-03 | Signal decay | Predictive lead time |
| 165–180 | CAM-03 | Feed lost | The failure that should have been prevented |
| 200–260 | All | Convoy, offline by design | That the agent does **not** false-alarm |
| 300–340 | CAM-05 | 4G congestion, oscillating | Intermittent vs clean decay |
| 380–420 | CAM-02 | Thermal rise, signal fine | Watching more than one metric |
| 520–620 | CAM-03 | Battery collapse | Minutes remaining vs percentage |
| 545–560 | CAM-04 | Brief blip, self-recovers | Transient vs real failure |

Two numbers from that day are worth stating plainly:

**31 minutes of lead time.** The warning threshold on CAM-03 is crossed at
minute 134. The feed is lost at minute 165. Half an hour is enough to move a
relay, reposition, or reorder the shot list.

**The battery deception.** At minute 535 CAM-03 reads 72% — about 174 minutes.
At minute 615 it reads 26% — about 37 minutes. The same scale, but a percentage
point late in a fading pack's life is worth a fraction of one early on.

## Assumptions and limitations

**The telemetry is simulated.** The pipeline is real — data genuinely flows into
Grafana Cloud, and the agent genuinely queries it at runtime — but the samples
are generated rather than captured from hardware. Integrating a real transmitter
fleet is the obvious next step and changes nothing above the ingest layer.

**Local recording is modelled, not confirmed.** Omnisight treats a dropout on a
locally-recording camera as a lost monitor feed with the take intact, and a
dropout without local recording as a lost take. This single fact drives severity
across half the alert rules. It reflects standard practice but has not yet been
confirmed against this unit's actual workflow — that verification is pending.

**Thresholds are a starting point.** The dBm bands and drain-rate multipliers in
`docs/alert-rules.md` are reasoned defaults. Real deployment would tune them per
location and per transmitter model.

## License

MIT. See [LICENSE](LICENSE).
