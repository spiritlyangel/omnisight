# Omnisight

**Know which camera is about to fail — before it does.**

A monitoring agent for live television production. Omnisight reads wireless
camera telemetry and answers a director's questions in a director's language —
before the shoot, during it, and after.

- **Try it:** https://omnisight-web-708110251968.asia-southeast1.run.app
- **Live dashboard:** https://omnisighttelemetry.grafana.net/public-dashboards/38c7596404964e1bbf2b68602f88ea0d
- **Partner track:** Grafana Labs

---

## The problem

A teleserye unit shoots daily. Two or three locations a day, often far outside
Manila. The cameras are wireless, transmitting back to a main server so the
director can check the take in real time.

Two things go wrong, and a working director named them in this order.

**First: you cannot tell in advance whether a location will hold the link.** The
unit arrives somewhere remote and finds out by trial and error — set up, test,
move the relay, test again. Time that comes directly out of the shooting day.
What a director wants is to look at a location and know: will this work, and
where should the cameras go?

**Second: you find out a camera is failing when the monitor goes black.**
Mid-take. No warning. The director is suddenly working on a scene they cannot
see.

Both have the same shape. Everything needed to answer them is already being
measured — signal strength, distance, environment, battery, thermal, stream
quality. Nobody reads it, because reading instruments is not a job anyone on a
working set has time to do, and because raw readings don't answer the question
anyway. "-76 dBm" is not an answer to "where do I put Cam 3?"

The problem described here comes from an award-winning Philippine teleserye
director, whose debut feature won Best Picture and Best Director honors from
both the Star Awards and FAMAS. The workflow modelled here follows his account
of directing across multiple location shoots.

## What it does

### Before the shoot — will this location hold?

Paste tomorrow's call sheet into the chat page and ask. Omnisight reads the
locations, distances, camera assignments and pack conditions from it, then
checks them against what the fleet actually recorded in comparable conditions.

From a real exchange, for a location that appears nowhere in the telemetry:

> **You will likely lose your monitor feeds on Cam 3 (Kiko) and Cam 5 (Jorge).**
> Today in the Ancestral House, the stone walls completely froze Kiko's monitor
> and turned Jorge's feed into a stuttering mess. Tomorrow they are pushed even
> further out — 70m and 90m into the far wing. Because they record locally your
> takes are safe, but expect to shoot blind on those two monitors.
>
> **Kiko's battery will not survive the block.** The call sheet lists his pack as
> "aging," and our telemetry confirms it: today it drained at double the rate of
> the rest of the fleet. It will die about three hours into this four-and-a-half
> hour block. Swap it before the first setup, or schedule a hard swap no later
> than 09:30.

Nobody wrote that reasoning. It is the call sheet, the telemetry, and the
system prompt.

### During the shoot — what is about to fail?

Omnisight reasons from trend rather than current value. A camera at -76 dBm and
falling matters more than one sitting steady at -79. It reports battery in
minutes, not percent, using each pack's measured drain rate — and flags packs
fading faster than the fleet.

### After the shoot — what should change tomorrow?

Where and when signal was lost, whether footage was at risk, which packs
underperformed.

### Why an agent rather than a dashboard

A dashboard shows five wobbly lines and leaves the reading to you. A director
does not have time to read, and should not need to learn what dBm means.

The product is the translation:

> Not: *"CAM-03 is at 69% battery, draining 0.43%/min."*
>
> But: *"About two hours forty on Kiko's camera. But that pack is fading at
> nearly double the fleet rate — treat the estimate with caution and swap it at
> the next break."*

Knowing when to stay quiet matters just as much. When the whole fleet drops off
at once, the unit is in a van between locations — that is a convoy, not five
failures. A tool that cries wolf during a planned transit gets switched off by
lunch, so the suppression rules are as carefully specified as the alerts.

## The call sheet is the configuration

Omnisight does not ask a director to configure anything.

Wireless video transmitters already report their own health — signal strength,
battery, temperature, bitrate — to the receiver. That is how a base station
knows which transmitter it is paired with. What the hardware cannot supply is
meaning: the receiver knows a device is at -74 dBm, not that it is Kiko's
camera, in the far bedroom, on a pack the camera department flagged twice last
week.

The call sheet supplies exactly that missing layer. Every production already
distributes one the night before — roster, operators, locations, INT/EXT,
schedule, pack condition — and somebody is already responsible for its accuracy.

So the only manual step is a one-time pairing: on the first day, whoever runs
video village maps each transmitter to a camera slot. Five minutes, once. After
that the system reads the call sheet each morning and everything else follows.

In this repository the call sheet drives the telemetry generator
(`data/call_sheet.txt` → `data/call_sheet.py`), and the chat page accepts a
pasted or uploaded sheet at runtime for pre-shoot questions.

## Architecture

| Layer | What it does |
|---|---|
| Call sheet | Roster, operators, locations, setups, schedule, pack condition — the production's own document |
| Telemetry | Per-camera samples at 30s intervals: signal, battery, temperature, link state, bitrate, dropped frames, latency, local-recording flag |
| **Grafana Cloud** | Stores and visualises the fleet; public dashboard; queried live by both MCP servers below |
| **`grafana/mcp-grafana`** | The official Grafana MCP server, deployed to Cloud Run over streamable-HTTP, read-only |
| Omnisight MCP server | A Cloud Function wrapped in MCP. Queries Grafana's `/api/ds/query` and computes what raw readings cannot express: signal trend, battery drain rate, minutes remaining, pack-swap detection |
| **Gemini agent** | Built in Agent Studio on the Gemini Enterprise Agent Platform. Calls both toolsets and translates telemetry into director language |
| Chat page | Cloud Run service running the same ADK agent, open to anyone, with a call sheet panel |

