# Agent Notes

## Local camera stack

- The camera monitor is a self-contained Docker Compose stack on this machine.
- Keep camera inventory in ignored `tools/camera_monitor.local.json`; use `tools/camera_monitor.example.json` as the public template.
- Keep Google/Nest credentials in ignored `tools/camera_monitor.docker.local.env` and Eufy credentials in ignored `tools/eufy-security.local.env`. Never print, paste, or commit them.
- Eufy control runs through the stack's `eufy-security-ws` service. Nest signaling and all browser media run through the stack's private `go2rtc` service.
- Keep provider control and recovery local to the three Compose services.
- Publish `cameras.local` only from this machine with `tools/publish_camera_mdns.sh`.
- After camera-monitor changes, run `make camera-monitor-docker` and verify live frames, container CPU, memory, and go2rtc consumer counts.
