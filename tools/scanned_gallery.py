#!/usr/bin/env python3
"""Serve a local auto-updating gallery for scanned stained-glass photos."""

from __future__ import annotations

import argparse
import html
import json
import mimetypes
import os
import re
import selectors
import shutil
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCANNED_ROOT = ROOT / "photos" / "scanned"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
STAMP_RE = re.compile(r"(?P<stamp>\d{8}_\d{6})")
DEFAULT_SCAN_SECONDS = 55.0
SCAN_LOCK = threading.Lock()
SCAN_STATE: dict[str, object] = {
    "running": False,
    "stage": "idle",
    "progress": 0,
    "message": "Ready",
    "folder": None,
    "outputs": [],
    "error": None,
    "startedAt": None,
    "startedEpoch": None,
    "finishedAt": None,
    "finishedEpoch": None,
    "estimatedSeconds": DEFAULT_SCAN_SECONDS,
    "elapsedSeconds": 0,
    "remainingSeconds": None,
    "lastLine": None,
}
SCAN_DURATIONS: list[float] = []


def title_from_slug(slug: str) -> str:
    return slug.replace("_", " ").strip().title()


def normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def aliases_for_slug(slug: str) -> list[str]:
    title = title_from_slug(slug)
    base = re.sub(r"^sculpture_\d+_", "", slug)
    base = re.sub(r"^torch_\d+_", "", base)
    terms = {
        slug,
        title,
        slug.replace("_", " "),
        base,
        base.replace("_", " "),
    }
    parts = [part for part in slug.split("_") if part and not part.isdigit()]
    for part in parts:
        if part not in {"sculpture", "torch"}:
            terms.add(part)
    if "land" in parts and "bridge" in parts:
        terms.add("land bridge")
        terms.add("bridge")
    custom_aliases = {
        "sculpture_08_torch_3_land_bridge": {"torch 3", "third torch", "land bridge torch"},
        "sculpture_09_torch_4_shed": {"torch", "torch 4", "shed torch"},
        "sculpture_10_torch_5_tulip": {"torch 5", "tulip torch"},
        "sculpture_12_four_stages_of_evolution": {"four stages", "four stages general", "evolution", "evolution general"},
        "sculpture_12_four_stages_of_evolution_1": {"four stages 1", "evolution 1", "stage 1"},
        "sculpture_13_four_stages_of_evolution_2": {"four stages 2", "evolution 2", "stage 2"},
        "sculpture_13_four_stages_of_evolution_3": {"four stages 3", "evolution 3", "stage 3"},
        "sculpture_13_four_stages_of_evolution_4": {"four stages 4", "evolution 4", "stage 4"},
    }
    terms.update(custom_aliases.get(slug, set()))
    return sorted({normalize_text(term) for term in terms if normalize_text(term)})


def folder_options(scanned_root: Path) -> list[dict[str, object]]:
    options: list[dict[str, object]] = []
    if not scanned_root.exists():
        return options
    for path in sorted(scanned_root.iterdir()):
        if not path.is_dir() or path.name.startswith("_") or path.name.startswith("."):
            continue
        base = re.sub(r"^sculpture_\d+_", "", path.name)
        base = re.sub(r"^torch_\d+_", "", base)
        options.append(
            {
                "slug": path.name,
                "title": title_from_slug(path.name),
                "shortName": title_from_slug(base),
                "aliases": aliases_for_slug(path.name),
            }
        )
    return options


def resolve_folder(scanned_root: Path, query: str) -> dict[str, object] | None:
    needle = normalize_text(query)
    if not needle:
        return None
    scored: list[tuple[int, int, dict[str, object]]] = []
    for option in folder_options(scanned_root):
        aliases = [str(alias) for alias in option["aliases"]]
        best = 0
        for alias in aliases:
            words = alias.split()
            if needle == alias:
                best = max(best, 100)
            elif alias.startswith(needle):
                best = max(best, 90)
            elif any(word.startswith(needle) for word in words):
                best = max(best, 80)
            elif needle in alias:
                best = max(best, 70)
        if best:
            scored.append((best, -len(str(option["slug"])), option))
    if not scored:
        return None
    scored.sort(key=lambda item: item[:2], reverse=True)
    return scored[0][2]


def parsed_stamp(path: Path) -> datetime | None:
    match = STAMP_RE.search(path.name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group("stamp"), "%Y%m%d_%H%M%S")
    except ValueError:
        return None


