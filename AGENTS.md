# Agent Notes

## Camera stack

- The production camera monitor runs on the Home Assistant box as the local `brightwater_camera_monitor` and `brightwater_mdns_alias` Docker add-ons in `home-assistant-addons/`.
- Keep camera inventory in ignored `tools/camera_monitor.local.json`; use `tools/camera_monitor.example.json` as the public template.
- Keep Google/Nest credentials in ignored `tools/camera_monitor.docker.local.env`, deployment settings in ignored `tools/deploy.local.env`, and Eufy credentials in ignored `tools/eufy-security.local.env`. Never print, paste, or commit them.
- Home Assistant's existing `eufy-security-ws` add-on is the sole Eufy account and P2P session owner for both cameras and floodlights. Never start the Compose `standalone-eufy` profile for this account.
- The monitor shares Home Assistant's existing `go2rtc` add-on. Monitor-owned Eufy streams use `camera_eufy_` names; never overwrite Home Assistant's serial-number stream names.
- The production profile allows one Eufy viewer slot and targets a five-minute thumbnail refresh to keep station command pressure, CPU, and memory low.
- Publish `cameras.local` only from Home Assistant's `brightwater_mdns_alias` add-on. The laptop LaunchAgent and Compose stack are fallback/development tools and must remain stopped during production.
- Deploy camera-monitor changes with `make camera-monitor-ha-deploy`, then verify all ten live frames, all four Eufy floodlight controls, add-on CPU/memory, Eufy error logs, and go2rtc producer/consumer counts.

## Website (website-fable/)

- `src/data/photos.json` and `src/data/trail.json` are generated (and gitignored) — the site reads them, not the sources. After editing sculpture frontmatter (GPS, `map:`), `MAP_COORDS` in `scripts/catalog.mjs`, or the photos tree, run `make catalog` in `website-fable/` to regenerate.
- Trail minimap x/y positions are hand-tuned in `MAP_COORDS` (frontmatter `map: {x,y}` overrides); place stops using both latitude and longitude relative to neighboring stops, not just path order.
