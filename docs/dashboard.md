# Dashboard

A built-in, read-only status page at port 8080. Stdlib Python + one
self-contained HTML page per view: no framework, no CDN, no external assets,
because the whole point is working where there is no Internet.

## Live view (`/`)

- **24h strip chart**: one row per node, events as color-coded ticks
  (vehicle amber, vibration ember, contact teal, wireless slate). Correlated
  events **line up vertically across rows**: the chart shows the fusion
  logic structurally.
- **Nodes**: per-node last-seen and totals; nodes silent past 30 minutes
  flag in red.
- **Notification log**: the last 40 delivery attempts with ✓/✗ and errors.
- **Recent events**: live feed, refreshing every 5 s, with an
  "unknown & strong signals only" filter that hides whitelisted gear and weak
  sightings.

## History search (`/history`)

Searches **all** recorded activity (pre-v0.2 detections are backfilled in):

- free text: matches node names, event names, and meta content including MACs
- type / node / event dropdowns, populated from the actual data
- date/time range with native pickers, converted to UTC for the query
- 100 rows per page with "load older"

## Night mode

The **NIGHT** toggle switches both pages to red-flashlight mode: every color
becomes a brightness-ranked red so checking the dashboard at 2 AM doesn't
destroy your night vision. Event types rank by brightness (hue carries no
meaning under red light). Persisted per browser; `?theme=night` deep-links it.

## Mobile

Both pages are built for one-handed phone use: the event feed leads the live
view, feed rows break MACs onto their own full-width line, history results
render as card rows with collapsed-by-default filters, and nothing ever
scrolls horizontally.

## API

The dashboard's endpoints are plain JSON over GET, usable by scripts and
automations directly:

| Endpoint | Returns |
|---|---|
| `/api/events?limit=` | recent canonical events, newest first |
| `/api/nodes` | per-node last-seen and event count |
| `/api/notifications?limit=` | alert delivery attempts per channel |
| `/api/search?q=&type=&node=&event=&from=&to=&limit=&offset=` | filtered event search |
| `/api/facets` | distinct types/nodes/events for filter UIs |

## Security posture

Read-only by design: no arm/disarm buttons, no configuration endpoints;
control stays on the secret-gated MQTT topic. All MQTT-sourced strings are
HTML-escaped before rendering (sensor names arrive from a broker that may
allow anonymous LAN publishes; see [Arming & Security](security.md)).
