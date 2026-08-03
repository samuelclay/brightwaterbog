#!/usr/bin/env node
// Generates src/data/photos.json and src/data/trail.json from the photo tree.
//
// - Reads every content/sculptures/*.md for folder mappings, GPS, path order.
// - Scanned photos: listed from photos/scanned/<folder>, dimensions read with sharp.
// - Modern photos: read from each folder's _manifest.json (GPS + created + dims);
//   falls back to sharp if a manifest is missing.
// - photos.json: { [slug]: PhotoEntry[] }  (scanned first, then modern)
// - trail.json:  ordered outdoor nodes with GPS projected to a 0..100 viewBox.
//
// Photo identity is the path relative to the repo photos/ dir; the dev image
// server resolves photos/<key>, and R2 uploads preserve the same key.

import { readFile, readdir, writeFile, mkdir, stat } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import matter from "gray-matter";
import sharp from "sharp";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SITE = path.resolve(HERE, "..");
const REPO = path.resolve(SITE, "..");
const PHOTOS = path.join(REPO, "photos");
const SCANNED = path.join(PHOTOS, "scanned");
const APPLE = path.join(PHOTOS, "apple-photos-stained-glass");
const MODERN = path.join(APPLE, "selected");
const CONTENT = path.join(SITE, "src", "content", "sculptures");
const DATA = path.join(SITE, "src", "data");

// Curated per-photo construction flags (src/data/construction.json). A "now"
// photo listed there becomes era "construction-now"; a "then" photo becomes
// era "construction" — each shown at the end of its own strip section.
const CONSTRUCTION_FILE = path.join(DATA, "construction.json");
const constructionKeys = existsSync(CONSTRUCTION_FILE)
  ? new Set(JSON.parse(await readFile(CONSTRUCTION_FILE, "utf8")).keys)
  : new Set();

const IMG_RE = /\.(jpe?g|png|webp)$/i;
const isImage = (f) => IMG_RE.test(f) && !f.startsWith(".") && !f.startsWith("_");

/** Read displayed dimensions (respecting EXIF orientation) via sharp. */
async function dims(absPath) {
  try {
    const m = await sharp(absPath).metadata();
    const rotated = m.orientation && m.orientation >= 5;
    const w = rotated ? m.height : m.width;
    const h = rotated ? m.width : m.height;
    return { width: w ?? null, height: h ?? null };
  } catch {
    return { width: null, height: null };
  }
}

const orient = (w, h) =>
  w && h ? (w > h ? "landscape" : h > w ? "portrait" : "square") : "landscape";

async function listImages(dir) {
  if (!existsSync(dir)) return [];
  const entries = await readdir(dir);
  return entries.filter(isImage).sort();
}

// Small concurrency limiter so we don't open thousands of files at once.
async function mapLimit(items, limit, fn) {
  const out = new Array(items.length);
  let i = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (i < items.length) {
      const idx = i++;
      out[idx] = await fn(items[idx], idx);
    }
  });
  await Promise.all(workers);
  return out;
}

// Scanned-root folders. `era` is "then" (finished, decades ago) or
// "construction" (build/in-progress shots) — both live under photos/scanned.
async function scannedEntries(folder, era = "then") {
  const dir = path.join(SCANNED, folder);
  const files = await listImages(dir);
  return mapLimit(files, 8, async (file) => {
    const { width, height } = await dims(path.join(dir, file));
    return {
      key: `scanned/${folder}/${file}`,
      era,
      date: null,
      width,
      height,
      orientation: orient(width, height),
    };
  });
}

async function modernEntries(folder) {
  const dir = path.join(MODERN, folder);
  if (!existsSync(dir)) return [];
  const manifestPath = path.join(dir, "_manifest.json");
  if (existsSync(manifestPath)) {
    const rows = JSON.parse(await readFile(manifestPath, "utf8"));
    const entries = rows
      .filter((r) => r.filename && isImage(r.filename))
      .map((r) => ({
        key: `apple-photos-stained-glass/selected/${folder}/${r.filename}`,
        era: "now",
        date: r.created ? r.created.replace(" ", "T") : null,
        width: r.width ?? null,
        height: r.height ?? null,
        orientation: orient(r.width, r.height),
        gps:
          r.latitude && r.longitude
            ? { lat: r.latitude, lon: r.longitude }
            : undefined,
      }));
    entries.sort((a, b) => (a.date ?? "").localeCompare(b.date ?? ""));
    return entries;
  }
  // Fallback: no manifest → list + sharp.
  const files = await listImages(dir);
  return mapLimit(files, 8, async (file) => {
    const { width, height } = await dims(path.join(dir, file));
    return {
      key: `apple-photos-stained-glass/selected/${folder}/${file}`,
      era: "now",
      date: null,
      width,
      height,
      orientation: orient(width, height),
    };
  });
}