def scanned_images(scanned_root: Path) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    if not scanned_root.exists():
        return items
    for path in scanned_root.rglob("*"):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        try:
            rel = path.relative_to(scanned_root)
        except ValueError:
            continue
        if any(part.startswith("_") for part in rel.parts):
            continue
        stat = path.stat()
        scan_dt = parsed_stamp(path)
        folder_slug = rel.parts[0] if len(rel.parts) > 1 else "scanned"
        sort_ts = scan_dt.timestamp() if scan_dt else stat.st_mtime
        rel_url = quote(rel.as_posix())
        items.append(
            {
                "path": rel.as_posix(),
                "src": f"/image/{rel_url}?v={stat.st_mtime_ns}",
                "filename": path.name,
                "folder": folder_slug,
                "folderTitle": title_from_slug(folder_slug),
                "bytes": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                "scanTime": scan_dt.isoformat(timespec="seconds") if scan_dt else None,
                "sortTs": sort_ts,
            }
        )
    items.sort(key=lambda item: (float(item["sortTs"]), str(item["path"])), reverse=True)
    return items


def set_scan_state(**updates: object) -> None:
    with SCAN_LOCK:
        SCAN_STATE.update(updates)


def estimated_scan_seconds() -> float:
    if SCAN_DURATIONS:
        return max(15.0, sum(SCAN_DURATIONS[-8:]) / len(SCAN_DURATIONS[-8:]))
    return DEFAULT_SCAN_SECONDS


def record_scan_duration(seconds: float) -> None:
    if seconds > 0:
        SCAN_DURATIONS.append(seconds)
        del SCAN_DURATIONS[:-12]


def get_scan_state() -> dict[str, object]:
    with SCAN_LOCK:
        state = dict(SCAN_STATE)
    started_epoch = state.get("startedEpoch")
    finished_epoch = state.get("finishedEpoch")
    now = time.time()
    if isinstance(started_epoch, (int, float)):
        end_epoch = finished_epoch if isinstance(finished_epoch, (int, float)) else now
        elapsed = max(0.0, float(end_epoch) - float(started_epoch))
    else:
        elapsed = 0.0
    estimated = float(state.get("estimatedSeconds") or estimated_scan_seconds())
    running = bool(state.get("running"))
    progress = float(state.get("progress") or 0)
    if running and estimated > 0:
        timed_progress = min(96.0, (elapsed / estimated) * 100.0)
        progress = max(progress, timed_progress)
    remaining = max(0.0, estimated - elapsed) if running and estimated > 0 else None
    state["progress"] = round(progress, 1)
    state["elapsedSeconds"] = round(elapsed, 1)
    state["remainingSeconds"] = round(remaining, 1) if remaining is not None else None
    state["estimatedSeconds"] = round(estimated, 1)
    return state


def scan_progress_for_line(line: str) -> tuple[str | None, float | None, str | None]:
    if line.startswith("[1/3]"):
        return "scan", 5.0, "Scanning"
    if "scan=0" in line:
        return "scan", 12.0, "Scanner moving"
    if "imageData[1].copy=" in line:
        return "scan", 78.0, "Scanner image received"
    if line.startswith("[2/3]"):
        return "crop", 82.0, "Cropping"
    if "/photos/_staging/" in line and Path(line).suffix.lower() in IMAGE_SUFFIXES:
        return "crop", 90.0, "Crop ready"
    if line.startswith("[3/3]"):
        return "file", 94.0, "Filing"
    if line.startswith("Done."):
        return "done", 100.0, "Done"
    return None, None, None


def parse_crop_paths(output: str) -> list[Path]:
    crops: list[Path] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        path = Path(line)
        if path.is_absolute() and "/photos/_staging/" in line and path.suffix.lower() in IMAGE_SUFFIXES:
            crops.append(path)
    return crops


