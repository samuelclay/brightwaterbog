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
const MODERN = path.join(PHOTOS, "apple-photos-stained-glass", "selected");
const CONTENT = path.join(SITE, "src", "content", "sculptures");
const DATA = path.join(SITE, "src", "data");

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
    // Order within a stop: Now → Aerial → Then → Construction (construction last).
    const all = [...now, ...aerial, ...then, ...construction];
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

  // Stylized trail layout: connecting sculptures by raw GPS in walking order
  // makes a self-crossing scribble (the real path doubles back on itself), so
  // we lay the stops out as beads along a smooth left-to-right meander instead.
  // Legible, never crosses, reads as progress. GPS is kept on each node for a
  // possible real-aerial view later.
  const N = nodes.length;
  const VW = 100;
  const VH = 62;
  const padX = 8;
  const padY = 13;
  // deterministic pseudo-random in [0,1) from an index (no Math.random)
  const rand = (i) => {
    const v = Math.sin((i + 1) * 12.9898) * 43758.5453;
    return v - Math.floor(v);
  };
  nodes.forEach((n, i) => {
    const t = N > 1 ? i / (N - 1) : 0.5;
    n.x = +(padX + t * (VW - 2 * padX)).toFixed(2);
    // Two overlaid sines + gentle jitter → an organic, hand-drawn wander.
    const wave = Math.sin(t * Math.PI * 3.1) * 0.72 + Math.sin(t * Math.PI * 1.3 + 0.8) * 0.28;
    const jit = (rand(i) - 0.5) * 0.16;
    n.y = +(VH / 2 + (wave + jit) * (VH / 2 - padY)).toFixed(2);
  });

  // Smooth path through the beads (uniform Catmull-Rom → cubic bézier).
  const pts = nodes.map((n) => [n.x, n.y]);
  let pathD = "";
  if (pts.length === 1) {
    pathD = `M ${pts[0][0]} ${pts[0][1]}`;
  } else {
    pathD = `M ${pts[0][0]} ${pts[0][1]}`;
    for (let i = 0; i < pts.length - 1; i++) {
      const p0 = pts[i - 1] ?? pts[i];
      const p1 = pts[i];
      const p2 = pts[i + 1];
      const p3 = pts[i + 2] ?? p2;
      const c1x = p1[0] + (p2[0] - p0[0]) / 6;
      const c1y = p1[1] + (p2[1] - p0[1]) / 6;
      const c2x = p2[0] - (p3[0] - p1[0]) / 6;
      const c2y = p2[1] - (p3[1] - p1[1]) / 6;
      pathD += ` C ${c1x.toFixed(2)} ${c1y.toFixed(2)}, ${c2x.toFixed(2)} ${c2y.toFixed(2)}, ${p2[0]} ${p2[1]}`;
    }
  }

  const trail = {
    viewBox: `0 0 ${VW} ${VH}`,
    pathD,
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
