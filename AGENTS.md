# Agent Notes

## Local camera stack

- The camera monitor is a self-contained Docker Compose stack on this machine.
- Keep camera inventory in ignored `tools/camera_monitor.local.json`; use `tools/camera_monitor.example.json` as the public template.
- Keep Google/Nest credentials in ignored `tools/camera_monitor.docker.local.env` and Eufy credentials in ignored `tools/eufy-security.local.env`. Never print, paste, or commit them.
- Home Assistant's `eufy-security-ws` add-on is the sole Eufy account and P2P session owner. The camera monitor connects outbound through the ignored `CAMERA_EUFY_WS_URL`; never start the Compose `standalone-eufy` profile for the same account.
- Nest signaling and all browser media run through the stack's private `go2rtc` service.
- Keep Nest control and recovery local to the Compose stack. Recover Eufy through Home Assistant's add-on so the laptop cannot disrupt cabin controls.
- Publish `cameras.local` only from this machine with `tools/publish_camera_mdns.sh`.
- After camera-monitor changes, run `make camera-monitor-docker` and verify live frames, container CPU, memory, and go2rtc consumer counts.