def scan_worker(scanned_root: Path, folder_slug: str, folder_title: str) -> None:
    started = datetime.now()
    started_epoch = time.time()
    estimate = estimated_scan_seconds()
    set_scan_state(
        running=True,
        stage="scan",
        progress=3,
        message=f"Starting scan into {folder_title}",
        folder=folder_slug,
        outputs=[],
        error=None,
        startedAt=started.isoformat(timespec="seconds"),
        startedEpoch=started_epoch,
        finishedAt=None,
        finishedEpoch=None,
        estimatedSeconds=estimate,
        elapsedSeconds=0,
        remainingSeconds=estimate,
        lastLine=None,
    )
    try:
        env = os.environ.copy()
        env.setdefault("SCAN_BACKEND", "epson2")
        env.setdefault("SCAN_ROTATE", "270")
        process = subprocess.Popen(
            [str(ROOT / "digitize.sh"), "--no-tag"],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
        )
        if process.stdout is None:
            raise RuntimeError("scan process did not expose stdout")
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        output_lines: list[str] = []
        deadline = time.time() + 360
        while True:
            if time.time() > deadline and process.poll() is None:
                process.kill()
                raise TimeoutError("scan timed out after 360 seconds")
            for key, _ in selector.select(timeout=0.5):
                line = key.fileobj.readline()
                if not line:
                    continue
                line = line.rstrip("\n")
                output_lines.append(line)
                stage, progress, message = scan_progress_for_line(line.strip())
                updates: dict[str, object] = {"lastLine": line[-220:]}
                if stage:
                    updates["stage"] = stage
                if progress is not None:
                    updates["progress"] = progress
                if message:
                    updates["message"] = f"{message} into {folder_title}"
                set_scan_state(**updates)
            if process.poll() is not None:
                for line in process.stdout:
                    line = line.rstrip("\n")
                    output_lines.append(line)
                    stage, progress, message = scan_progress_for_line(line.strip())
                    updates = {"lastLine": line[-220:]}
                    if stage:
                        updates["stage"] = stage
                    if progress is not None:
                        updates["progress"] = progress
                    if message:
                        updates["message"] = f"{message} into {folder_title}"
                    set_scan_state(**updates)
                break
        result_code = process.wait()
        combined_output = "\n".join(output_lines)
        if result_code != 0:
            tail = "\n".join(combined_output.splitlines()[-12:])
            raise RuntimeError(f"scan failed with exit {result_code}\n{tail}")
        crops = parse_crop_paths(combined_output)
        if not crops:
            raise RuntimeError("scan finished, but no cropped photo path was found")
        dest_dir = scanned_root / folder_slug
        dest_dir.mkdir(parents=True, exist_ok=True)
        outputs: list[str] = []
        set_scan_state(stage="file", progress=96, message=f"Filing into {folder_title}")
        for index, crop in enumerate(crops, 1):
            stamp_match = STAMP_RE.search(crop.name)
            stamp = stamp_match.group("stamp") if stamp_match else started.strftime("%Y%m%d_%H%M%S")
            dest = dest_dir / f"{stamp}_flatbed_{index:03d}.jpg"
            shutil.copy2(crop, dest)
            outputs.append(dest.relative_to(scanned_root).as_posix())
        finished_epoch = time.time()
        duration = finished_epoch - started_epoch
        record_scan_duration(duration)
        set_scan_state(
            running=False,
            stage="done",
            progress=100,
            message=f"Done in {duration:.0f}s: {len(outputs)} photo{'s' if len(outputs) != 1 else ''} filed in {folder_title}",
            outputs=outputs,
            error=None,
            finishedAt=datetime.now().isoformat(timespec="seconds"),
            finishedEpoch=finished_epoch,
            elapsedSeconds=duration,
            remainingSeconds=None,
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to local-only UI
        finished_epoch = time.time()
        set_scan_state(
            running=False,
            stage="error",
            progress=100,
            message="Scan failed",
            outputs=[],
            error=f"{exc}\n{traceback.format_exc(limit=3)}",
            finishedAt=datetime.now().isoformat(timespec="seconds"),
            finishedEpoch=finished_epoch,
            elapsedSeconds=max(0.0, finished_epoch - started_epoch),
            remainingSeconds=None,
        )


def start_scan(scanned_root: Path, folder_slug: str, folder_title: str) -> bool:
    with SCAN_LOCK:
        if bool(SCAN_STATE.get("running")):
            return False
        SCAN_STATE.update(
            {
                "running": True,
                "stage": "queued",
                "progress": 1,
                "message": f"Queued scan for {folder_title}",
                "folder": folder_slug,
                "outputs": [],
                "error": None,
                "startedAt": datetime.now().isoformat(timespec="seconds"),
                "startedEpoch": time.time(),
                "finishedAt": None,
                "finishedEpoch": None,
                "estimatedSeconds": estimated_scan_seconds(),
                "elapsedSeconds": 0,
                "remainingSeconds": estimated_scan_seconds(),
                "lastLine": None,
            }
        )
    thread = threading.Thread(target=scan_worker, args=(scanned_root, folder_slug, folder_title), daemon=True)
    thread.start()
    return True


def page_html() -> bytes:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Capture</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f7f4;
      --ink: #1e1f1d;
      --muted: #6a6d67;
      --line: #d8d8d0;
      --panel: #ffffff;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    header {
      position: sticky;
      top: 0;
      z-index: 5;
      padding: 16px 24px;
      background: rgba(247, 247, 244, 0.94);
      border-bottom: 1px solid var(--line);
      backdrop-filter: blur(8px);
    }
    .topRow {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 20px;
      margin-bottom: 12px;
    }
    h1 {
      margin: 0;
      font-size: 24px;
      font-weight: 650;
      letter-spacing: 0;
    }
    #status {
      color: var(--muted);
      font-size: 14px;
      white-space: nowrap;
    }
    .scanBar {
      display: grid;
      grid-template-columns: minmax(220px, 520px) minmax(160px, 260px);
      gap: 12px;
      align-items: stretch;
    }
    .folderBox {
      position: relative;
    }
    label {
      display: block;
      margin: 0 0 5px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 650;
    }
    #folderInput {
      width: 100%;
      height: 58px;
      padding: 0 16px;
      border: 1px solid #b9bbb2;
      border-radius: 8px;
      background: var(--panel);
      color: var(--ink);
      font: inherit;
      font-size: 24px;
      letter-spacing: 0;
    }
    #folderHint {
      min-height: 20px;
      margin-top: 5px;
      color: var(--muted);
      font-size: 13px;
    }
    #scanButton {
      align-self: end;
      height: 58px;
      border: 0;
      border-radius: 8px;
      background: #1f6f4a;
      color: white;
      cursor: pointer;
      font: inherit;
      font-size: 24px;
      font-weight: 750;
      letter-spacing: 0;
    }
    #scanButton:disabled {
      background: #8b928b;
      cursor: not-allowed;
    }
    #scanStatus {
      margin-top: 8px;
      color: var(--muted);
      font-size: 14px;
      white-space: pre-wrap;
    }
    .progressWrap {
      margin-top: 10px;
      max-width: 792px;
    }
    #progressTrack {
      width: 100%;
      height: 18px;
      overflow: hidden;
      border: 1px solid #b9bbb2;
      border-radius: 999px;
      background: #e8e8e1;
    }
    #progressFill {
      width: 0%;
      height: 100%;
      background: #1f6f4a;
      transition: width 260ms ease;
    }
    #progressMeta {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      margin-top: 5px;
      color: var(--muted);
      font-size: 13px;
    }
    main {
      width: 100%;
      margin: 0 auto;
      padding: 18px 24px 48px;
    }
    .empty {
      color: var(--muted);
      padding: 32px 0;
      font-size: 16px;
    }
    .capture {
      display: inline-block;
      width: min(500px, 100%);
      margin: 0 14px 22px 0;
      vertical-align: top;
    }
    .meta {
      margin: 0 0 6px;
    }
    .folder {
      margin: 0;
      font-size: 15px;
      font-weight: 650;
      letter-spacing: 0;
    }
    .details {
      color: var(--muted);
      font-size: 13px;
      overflow-wrap: anywhere;
      text-align: left;
    }
    .actions {
      display: flex;
      gap: 6px;
      margin: 6px 0 8px;
    }
    .actions button {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel);
      color: var(--ink);
      cursor: pointer;
      font: inherit;
      font-size: 12px;
      padding: 4px 8px;
    }
    img {
      display: block;
      width: min(100%, 500px);
      height: auto;
      background: var(--panel);
      border: 1px solid var(--line);
    }
    @media (max-width: 700px) {
      header {
        padding: 12px 14px;
      }
      .topRow {
        align-items: flex-start;
        flex-direction: column;
        gap: 4px;
      }
      .scanBar {
        grid-template-columns: 1fr;
      }
      main {
        padding: 12px 12px 36px;
      }
    }
  </style>