// Poem-placard folders under photos/apple-photos-stained-glass/<folder>.
async function poemEntries(folder) {
  const dir = path.join(APPLE, folder);
  if (!existsSync(dir)) return [];
  const manifestPath = path.join(dir, "_manifest.json");
  if (existsSync(manifestPath)) {
    const rows = JSON.parse(await readFile(manifestPath, "utf8"));
    return rows
      .filter((r) => r.filename && isImage(r.filename))
      .map((r) => ({
        key: `apple-photos-stained-glass/${folder}/${r.filename}`,
        era: "poem",
        date: r.created ? r.created.replace(" ", "T") : null,
        width: r.width ?? null,
        height: r.height ?? null,
        orientation: orient(r.width, r.height),
      }));
  }
  const files = await listImages(dir);
  return mapLimit(files, 8, async (file) => {
    const { width, height } = await dims(path.join(dir, file));
    return {
      key: `apple-photos-stained-glass/${folder}/${file}`,
      era: "poem",
      date: null,
      width,
      height,
      orientation: orient(width, height),
    };
  });
}

function median(nums) {
  if (!nums.length) return null;
  const s = [...nums].sort((a, b) => a - b);
  return s[Math.floor(s.length / 2)];
}

async function main() {
  if (!existsSync(CONTENT)) {
    console.error(`No content dir at ${CONTENT}`);
    process.exit(1);
  }
  const files = (await readdir(CONTENT)).filter((f) => f.endsWith(".md"));
  if (!files.length) {
    console.error("No sculpture .md files found — nothing to catalog.");
    process.exit(1);
  }

  const stops = [];
  for (const file of files) {
    const slug = file.replace(/\.md$/, "");
    const { data } = matter(await readFile(path.join(CONTENT, file), "utf8"));
    stops.push({ slug, fm: data });
  }

  const photos = {};
  const nodes = [];

  for (const { slug, fm } of stops) {
    const then = (
      await Promise.all((fm.scannedFolders ?? []).map((f) => scannedEntries(f, "then")))
    ).flat();
    const now = (
      await Promise.all((fm.modernFolders ?? []).map(modernEntries))
    ).flat();
    const aerial = (
      await Promise.all((fm.aerialFolders ?? []).map((f) => scannedEntries(f, "aerial")))
    ).flat();
    const construction = (
      await Promise.all(
        (fm.constructionFolders ?? []).map((f) => scannedEntries(f, "construction")),
      )
    ).flat();
    const poems = (
      await Promise.all((fm.poemFolders ?? []).map(poemEntries))
    ).flat();
    // Per-photo construction flags split each era: flagged "now" photos become
    // construction-now, flagged "then" photos join the scanned construction era.
    for (const p of now) if (constructionKeys.has(p.key)) p.era = "construction-now";
    for (const p of then) if (constructionKeys.has(p.key)) p.era = "construction";
    const nowFinished = now.filter((p) => p.era === "now");
    const nowConstruction = now.filter((p) => p.era === "construction-now");
    const thenFinished = then.filter((p) => p.era === "then");
    const thenConstruction = then.filter((p) => p.era === "construction");

    // Order within a stop: Now → its construction → Aerial → Then → its
    // construction (folder-level constructionFolders merge into the latter).
    const all = [
      ...nowFinished,
      ...nowConstruction,
      ...aerial,
      ...thenFinished,
      ...thenConstruction,
      ...construction,
      ...poems,
    ];
    photos[slug] = all;

    // Resolve GPS: explicit frontmatter wins; else median of recent-photo GPS.
    let gps = fm.gps ?? null;
    if (!gps) {
      const lats = now.map((p) => p.gps?.lat).filter((n) => typeof n === "number");
      const lons = now.map((p) => p.gps?.lon).filter((n) => typeof n === "number");
      const la = median(lats);
      const lo = median(lons);
      if (la != null && lo != null) gps = { lat: la, lon: lo };
    }

    if (fm.section !== "indoor" && fm.section !== "unplaced" && fm.pathOrder != null) {
      nodes.push({
        slug,
        title: fm.title,
        kicker: fm.kicker ?? null,
        glass: fm.glass ?? "amber",
        order: fm.pathOrder,
        status: fm.status ?? "present",
        count: all.length,
        gps,
      });
    }
  }

  nodes.sort((a, b) => a.order - b.order);

  // Accurate hand-tuned map positions (normalized 0..100), matched to the real
  // GPS layout of the trail. A stop's own frontmatter `map: {x,y}` overrides.
  const MAP_COORDS = {
    // Stops 1→2→3→6 read as one straight line; the mailboxes sit on the
    // gun's row to its left; the descent to the dancers curves gently right
    // with the seal & eel close below the gun; the return passes back by
    // the gun (geometric torch) before the torch run climbs to the shed.
    stargate: { x: 15, y: 10 },
    tetris: { x: 13, y: 27 },
    "hoopla-pyramid": { x: 11, y: 44 },
    "welcome-sconce": { x: 3, y: 61 },
    "the-mailbox": { x: 2, y: 54 },
    "wood-seal-and-eel": { x: 9, y: 68 },
    "the-gun": { x: 9, y: 61 },
    "the-dancers": { x: 20, y: 90 },
    "jo-bird": { x: 19, y: 78 },
    // The walk back from the dancers passes the gun again before the torch
    // run — the ribbon bends there (`path`), while the numbered marker sits
    // on the curve halfway to Four Stages (the smoothing joint at the
    // midpoint of the bend and the next stop lies exactly on the ribbon).
    "geometric-torch": { x: 31.5, y: 65, path: { x: 19, y: 60 } },
    "torch-2-fire": { x: 46, y: 59 },
    "four-stages-of-evolution": { x: 44, y: 70 },
    "torch-3-land-bridge": { x: 51, y: 47 },
    "shed-torch": { x: 55, y: 34 },
    // The ribbon still swings out through the tulip's true turn (`path`),
    // but its marker sits further down the run, with Aspire and Porch Light
    // shifted down toward the well to make room.
    "tulip-torch": { x: 75, y: 40, path: { x: 68, y: 30 } },
    "aspire-to-grace": { x: 79, y: 47 },
    "porch-light": { x: 83, y: 52 },
    "the-well": { x: 87, y: 57 },
    "dam-light": { x: 71, y: 15 },
  };
  // A stop's marker (x,y) and the point the trail ribbon bends through
  // (path.x, path.y) usually coincide; `path` lets a marker sit beside the
  // ribbon while the route still bends where the walk actually goes.
  nodes.forEach((n) => {
    const c = n.map ?? MAP_COORDS[n.slug] ?? { x: 50, y: 50 };
    n.x = c.x;
    n.y = c.y;
    n.px = c.path?.x ?? c.x;
    n.py = c.path?.y ?? c.y;
  });

  // Quadratic smoothing (midpoint Q/T) — reads as a routed trail, not a scribble.
  const round = (v) => Number(v.toFixed(2));
  const smoothPath = (pts) => {
    if (!pts.length) return "";
    if (pts.length === 1) return `M ${round(pts[0].x)} ${round(pts[0].y)}`;
    if (pts.length === 2)
      return `M ${round(pts[0].x)} ${round(pts[0].y)} L ${round(pts[1].x)} ${round(pts[1].y)}`;
    const cmds = [`M ${round(pts[0].x)} ${round(pts[0].y)}`];
    for (let i = 1; i < pts.length - 1; i++) {
      const p = pts[i];
      const nx = pts[i + 1];
      cmds.push(`Q ${round(p.x)} ${round(p.y)} ${round((p.x + nx.x) / 2)} ${round((p.y + nx.y) / 2)}`);
    }
    const last = pts[pts.length - 1];
    cmds.push(`T ${round(last.x)} ${round(last.y)}`);
    return cmds.join(" ");
  };

  // Stretch the map vertically: coords stay hand-tuned in 0..100, but the
  // emitted geometry is 2.5× taller so the rail's minimap uses its column's
  // full height (capped by CSS at ~75svh).
  const Y_STRETCH = 2.5;
  nodes.forEach((n) => {
    n.y = round(n.y * Y_STRETCH);
    n.py = round(n.py * Y_STRETCH);
  });

  const pts = nodes.map((n) => ({ x: n.px, y: n.py }));
  const pathD = smoothPath(pts); // open — drives progress + active-stop mapping
  const loopD = pts.length > 2 ? smoothPath([...pts, pts[0]]) : pathD; // closes 20 → 1

  const trail = {
    viewBox: `0 0 100 ${100 * Y_STRETCH}`,
    pathD,
    loopD,
    nodes,
  };

  await mkdir(DATA, { recursive: true });
  await writeFile(path.join(DATA, "photos.json"), JSON.stringify(photos, null, 2));
  await writeFile(path.join(DATA, "trail.json"), JSON.stringify(trail, null, 2));

  const total = Object.values(photos).reduce((s, a) => s + a.length, 0);
  console.log(
    `catalog: ${stops.length} stops, ${total} photos, ${nodes.length} trail nodes → src/data/`,
  );
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
