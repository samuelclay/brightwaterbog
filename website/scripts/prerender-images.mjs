#!/usr/bin/env node
// Pre-render the production image ladder. For every photo key in
// src/data/photos.json, render WebP at each width in ALL_WIDTHS with the
// exact settings the dev resizer uses (rotate → resize(width,
// withoutEnlargement) → webp q78), so prod pixels match what dev showed.
//
// Renders are cached in .img-cache/prerender/<w>/<key>.webp (skipped when
// newer than the source photo) and cloned into dist/img/ at the end — run
// this AFTER `astro build` wipes dist/. APFS clonefile makes the copy free.

import { copyFile, mkdir, readFile, stat } from "node:fs/promises";
import { constants } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";
import { ALL_WIDTHS } from "../src/lib/image-widths.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SITE = path.resolve(HERE, "..");
const PHOTOS = path.join(SITE, "..", "photos");
const CACHE = path.join(SITE, ".img-cache", "prerender");
const DIST = path.join(SITE, "dist", "img");
const QUALITY = 78;

const photos = JSON.parse(await readFile(path.join(SITE, "src", "data", "photos.json"), "utf8"));
const keys = [...new Set(Object.values(photos).flat().map((p) => p.key))].sort();

const jobs = [];
for (const key of keys) {
  for (const w of ALL_WIDTHS) jobs.push({ key, w });
}

let rendered = 0;
let cached = 0;
let failed = 0;

async function mtime(p) {
  try {
    return (await stat(p)).mtimeMs;
  } catch {
    return -1;
  }
}

async function run({ key, w }) {
  const src = path.join(PHOTOS, key);
  const out = path.join(CACHE, String(w), `${key}.webp`);
  const dest = path.join(DIST, String(w), `${key}.webp`);
  const srcTime = await mtime(src);
  if (srcTime < 0) {
    failed++;
    console.error(`missing source photo: ${key}`);
    return;
  }
  if ((await mtime(out)) >= srcTime) {
    cached++;
  } else {
    await mkdir(path.dirname(out), { recursive: true });
    await sharp(src)
      .rotate() // honor EXIF orientation
      .resize({ width: w, withoutEnlargement: true })
      .webp({ quality: QUALITY })
      .toFile(out);
    rendered++;
  }
  await mkdir(path.dirname(dest), { recursive: true });
  await copyFile(out, dest, constants.COPYFILE_FICLONE);
}

// Small worker pool — sharp saturates cores on its own, but photo I/O benefits
// from a few in flight.
const POOL = Math.max(2, Math.min(8, os.cpus().length - 2));
let next = 0;
await Promise.all(
  Array.from({ length: POOL }, async () => {
    while (next < jobs.length) {
      const job = jobs[next++];
      try {
        await run(job);
      } catch (err) {
        failed++;
        console.error(`render failed: ${job.key} @${job.w}: ${err?.message ?? err}`);
      }
    }
  }),
);

// Videos ship as-is — they were transcoded once at ingest (H.264, long edge
// ≤1280, no audio, faststart), so there is no ladder to render. Copy each
// clip to dist/video/<key>, matching videoUrl() in src/lib/imageUrl.ts.
const videoKeys = [
  ...new Set(Object.values(photos).flat().map((p) => p.video).filter(Boolean)),
].sort();
let videosCopied = 0;
for (const key of videoKeys) {
  const src = path.join(PHOTOS, key);
  if ((await mtime(src)) < 0) {
    failed++;
    console.error(`missing source video: ${key}`);
    continue;
  }
  const dest = path.join(SITE, "dist", "video", key);
  await mkdir(path.dirname(dest), { recursive: true });
  await copyFile(src, dest, constants.COPYFILE_FICLONE);
  videosCopied++;
}

// Social preview card: the same photo the hero leads with (Stargate's newest
// "now" shot, matching Hero.astro's slide pick), cropped to the 1200x630
// Open Graph frame as JPEG — WhatsApp and older scrapers are unreliable with
// WebP. Base.astro points og:image at /og.jpg.
const ogList = photos["stargate"] ?? [];
const ogNows = ogList
  .filter((p) => p.era === "now")
  .sort((a, b) => (a.date ?? "").localeCompare(b.date ?? ""));
const ogPhoto = ogNows.at(-1) ?? ogList[0];
if (ogPhoto) {
  await sharp(path.join(PHOTOS, ogPhoto.key))
    .rotate()
    .resize({ width: 1200, height: 630, fit: "cover" })
    .jpeg({ quality: 82 })
    .toFile(path.join(SITE, "dist", "og.jpg"));
} else {
  console.error("og.jpg skipped: no stargate photo in the catalog");
}

console.log(
  `prerender-images: ${keys.length} photos × ${ALL_WIDTHS.length} widths — ` +
    `${rendered} rendered, ${cached} cached, ${failed} failed` +
    `, ${videosCopied} videos copied` +
    (ogPhoto ? ", og.jpg rendered" : ""),
);
if (failed) process.exit(1);