</head>
<body>
  <header>
    <div class="topRow">
      <h1>Capture</h1>
      <div id="status">Loading</div>
    </div>
    <form id="scanForm" class="scanBar">
      <div class="folderBox">
        <label for="folderInput">Folder</label>
        <input id="folderInput" list="folderOptions" autocomplete="off" placeholder="shed, porch, tulip, gun">
        <datalist id="folderOptions"></datalist>
        <div id="folderHint">Type a folder shortcut, then scan.</div>
      </div>
      <button id="scanButton" type="submit">Scan</button>
    </form>
    <div id="scanStatus">Ready</div>
    <div class="progressWrap" aria-live="polite">
      <div id="progressTrack" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">
        <div id="progressFill"></div>
      </div>
      <div id="progressMeta">
        <span id="progressStage">Ready</span>
        <span id="progressTime">0s</span>
      </div>
    </div>
  </header>
  <main id="captures"></main>
  <script>
    const captures = document.getElementById("captures");
    const status = document.getElementById("status");
    const scanForm = document.getElementById("scanForm");
    const scanButton = document.getElementById("scanButton");
    const scanStatus = document.getElementById("scanStatus");
    const progressTrack = document.getElementById("progressTrack");
    const progressFill = document.getElementById("progressFill");
    const progressStage = document.getElementById("progressStage");
    const progressTime = document.getElementById("progressTime");
    const folderInput = document.getElementById("folderInput");
    const folderHint = document.getElementById("folderHint");
    const folderOptions = document.getElementById("folderOptions");
    let lastSignature = "";
    let folders = [];

    function formatTime(value) {
      if (!value) return "";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return "";
      return date.toLocaleString([], {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit"
      });
    }

    function normalize(value) {
      return (value || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
    }

    function formatDuration(seconds) {
      if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) return "";
      const total = Math.max(0, Math.round(Number(seconds)));
      const mins = Math.floor(total / 60);
      const secs = total % 60;
      return mins ? `${mins}m ${String(secs).padStart(2, "0")}s` : `${secs}s`;
    }

    function preferredAlias(option) {
      const aliases = option.aliases || [];
      const shortName = normalize(option.shortName || "");
      if (shortName) return shortName;
      for (const alias of aliases) {
        if (!alias.includes("sculpture") && alias.length <= 18) return alias;
      }
      return option.title || option.slug;
    }

    function bestFolder(query) {
      const needle = normalize(query);
      if (!needle) return null;
      const candidates = [];
      for (const option of folders) {
        let score = 0;
        for (const alias of option.aliases || []) {
          const words = alias.split(" ");
          if (needle === alias) score = Math.max(score, 100);
          else if (alias.startsWith(needle)) score = Math.max(score, 90);
          else if (words.some(word => word.startsWith(needle))) score = Math.max(score, 80);
          else if (alias.includes(needle)) score = Math.max(score, 70);
        }
        if (score) candidates.push({ score, option });
      }
      candidates.sort((a, b) => b.score - a.score || a.option.slug.localeCompare(b.option.slug));
      return candidates.length ? candidates[0].option : null;
    }

    function updateFolderHint() {
      const match = bestFolder(folderInput.value);
      if (!folderInput.value.trim()) {
        folderHint.textContent = "Type a folder shortcut, then scan.";
      } else if (match) {
        folderHint.textContent = `${preferredAlias(match)} -> ${match.title}`;
      } else {
        folderHint.textContent = "No matching folder";
      }
    }

    async function loadFolders() {
      const response = await fetch("/api/folders", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      folders = (await response.json()).folders || [];
      folderOptions.textContent = "";
      for (const option of folders) {
        const opt = document.createElement("option");
        opt.value = preferredAlias(option);
        opt.label = option.title;
        folderOptions.append(opt);
      }
      updateFolderHint();
    }

    async function refreshScanStatus() {
      try {
        const response = await fetch("/api/scan/status", { cache: "no-store" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const state = await response.json();
        const running = Boolean(state.running);
        const done = !running && state.stage === "done";
        const outputs = state.outputs || [];
        scanButton.disabled = running;
        if (state.error) {
          scanStatus.textContent = `${state.message}\\n${state.error}`;
        } else if (done) {
          const last = outputs.length ? outputs[outputs.length - 1].split("/").pop() : "";
          scanStatus.textContent = last
            ? `Ready for next scan. Last filed: ${last}`
            : "Ready for next scan.";
        } else {
          scanStatus.textContent = state.message || "Ready";
        }
        const visibleProgress = done && !state.error ? 0 : Number(state.progress || 0);
        const progress = Math.max(0, Math.min(100, visibleProgress));
        progressFill.style.width = `${progress}%`;
        progressTrack.setAttribute("aria-valuenow", String(Math.round(progress)));
        const stage = state.stage ? String(state.stage).replace(/^./, c => c.toUpperCase()) : "Ready";
        progressStage.textContent = done ? "Ready for next scan / 0%" : `${stage} / ${Math.round(progress)}%`;
        if (running) {
          const elapsed = formatDuration(state.elapsedSeconds);
          const remaining = formatDuration(state.remainingSeconds);
          progressTime.textContent = remaining ? `${elapsed} elapsed / about ${remaining} left` : `${elapsed} elapsed`;
        } else if (done && state.elapsedSeconds) {
          progressTime.textContent = `Last scan took ${formatDuration(state.elapsedSeconds)}`;
        } else if (state.elapsedSeconds) {
          progressTime.textContent = `${formatDuration(state.elapsedSeconds)} total`;
        } else {
          progressTime.textContent = "0s";
        }
      } catch (error) {
        scanStatus.textContent = "Scan controls offline";
        progressStage.textContent = "Offline";
      }
    }

    async function rotatePhoto(path, degrees) {
      scanStatus.textContent = "Rotating";
      const response = await fetch("/api/rotate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, degrees })
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        scanStatus.textContent = payload.error || `Rotate failed: HTTP ${response.status}`;
        return;
      }
      scanStatus.textContent = "Rotated";
      lastSignature = "";
      await refresh();
    }

    function render(items) {
      const signature = JSON.stringify(items.map(item => [item.path, item.src]));
      if (signature === lastSignature) return;
      lastSignature = signature;
      captures.textContent = "";
      if (!items.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = "No scanned photos";
        captures.append(empty);
        return;
      }
      for (const item of items) {
        const section = document.createElement("section");
        section.className = "capture";

        const meta = document.createElement("div");
        meta.className = "meta";

        const title = document.createElement("h2");
        title.className = "folder";
        title.textContent = item.folderTitle;

        const details = document.createElement("div");
        details.className = "details";
        const taken = formatTime(item.scanTime || item.mtime);
        details.textContent = taken ? `${taken} / ${item.filename}` : item.filename;

        const actions = document.createElement("div");
        actions.className = "actions";
        const left = document.createElement("button");
        left.type = "button";
        left.textContent = "Rotate Left";
        left.addEventListener("click", () => rotatePhoto(item.path, 270));
        const right = document.createElement("button");
        right.type = "button";
        right.textContent = "Rotate Right";
        right.addEventListener("click", () => rotatePhoto(item.path, 90));
        actions.append(left, right);

        const img = document.createElement("img");
        img.src = item.src;
        img.alt = `${item.folderTitle} - ${item.filename}`;
        img.loading = "eager";
        img.decoding = "async";

        meta.append(title, details, actions);
        section.append(meta, img);
        captures.append(section);
      }
    }

    async function refresh() {
      try {
        const response = await fetch("/api/photos", { cache: "no-store" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = await response.json();
        render(payload.photos || []);
        const now = new Date();
        status.textContent = `${(payload.photos || []).length} photos / ${now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" })}`;
      } catch (error) {
        status.textContent = "Offline";
      }
    }

    scanForm.addEventListener("submit", async event => {
      event.preventDefault();
      const match = bestFolder(folderInput.value);
      if (!match) {
        scanStatus.textContent = "Pick a folder first.";
        folderInput.focus();
        return;
      }
      folderInput.value = preferredAlias(match);
      updateFolderHint();
      scanStatus.textContent = `Starting scan into ${match.title}`;
      scanButton.disabled = true;
      const response = await fetch("/api/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ folder: match.slug })
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        scanStatus.textContent = payload.error || `Scan failed to start: HTTP ${response.status}`;
        scanButton.disabled = false;
        return;
      }
      await refreshScanStatus();
    });

    folderInput.addEventListener("input", updateFolderHint);
    folderInput.addEventListener("focus", () => {
      loadFolders().catch(() => {
        folderHint.textContent = "Could not load folders";
      });
      folderInput.select();
    });
    folderInput.addEventListener("click", () => folderInput.select());
    folderInput.addEventListener("keydown", event => {
      if (event.key !== "Tab") return;
      const match = bestFolder(folderInput.value);
      if (!match) return;
      event.preventDefault();
      folderInput.value = preferredAlias(match);
      updateFolderHint();
    });

    loadFolders().catch(() => {
      folderHint.textContent = "Could not load folders";
    });
    refresh();
    refreshScanStatus();
    setInterval(refresh, 2000);
    setInterval(refreshScanStatus, 2000);
  </script>
</body>
</html>
""".encode("utf-8")


class GalleryHandler(BaseHTTPRequestHandler):
    scanned_root: Path

    def log_message(self, fmt: str, *args: object) -> None:
        if self.path == "/api/photos":
            return
        print(f"{self.address_string()} - {fmt % args}", file=sys.stderr)

    def send_bytes(self, body: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: object) -> None:
        self.send_bytes(json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def read_json_body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            self.send_bytes(page_html(), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/photos":
            self.send_json({"photos": scanned_images(self.scanned_root)})
            return
        if parsed.path == "/api/folders":
            self.send_json({"folders": folder_options(self.scanned_root)})
            return
        if parsed.path == "/api/scan/status":
            self.send_json(get_scan_state())
            return
        if parsed.path.startswith("/image/"):
            self.serve_image(parsed.path.removeprefix("/image/"), parsed.query)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/scan":
                self.start_scan_from_request()
                return
            if parsed.path == "/api/rotate":
                self.rotate_from_request()
                return
        except ValueError as exc:
            self.send_bytes(
                json.dumps({"error": str(exc)}).encode("utf-8"),
                "application/json; charset=utf-8",
                HTTPStatus.BAD_REQUEST,
            )
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def start_scan_from_request(self) -> None:
        payload = self.read_json_body()
        requested = str(payload.get("folder") or "")
        option = resolve_folder(self.scanned_root, requested)
        if not option:
            self.send_bytes(
                json.dumps({"error": f"unknown folder: {requested}"}).encode("utf-8"),
                "application/json; charset=utf-8",
                HTTPStatus.BAD_REQUEST,
            )
            return
        started = start_scan(self.scanned_root, str(option["slug"]), str(option["title"]))
        if not started:
            self.send_bytes(
                json.dumps({"error": "scan already running", **get_scan_state()}).encode("utf-8"),
                "application/json; charset=utf-8",
                HTTPStatus.CONFLICT,
            )
            return
        self.send_json({"ok": True, **get_scan_state()})

    def rotate_from_request(self) -> None:
        payload = self.read_json_body()
        rel = Path(str(payload.get("path") or ""))
        degrees = int(payload.get("degrees") or 0)
        if degrees % 360 not in {90, 180, 270}:
            raise ValueError("degrees must be 90, 180, or 270")
        target = (self.scanned_root / rel).resolve()
        try:
            target.relative_to(self.scanned_root)
        except ValueError as exc:
            raise ValueError("invalid image path") from exc
        if not target.is_file() or target.suffix.lower() not in IMAGE_SUFFIXES:
            raise ValueError("image not found")
        with Image.open(target) as im:
            im = ImageOps.exif_transpose(im)
            icc = im.info.get("icc_profile")
            rotated = im.rotate(-degrees, expand=True)
            save_kwargs: dict[str, object] = {"quality": 95}
            if icc:
                save_kwargs["icc_profile"] = icc
            temp = target.with_name(f".{target.name}.tmp")
            rotated.save(temp, "JPEG", **save_kwargs)
        temp.replace(target)
        self.send_json({"ok": True, "path": rel.as_posix()})

    def serve_image(self, encoded_rel: str, query: str) -> None:
        # Preserve literal slashes while decoding escaped path characters.
        rel = Path(unquote(encoded_rel))
        target = (self.scanned_root / rel).resolve()
        try:
            target.relative_to(self.scanned_root)
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not target.is_file() or target.suffix.lower() not in IMAGE_SUFFIXES:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(target.stat().st_size))
        self.send_header("Cache-Control", "public, max-age=31536000, immutable" if parse_qs(query).get("v") else "no-store")
        self.end_headers()
        with target.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                self.wfile.write(chunk)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_SCANNED_ROOT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    scanned_root = args.root.resolve()
    scanned_root.mkdir(parents=True, exist_ok=True)
    handler = type("ConfiguredGalleryHandler", (GalleryHandler,), {"scanned_root": scanned_root})
    server = ThreadingHTTPServer((args.host, args.port), handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Capture serving {html.escape(str(scanned_root))} at {url}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
