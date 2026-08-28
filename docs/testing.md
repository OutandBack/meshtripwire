# Testing

Assert-style test scripts, no framework, run from the repo root with the venv
interpreter. Every non-trivial code path was built test-first.

## The suites

```bash
venv/bin/python test_smoke.py               # monitor: pipeline, arming, events,
                                            # correlation, notifications, backfill
venv/bin/python -m mqtt.test_events         # canonical schema + type registry
venv/bin/python -m mqtt.test_serial_bridge  # Meshtastic bridge + compact lines
venv/bin/python -m mqtt.test_rns_bridge     # LXMF text/naming mapping
venv/bin/python -m dashboard.test_server    # dashboard queries: events, nodes,
                                            # notifications, search, facets
```

All suites end with an `... OK` line; any assertion failure is a real
regression. Network channels are mocked in tests — no test sends a real
notification.

## Firmware

Sketches compile-check with `arduino-cli`:

```bash
arduino-cli compile --jobs 2 --fqbn esp32:esp32:esp32c3:CDCOnBoot=cdc firmware/esp32_sniffer
```

There is no on-target test harness; the `DEBUG_PRINT` serial readout is the
verification tool — flash, watch the once-per-second line, exercise the
sensor physically ([calibration guides](firmware.md)).

## End-to-end against a live stack

```bash
docker compose up -d --build
mosquitto_pub -t meshtastic/receive -m '{"event":"knock","from":"bench","peak":700}'
docker compose logs -f monitor        # classification + alert dispatch
curl -s localhost:8080/api/events?limit=3
```

A synthetic event exercises ingest → classification → storage → alerting →
dashboard in one command. The alert channels fire for real here — point ntfy
at a scratch topic first if you don't want the ping.
