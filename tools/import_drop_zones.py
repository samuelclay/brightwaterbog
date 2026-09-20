#!/usr/bin/env python3
"""Import photos and clips dropped into drop-zones/ into the site's photo tree.

Two steps, so nothing lands in the tree without a look first:

  plan   Scan drop-zones/<zone>/, hash-check every file against photos/, read
         EXIF (stills) or ffprobe (clips), and write drop-zones/_plan.json
         (git-ignored). Each entry says what the file is, whether it looks like
         a duplicate, and the proposed action. Edit the plan to change
         `action` (import | skip), `folder` (a photos/apple-photos-stained-glass/
         selected/ folder), or `era` (now | construction | drawings).

  apply  Carry out the plan: copy stills in as YYYYMMDD_HHMMSS_<stem>.jpeg,
         boomerang-encode clips to .mp4 with a poster .jpg, append manifest
         rows, add construction/drawings keys, then move the originals into
         drop-zones/_imported/<zone>/. Finish with `make catalog` in website/.

    .venv/bin/python tools/import_drop_zones.py plan
    .venv/bin/python tools/import_drop_zones.py apply [--dry-run]

Needs Pillow (the repo .venv has it) and ffmpeg/ffprobe on PATH. HEIC stills
are converted with macOS `sips` first.

Duplicate detection, in order of confidence:
  exact_duplicate     byte-identical to a file already under photos/
  probable_duplicate  a manifest row in the tree has the same capture second
                      (Photos re-exports of the same frame — rotated, cropped,
                      or re-encoded — hash differently but keep the timestamp)
  near_duplicate      64-bit dHash within NEAR_DHASH bits of an existing photo
                      or another new file; imported, but flagged for review

Timestamps: a still's EXIF DateTimeOriginal is already local wall-clock time
where it was shot (Eastern at the bog) and is used as-is — never mdls, which
re-reads it in the machine's zone. A clip's QuickTime creationdate carries its
offset; bare creation_time (app exports) is UTC and converted to America/New_York.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover
    sys.exit("Pillow is required: run with .venv/bin/python")

ROOT = Path(__file__).resolve().parent.parent
ZONES = ROOT / "drop-zones"
IMPORTED = ZONES / "_imported"
PLAN_PATH = ZONES / "_plan.json"
PHOTOS = ROOT / "photos"
SELECTED = PHOTOS / "apple-photos-stained-glass" / "selected"
KEY_PREFIX = "apple-photos-stained-glass/selected"
DATA = ROOT / "website" / "src" / "data"
ET = ZoneInfo("America/New_York")

STILL_EXT = {".jpg", ".jpeg", ".heic", ".png"}
CLIP_EXT = {".mov", ".mp4", ".m4v"}
IGNORE_NAMES = {".gitkeep", ".DS_Store", "README.txt"}
SPECIAL_ZONES = {"_unsorted", "_new_piece", "_scans_of_old_prints", "_drawings_and_cad"}
NEAR_DHASH = 8
MAX_SIDE = 1280
CATALOG_SOURCE = "drop_zone_import"


# ----------------------------------------------------------------- helpers --
def md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dhash(img: Image.Image, size: int = 8) -> int:
    g = img.convert("L").resize((size + 1, size), Image.LANCZOS)
    px = list(g.get_flattened_data()) if hasattr(g, "get_flattened_data") else list(g.getdata())
    bits = 0
    for r in range(size):
        for c in range(size):
            bits = (bits << 1) | (1 if px[r * (size + 1) + c] > px[r * (size + 1) + c + 1] else 0)
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def title_for(folder: str) -> str:
    return " ".join(w.capitalize() for w in folder.split("_"))


def short_stem(stem: str) -> str:
    # video_0_c692d4321a45403f825588c44f3855f5 -> video_0_c692d432
    return re.sub(r"([0-9a-f]{8})[0-9a-f]{24,}", r"\1", stem)


def load_manifest(folder: Path) -> list[dict]:
    p = folder / "_manifest.json"
    return json.loads(p.read_text()) if p.exists() else []


def save_manifest(folder: Path, rows: list[dict]) -> None:
    (folder / "_manifest.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n")


def parse_created(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


# ------------------------------------------------------------- metadata ----
def still_info(path: Path) -> dict:
    """EXIF capture time (local wall clock), GPS and display-oriented dims."""
    src = path
    if path.suffix.lower() == ".heic":
        tmp = Path(tempfile.mkdtemp()) / (path.stem + ".jpg")
        run(["sips", "-s", "format", "jpeg", str(path), "--out", str(tmp)])
        src = tmp
    im = Image.open(src)
    ex = im.getexif()
    ifd = ex.get_ifd(0x8769)
    gps = ex.get_ifd(0x8825)
    dt = ifd.get(0x9003) or ifd.get(0x9004) or ex.get(0x0132)
    created = dt.replace(":", "-", 2)[:19] if isinstance(dt, str) and len(dt) >= 19 else None
    date_source = "exif" if created else "mtime"
    if not created:
        created = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")

    def coord(v, ref):
        d, m, s = (float(x) for x in v)
        val = d + m / 60 + s / 3600
        return -val if ref in ("S", "W") else val

    lat = lon = None
    if gps and 2 in gps and 4 in gps:
        try:
            lat, lon = coord(gps[2], gps.get(1, "N")), coord(gps[4], gps.get(3, "E"))
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    oriented = ImageOps.exif_transpose(im)
    return {
        "kind": "still", "created": created, "date_source": date_source,
        "offset": ifd.get(0x9011), "latitude": lat, "longitude": lon,
        "width": oriented.width, "height": oriented.height,
        "model": (ex.get(0x0110) or "").strip() or None,
        "dhash": dhash(oriented), "converted_from": str(path) if src is not path else None,
        "_source": str(src),
    }


def clip_info(path: Path) -> dict:
    j = json.loads(run(["ffprobe", "-v", "quiet", "-print_format", "json",
                        "-show_format", "-show_streams", str(path)]).stdout)
    v = next(s for s in j["streams"] if s["codec_type"] == "video")
    tags = j["format"].get("tags", {})
    vtags = v.get("tags", {})
    rot = int(vtags.get("rotate", 0) or 0)
    for sd in v.get("side_data_list", []):
        if "rotation" in sd:
            rot = int(sd["rotation"])
    w, h = v["width"], v["height"]
    if rot % 180:
        w, h = h, w
    ct = tags.get("com.apple.quicktime.creationdate") or tags.get("creation_time")
    created = None
    date_source = "none"
    if ct:
        try:
            d = datetime.fromisoformat(ct.replace("Z", "+00:00"))
            created = (d.astimezone(ET) if d.tzinfo else d).strftime("%Y-%m-%d %H:%M:%S")
            date_source = "quicktime" if "com.apple.quicktime.creationdate" in tags else "creation_time_utc"
        except ValueError:
            pass
    if not created:
        created = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        date_source = "mtime"
    lat = lon = None
    m = re.match(r"([+-]\d+\.\d+)([+-]\d+\.\d+)", tags.get("com.apple.quicktime.location.ISO6709", ""))
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
    duration = float(j["format"]["duration"])
    with tempfile.TemporaryDirectory() as td:
        frame = Path(td) / "f.jpg"
        run(["ffmpeg", "-y", "-v", "quiet", "-ss", f"{duration / 3:.3f}", "-i", str(path),
             "-frames:v", "1", str(frame)])
        h_ = dhash(Image.open(frame))
    return {
        "kind": "clip", "created": created, "date_source": date_source,
        "latitude": lat, "longitude": lon, "width": w, "height": h, "rotation": rot,
        "duration": round(duration, 2),
        "has_audio": any(s["codec_type"] == "audio" for s in j["streams"]),
        "model": tags.get("com.apple.quicktime.model"), "dhash": h_,
    }


# ------------------------------------------------------------------ plan ----
def tree_index() -> tuple[dict[str, str], list[dict]]:
    """md5 -> key for every media file under photos/, plus every manifest row."""
    hashes: dict[str, str] = {}
    for p in PHOTOS.rglob("*"):
        if p.is_file() and p.suffix.lower() in STILL_EXT | CLIP_EXT:
            hashes[md5(p)] = str(p.relative_to(PHOTOS))
    rows: list[dict] = []
    for folder in sorted(SELECTED.iterdir()):
        if folder.is_dir():
            for r in load_manifest(folder):
                r = dict(r)
                r["_folder"] = folder.name
                r["_key"] = f"{KEY_PREFIX}/{folder.name}/{r['filename']}"
                rows.append(r)
    return hashes, rows


def existing_dhashes(folders: set[str]) -> list[tuple[str, int]]:
    out = []
    for name in sorted(folders):
        folder = SELECTED / name
        if not folder.is_dir():
            continue
        for r in load_manifest(folder):
            p = folder / r["filename"]
            if p.exists() and p.suffix.lower() in STILL_EXT:
                try:
                    out.append((f"{KEY_PREFIX}/{name}/{r['filename']}",
                                dhash(ImageOps.exif_transpose(Image.open(p)))))
                except OSError:
                    pass
    return out


def scan_zone_files(only_zone: str | None) -> list[tuple[str, str, Path]]:
    """(zone, era, path) for every droppable file. A construction/ subfolder
    inside a zone marks its files as construction shots."""
    found = []
    for zone in sorted(ZONES.iterdir()):
        if not zone.is_dir() or zone.name == "_imported":
            continue
        if only_zone and zone.name != only_zone:
            continue
        for p in sorted(zone.rglob("*")):
            if not p.is_file() or p.name in IGNORE_NAMES or p.name.startswith("."):
                continue
            if p.suffix.lower() not in STILL_EXT | CLIP_EXT:
                continue
            rel = p.relative_to(zone).parts
            era = "construction" if "construction" in rel[:-1] else "now"
            if "drawings" in rel[:-1]:
                era = "drawings"
            found.append((zone.name, era, p))
    return found


def cmd_plan(args: argparse.Namespace) -> None:
    files = scan_zone_files(args.zone)
    if not files:
        print("drop zones are empty")
        return
    print(f"hashing photo tree …", flush=True)
    hashes, rows = tree_index()
    entries = []
    for zone, era, p in files:
        print(f"  reading {zone}/{p.relative_to(ZONES / zone)}", flush=True)
        info = clip_info(p) if p.suffix.lower() in CLIP_EXT else still_info(p)
        info.pop("_source", None)
        e = {
            "zone": zone, "file": str(p.relative_to(ZONES / zone)), "bytes": p.stat().st_size,
            **info, "folder": zone, "era": era, "action": "import", "status": "new",
            "duplicate_of": None, "similar_to": [], "notes": [],
        }
        h = md5(p)
        if h in hashes:
            e.update(status="exact_duplicate", action="skip", duplicate_of=hashes[h])
        else:
            t = parse_created(e["created"])
            for r in rows:
                rt = parse_created(r.get("created"))
                if not t or not rt:
                    continue
                same_kind = bool(r.get("video")) == (e["kind"] == "clip")
                if same_kind and abs((rt - t).total_seconds()) <= 1:
                    e.update(status="probable_duplicate", action="skip", duplicate_of=r["_key"])
                    e["notes"].append("same capture second as an existing row (re-export)")
                    break
        if zone in SPECIAL_ZONES:
            e.update(folder=None)
            if e["action"] == "import":
                e.update(action="hold")
                e["notes"].append("set `folder` (and `era`) then change action to import")
        elif not (SELECTED / zone).is_dir():
            e["notes"].append(f"no selected/{zone} folder yet — apply creates it; add it to the page's modernFolders")
        entries.append(e)

    # near-duplicate flags: against existing photos in the target folders, and within the batch
    ex = existing_dhashes({e["folder"] for e in entries if e["folder"]})
    for e in entries:
        if e["action"] != "import":
            continue
        for key, h in ex:
            if key.split("/")[2] == e["folder"] and hamming(e["dhash"], h) <= NEAR_DHASH:
                e["similar_to"].append(key)
        for o in entries:
            if o is e or o["action"] != "import" or o["kind"] != e["kind"]:
                continue
            if hamming(e["dhash"], o["dhash"]) <= NEAR_DHASH:
                e["similar_to"].append(f"{o['zone']}/{o['file']}")
        if e["similar_to"] and e["status"] == "new":
            e["status"] = "near_duplicate"
    for e in entries:
        e.pop("dhash", None)

    plan = {
        "_readme": "Edit `action` (import|skip|hold), `folder` (a selected/ folder) and `era` "
                   "(now|construction|drawings), then run: tools/import_drop_zones.py apply",
        "generated": datetime.now().isoformat(timespec="seconds"),
        "entries": entries,
    }
    out = Path(args.out)
    out.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n")
    print_plan(entries)
    print(f"\nplan written to {out.relative_to(ROOT)}")


def print_plan(entries: list[dict]) -> None:
    counts: dict[str, int] = {}
    for e in entries:
        counts[f"{e['action']}/{e['status']}"] = counts.get(f"{e['action']}/{e['status']}", 0) + 1
    print()
    for k, v in sorted(counts.items()):
        print(f"  {v:3d}  {k}")
    print()
    for e in entries:
        tag = f"clip {e['duration']}s" if e["kind"] == "clip" else "still"
        extra = f" -> {e['duplicate_of']}" if e["duplicate_of"] else ""
        sim = f"  ~ {', '.join(e['similar_to'])}" if e["similar_to"] else ""
        print(f"{e['action']:<6} {e['status']:<18} {e['zone']}/{e['file']:<44} {e['created']}  {e['era']:<12} {tag}{extra}{sim}")


# ----------------------------------------------------------------- apply ----
def target_stem(e: dict, created: datetime) -> str:
    stem = short_stem(Path(e["file"]).stem)
    prefix = created.strftime("%Y%m%d_%H%M%S")
    return stem if re.fullmatch(r"\d{8}_\d{6}", stem) else f"{prefix}_{stem}"


def unique(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 100):
        cand = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not cand.exists():
            return cand
    raise RuntimeError(f"too many collisions for {path}")


def boomerang(src: Path, dst: Path) -> None:
    """Forward pass then the reverse minus its first frame, so `loop` ping-pongs.
    Scale before reverse (reverse buffers every frame in RAM); drop audio."""
    vf = (f"[0:v]scale='min({MAX_SIDE},iw)':'min({MAX_SIDE},ih)':force_original_aspect_ratio=decrease,"
          f"scale=trunc(iw/2)*2:trunc(ih/2)*2,split[a][b];"
          f"[b]reverse,trim=start_frame=1,setpts=PTS-STARTPTS[r];[a][r]concat=n=2:v=1[out]")
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-filter_complex", vf, "-map", "[out]",
         "-c:v", "libx264", "-profile:v", "high", "-crf", "26", "-preset", "slow",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(dst)])


def probe_duration(path: Path) -> float:
    return float(run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                      "-of", "csv=p=0", str(path)]).stdout.strip())


def base_row(e: dict, folder: str, created: datetime, filename: str, src: Path) -> dict:
    return {
        "created": created.strftime("%Y-%m-%d %H:%M:%S"),
        "filename": filename,
        "latitude": e.get("latitude"),
        "longitude": e.get("longitude"),
        "width": None, "height": None,
        "uti": "public.jpeg",
        "source_path": str(src),
        "source_note": f"drop-zone:{e['zone']}/{e['file']}",
        "source_kind": "original",
        "folder": folder,
        "folder_title": title_for(folder),
        "catalog_path": f"photos/{KEY_PREFIX}/{folder}/{filename}",
        "relative_path": f"{folder}/{filename}",
        "suffix": Path(filename).suffix,
        "bytes": None,
        "mtime": datetime.now().isoformat(timespec="seconds"),
        "catalog_source": CATALOG_SOURCE,
    }


def import_still(e: dict, folder: str, created: datetime, src: Path, dry: bool) -> dict:
    dest_dir = SELECTED / folder
    dest = unique(dest_dir / f"{target_stem(e, created)}.jpeg")
    if not dry:
        if src.suffix.lower() == ".heic":
            run(["sips", "-s", "format", "jpeg", str(src), "--out", str(dest)])
        else:
            shutil.copy2(src, dest)
    row = base_row(e, folder, created, dest.name, src)
    row.update(width=e["width"], height=e["height"], bytes=src.stat().st_size if dry else dest.stat().st_size)
    return row


def import_clip(e: dict, folder: str, created: datetime, src: Path, dry: bool) -> dict:
    dest_dir = SELECTED / folder
    stem = target_stem(e, created)
    mp4 = unique(dest_dir / f"{stem}.mp4")
    poster = mp4.with_suffix(".jpg")
    row = base_row(e, folder, created, poster.name, src)
    row.update(video=mp4.name, source_kind="video_still", video_loop="boomerang")
    if dry:
        row.update(width=min(e["width"], MAX_SIDE), height=min(e["height"], MAX_SIDE),
                   duration=round(e["duration"] * 2, 2), bytes=None, video_bytes=None)
        return row
    boomerang(src, mp4)
    # poster one third into the forward pass, pulled from the ENCODED mp4 so
    # dims and orientation match what plays
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{e['duration'] / 3:.3f}", "-i", str(mp4),
         "-frames:v", "1", "-q:v", "3", str(poster)])
    with Image.open(poster) as im:
        w, h = im.size
    row.update(width=w, height=h, duration=round(probe_duration(mp4), 2),
               bytes=poster.stat().st_size, video_bytes=mp4.stat().st_size)
    # reorder like the existing video rows: created, filename, video, duration, …
    ordered = {k: row[k] for k in ("created", "filename", "video", "duration")}
    ordered.update({k: v for k, v in row.items() if k not in ordered})
    return ordered


def add_keys(data_file: Path, keys: list[str], dry: bool) -> None:
    if not keys:
        return
    doc = json.loads(data_file.read_text())
    have = set(doc["keys"])
    new = [k for k in keys if k not in have]
    if not new:
        return
    print(f"  + {len(new)} keys -> {data_file.relative_to(ROOT)}")
    if dry:
        return
    doc["keys"] = sorted(have | set(new))
    data_file.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def cmd_apply(args: argparse.Namespace) -> None:
    plan = json.loads(Path(args.plan).read_text())
    entries = plan["entries"]
    if args.zone:
        entries = [e for e in entries if e["zone"] == args.zone]
    dry = args.dry_run
    imported = skipped = held = 0
    construction_keys: list[str] = []
    drawing_keys: list[str] = []
    touched: set[str] = set()
    for e in entries:
        src = ZONES / e["zone"] / e["file"]
        if not src.exists():
            print(f"missing (already moved?): {e['zone']}/{e['file']}")
            continue
        if e["action"] == "hold":
            held += 1
            continue
        if e["action"] == "skip":
            skipped += 1
            archive(src, e["zone"], dry)
            continue
        folder = e.get("folder")
        if not folder:
            print(f"hold: {e['zone']}/{e['file']} has no folder")
            held += 1
            continue
        created = parse_created(e["created"])
        if not created:
            print(f"hold: {e['zone']}/{e['file']} has no usable created time")
            held += 1
            continue
        dest_dir = SELECTED / folder
        if not dest_dir.is_dir():
            print(f"  creating selected/{folder} (add it to the page's modernFolders)")
            if not dry:
                dest_dir.mkdir(parents=True)
        importer = import_clip if e["kind"] == "clip" else import_still
        print(f"{'would import' if dry else 'importing'} {e['zone']}/{e['file']} -> {folder}/ ({e['era']})", flush=True)
        row = importer(e, folder, created, src, dry)
        if not dry:
            rows = load_manifest(dest_dir)
            rows.append(row)  # append only; catalog.mjs orders by date itself
            save_manifest(dest_dir, rows)
        key = f"{KEY_PREFIX}/{folder}/{row['filename']}"
        if e["era"] == "construction":
            construction_keys.append(key)
        elif e["era"] == "drawings":
            drawing_keys.append(key)
        touched.add(folder)
        imported += 1
        archive(src, e["zone"], dry)
    add_keys(DATA / "construction.json", construction_keys, dry)
    add_keys(DATA / "drawings.json", drawing_keys, dry)
    print(f"\n{'dry run: ' if dry else ''}{imported} imported, {skipped} skipped (archived), {held} held")
    if touched:
        print("folders touched:", ", ".join(sorted(touched)))
        print("next: cd website && make catalog   (then make restart if the dev server is up)")


def archive(src: Path, zone: str, dry: bool) -> None:
    dest = IMPORTED / zone / src.name
    if dry:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(unique(dest)))


# ------------------------------------------------------------------- main ----
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="scan the drop zones and write the plan")
    p.add_argument("--zone", help="only this zone")
    p.add_argument("--out", default=str(PLAN_PATH))
    p.set_defaults(fn=cmd_plan)
    a = sub.add_parser("apply", help="carry out the plan")
    a.add_argument("--plan", default=str(PLAN_PATH))
    a.add_argument("--zone", help="only this zone")
    a.add_argument("--dry-run", action="store_true")
    a.set_defaults(fn=cmd_apply)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
