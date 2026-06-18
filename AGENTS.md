# Agent Notes

## Local Home Assistant

- Home Assistant is set up and reachable from this machine.
- SSH access is configured for routine Home Assistant work.
- A Home Assistant API token is available from the local shell environment when needed. Do not print it, paste it, or commit it.
- Use the existing local SSH key `~/.ssh/id_ed25519` for Home Assistant SSH access.
- Keep Home Assistant operational details local-only unless the user asks otherwise.
- Keep camera inventory in ignored `tools/camera_monitor.local.json`; use `tools/camera_monitor.example.json` as the public template.
- Keep deploy host/address details in ignored `tools/deploy.local.env`; use `tools/deploy.example.env` as the public template.
- Run `make deploy` after changing the camera monitor, its Home Assistant add-on wrapper, or the local mDNS alias. The deploy target syncs local add-on files over SSH, rebuilds/restarts `local_brightwater_camera_monitor` and `local_brightwater_mdns_alias`, publishes the configured mDNS alias, and keeps the Home Assistant token out of the repo.
