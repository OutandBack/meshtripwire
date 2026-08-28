# Hardware

Nothing here is required all at once — build the base station, then add
whichever sensors fit your site. Prices are rough street prices (mid-2026,
USD) for the cheap-clone tier; brand-name versions cost more. Product links
are Amazon affiliate links (they help fund the project; buy anywhere you
like).

| Role | Part | ~Cost | Notes |
|------|------|-------|-------|
| **Base station** | [Raspberry Pi 4 (2GB+)](https://amzn.to/4hT1AlV) | $45–99 | Runs the whole Docker stack. A Pi Zero 2 W (~$15) works for light loads. |
| | microSD 16GB+ | $6 | Or boot from USB/SSD. |
| **Base scanner radios** | USB BLE adapter | $8–12 | Any BlueZ-compatible dongle; many Pis have BLE built in ($0). |
| | [USB WiFi adapter w/ monitor mode](https://amzn.to/46jq68C) | $10–15 | Needs an mac80211 monitor-capable chipset (e.g. RTL8812AU, AR9271). Onboard Pi WiFi usually can't sniff. |
| **WiFi/BLE sniffer node** | [ESP32-C3 SuperMini](https://amzn.to/4gODyHE) | $2–3 | Cheapest sniffer; one radio per board (WiFi *or* BLE). Onboard PCB antenna is often detuned on clones → shorter range; prefer u.FL/external antenna if coverage matters. Deploy several. |
| | [ESP32-WROOM-32 DevKitC](https://amzn.to/45GHggp) | $3–5 | Dual-core alternative, no real advantage for sniffing. |
| **Vehicle sensor node** | GY-271 (QMC5883L) magnetometer + any ESP32 above | $2–3 | I2C module; detects the magnetic signature of vehicles within ~2–5 m, no phone required. |
| **Vibration sensor node** | Piezo disc (27 mm) + 1 MΩ resistor + any ESP32 above | <$1 | Glued to a door/gate/fence; classifies knock vs sustained shaking on-device, ignores wind. |
| **Contact sensors** | Reed switch, PIR (AM312), IR beam-break, float switch | $1–8 | Wire straight to a Meshtastic node's GPIO — no custom firmware. |
| **Off-grid / LoRa node** | [Heltec WiFi LoRa 32 V3](https://amzn.to/4gQIko0) | $12–18 | Only for sensors/alerts beyond WiFi range. Runs Meshtastic (or MeshCore/Reticulum stacks). |
| | 868/915 MHz antenna | $2–5 | Match your region's ISM band; never power a LoRa board without one. |
| **Reticulum relay host** | Pi Zero W + RNode | $15 + RNode | Runs `sensors/rns_field_relay.py` for the LXMF backhaul path. |
| **Power (per remote node)** | [18650 cell + holder](https://amzn.to/4c9de8z), or USB PSU | $5–15 | Solar + LiPo for true off-grid; a phone charger indoors. |

## Build tiers

- **Minimum viable tripwire** — a Pi with built-in BLE running the base
  scanner. Zero extra hardware, real BLE MACs, limited to the Pi's radio
  range.
- **Property coverage** — add ESP32-C3 sniffers around the buildings, a
  GY-271 at the driveway, piezo discs on gates and fence runs, reed switches
  on doors.
- **Off-grid perimeter** — Heltec V3 nodes carry far sensors over LoRa;
  RelayFabric carries alerts out the same way.

Reality check: phone MAC randomization means every tier detects *presence*,
not *identity* — buy for coverage (more cheap nodes), not for a fancier
single node.