Two MCP servers, deliberately. The official `grafana/mcp-grafana` server is the
runtime connection to the Grafana stack. Omnisight's own server sits alongside
it doing the domain arithmetic — rates of change, not point readings — because
"26% battery" and "37 minutes left" are the same fact but only one of them is an
answer.

The Grafana integration is live, not decorative. The agent never reads the data
file. Every answer comes from a runtime query against Grafana Cloud.

## Repository

```
agent/
  agent.py            ADK agent definition — model, instructions, MCP toolsets
  system_prompt.md    The translation logic: thresholds, suppression, severity
  grafana_tool.py     Queries Grafana Cloud; computes derived rates
  mcp_server.py       MCP wrapper (JSON-RPC over HTTP)
data/
  call_sheet.txt      The production's call sheet — the fleet configuration
  call_sheet.py       Call sheet parser
  generate_telemetry.py   Shoot-day telemetry generator
web/
  index.html          The chat page
  main.py             Flask service running the agent
  agent_def.py        Agent definition for the web service
docs/
  alert-rules.md      Full alert specification and suppression rules
shoot_day.json        Generated sample day, 6,200 rows
```

## Running it

Generate a shoot day from a call sheet:

```bash
python3 data/generate_telemetry.py                        # reads call_sheet.txt
python3 data/generate_telemetry.py --call-sheet other.txt
```

Deploy the official Grafana MCP server:

```bash
gcloud run deploy grafana-mcp \
  --image=docker.io/grafana/mcp-grafana:latest \
  --region=asia-southeast1 --allow-unauthenticated --port=8000 \
  --set-env-vars GRAFANA_URL=...,GRAFANA_SERVICE_ACCOUNT_TOKEN=... \
  --args="^|^-t|streamable-http|--address|:8000|--allowed-hosts|<your-run-host>|--enabled-tools|dashboard|--disable-write"
```

Deploy the Omnisight MCP server:

```bash
gcloud functions deploy omnisight-mcp \
  --gen2 --runtime=python311 --region=asia-southeast1 \
  --source=agent --entry-point=mcp \
  --trigger-http --allow-unauthenticated \
  --set-env-vars GRAFANA_URL=...,GRAFANA_DS_UID=...,GRAFANA_TOKEN=...
```

Deploy the chat page:

```bash
gcloud run deploy omnisight-web \
  --source=web --region=asia-southeast1 \
  --allow-unauthenticated --memory=1Gi --timeout=300 \
  --set-env-vars GOOGLE_GENAI_USE_VERTEXAI=1,GOOGLE_CLOUD_PROJECT=...,GOOGLE_CLOUD_LOCATION=global
```

## The simulated shoot day

`data/call_sheet.txt` describes a day across four setups — an ancestral house
interior, a convoy, a public market exterior, and a night rooftop — with five
cameras and named operators. The generator turns it into 6,200 rows spanning
07:00 to 17:19.

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
fleet changes nothing above the ingest layer.

**Local recording is modelled, not confirmed.** Omnisight treats a dropout on a
locally-recording camera as a lost monitor feed with the take intact, and a
dropout without local recording as a lost take. This single fact drives severity
across half the alert rules. It reflects standard practice but has not yet been
confirmed against this unit's actual workflow.

**Call sheets are parsed from structured text.** Real call sheets arrive as PDFs
and images in a hundred different house formats. The parser here reads a
structured plain-text sheet. Extraction from a real PDF is a solved problem and
an obvious next step, but it is not solved here.

**Pre-shoot assessment reasons from history, not survey.** Location advice comes
from what comparable setups actually recorded. It does not incorporate floor
plans, RF site surveys, or terrain data. The agent will sometimes reach for
general knowledge about building materials to explain a pattern; the numbers it
cites come from telemetry, the masonry does not.

**Thresholds are a starting point.** The dBm bands and drain-rate multipliers in
`docs/alert-rules.md` are reasoned defaults. Real deployment would tune them per
location and per transmitter model.

## What's next

**Read the call sheet as it arrives.** Today it has to be structured text. It
should accept the PDF the 2nd AD already emailed.

**Be there at the ocular.** The call sheet arrives the night before, when the
location is already locked and all that's left is mitigation. The moment that
actually matters is weeks earlier, at the recce — when a location manager walks
a space with a phone and the decision is still open. That's when moving the base
station is free, when a relay can go into the budget, when a room can be ruled
out. Omnisight should be in that walk: stand here, point at the far wing, and
get told this won't hold at 70 metres.

**Reconcile the call sheet's date against the telemetry's.** A call sheet is
authoritative about what day it is on a set, and the agent treats it that way —
correctly. But it doesn't yet check whether the sheet it's holding describes the
day the telemetry covers. Handed a future sheet alongside today's readings, it
concludes the feed has gone stale rather than recognising it is looking at
tomorrow's plan and today's history. The reasoning is right; it just needs both
dates in front of it.

**Per-location memory.** A unit shoots the same locations repeatedly. Omnisight
should remember that the second-floor bedroom always costs 8 dBm, and say so
before the crew walks in.

**Speak first, quietly.** Right now the director asks. The valuable version
warns without being asked — but a director mid-take does not want to be talked
to. The right form is glanceable, not spoken: a strip along the edge of the
monitor turning amber, information they can take or ignore.

**Real hardware.** Live transmitter telemetry in place of the generator.

## License

MIT. See [LICENSE](LICENSE).
