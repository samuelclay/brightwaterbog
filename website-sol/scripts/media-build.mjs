#!/usr/bin/env node

import { spawn } from "node:child_process";
import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const CATALOG_PATH = path.join(ROOT, "content/media-catalog.json");
const OUTPUT_ROOT = path.join(ROOT, "public/media");
const MANIFEST_PATH = path.join(ROOT, "src/generated/media-manifest.json");
const LONG_EDGES = [640, 1280, 2560];
const AVIF_EDGES = new Set([640, 1280]);
const MAX_ASSET_BYTES = 25 * 1024 * 1024;
const CONCURRENCY = Math.max(2, Math.min(4, Number(process.env.MEDIA_CONCURRENCY || 3)));

async function readJson(filename) {
  return JSON.parse(await fs.readFile(filename, "utf8"));
}

async function exists(filename) {
  try {
    await fs.access(filename);
    return true;
  } catch {
    return false;
  }
}

function orientedDimensions(metadata) {
  const swap = metadata.orientation >= 5 && metadata.orientation <= 8;
  return {
    width: swap ? metadata.height : metadata.width,
    height: swap ? metadata.width : metadata.height,
  };
}

function targetEdges(width, height) {
  const longest = Math.max(width, height);
  return [...new Set(LONG_EDGES.map((edge) => Math.min(edge, longest)))].sort((a, b) => a - b);
}

async function buildVariant(source, output, edge, format) {
  if (!(await exists(output))) {
    let pipeline = sharp(source, { failOn: "warning" }).rotate().resize({
      width: edge,
      height: edge,
      fit: "inside",
      withoutEnlargement: true,
    });
    pipeline = format === "avif" ? pipeline.avif({ quality: 62, effort: 4 }) : pipeline.webp({ quality: 84, effort: 4 });
    await pipeline.toFile(output);
  }
  const metadata = await sharp(output).metadata();
  const stat = await fs.stat(output);
  if (stat.size > MAX_ASSET_BYTES) throw new Error(`${output} exceeds Cloudflare's 25 MiB asset limit`);
  return { src: `/${path.relative(path.join(ROOT, "public"), output).split(path.sep).join("/")}`, width: metadata.width, height: metadata.height };
}

function run(command, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: ["ignore", "ignore", "pipe"] });
    let stderr = "";
    child.stderr.on("data", (chunk) => (stderr += chunk));
    child.on("error", reject);
    child.on("close", (code) => (code === 0 ? resolve() : reject(new Error(`${command} failed (${code}): ${stderr}`))));
  });
}

async function probeVideo(source) {
  return new Promise((resolve, reject) => {
    const child = spawn("ffprobe", ["-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration", "-of", "json", source]);
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => (stdout += chunk));
    child.stderr.on("data", (chunk) => (stderr += chunk));
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) return reject(new Error(`ffprobe failed (${code}): ${stderr}`));
      const data = JSON.parse(stdout);
      resolve({ width: data.streams[0].width, height: data.streams[0].height, duration: Number(data.format.duration || 0) });
    });
  });
}

async function buildImage(item) {
  const source = path.resolve(ROOT, item.source);
  const outputDirectory = path.join(OUTPUT_ROOT, item.workSlug);
  await fs.mkdir(outputDirectory, { recursive: true });
  const dimensions = orientedDimensions(await sharp(source).metadata());
  const edges = targetEdges(dimensions.width, dimensions.height);
  const webp = [];
  const avif = [];
  for (const edge of edges) {
    webp.push(await buildVariant(source, path.join(outputDirectory, `${item.id}-${edge}.webp`), edge, "webp"));
    if (AVIF_EDGES.has(edge)) {
      avif.push(await buildVariant(source, path.join(outputDirectory, `${item.id}-${edge}.avif`), edge, "avif"));
    }
  }
  return {
    id: item.id,
    workSlug: item.workSlug,
    chapter: item.chapter,
    kind: "image",
    alt: item.alt,
    reviewed: item.reviewed,
    focalPoint: item.focalPoint,
    variants: { avif, webp },
    zoom: webp.at(-1),
  };
}

async function buildVideo(item) {
  const source = path.resolve(ROOT, item.source);
  const outputDirectory = path.join(OUTPUT_ROOT, item.workSlug);
  await fs.mkdir(outputDirectory, { recursive: true });
  const videoOutput = path.join(outputDirectory, `${item.id}.mp4`);
  const posterOutput = path.join(outputDirectory, `${item.id}-poster.webp`);
  if (!(await exists(videoOutput))) {
    await run("ffmpeg", ["-y", "-i", source, "-vf", "scale='min(1920,iw)':-2", "-c:v", "libx264", "-preset", "medium", "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", videoOutput]);
  }
  if (!(await exists(posterOutput))) {
    await run("ffmpeg", ["-y", "-ss", "00:00:00.500", "-i", source, "-frames:v", "1", "-vf", "scale='min(1280,iw)':-2", posterOutput]);
  }
  const stat = await fs.stat(videoOutput);
  if (stat.size > MAX_ASSET_BYTES) throw new Error(`${item.source} produces a clip over 25 MiB; trim it or move it to managed media storage`);
  const probe = await probeVideo(videoOutput);
  const posterMetadata = await sharp(posterOutput).metadata();
  return {
    id: item.id,
    workSlug: item.workSlug,
    chapter: item.chapter,
    kind: "video",
    alt: item.alt,
    reviewed: item.reviewed,
    focalPoint: item.focalPoint,
    src: `/${path.relative(path.join(ROOT, "public"), videoOutput).split(path.sep).join("/")}`,
    poster: { src: `/${path.relative(path.join(ROOT, "public"), posterOutput).split(path.sep).join("/")}`, width: posterMetadata.width, height: posterMetadata.height },
    ...probe,
  };
}

async function main() {
  const catalog = await readJson(CATALOG_PATH);
  const queue = catalog.media.filter((item) => item.published);
  const media = new Array(queue.length);
  let cursor = 0;
  let completed = 0;

  async function worker() {
    while (cursor < queue.length) {
      const index = cursor++;
      const item = queue[index];
      media[index] = item.kind === "image" ? await buildImage(item) : await buildVideo(item);
      completed += 1;
      if (completed % 20 === 0 || completed === queue.length) console.log(`Built ${completed}/${queue.length} media items`);
    }
  }

  await fs.mkdir(path.dirname(MANIFEST_PATH), { recursive: true });
  await fs.mkdir(OUTPUT_ROOT, { recursive: true });
  await Promise.all(Array.from({ length: CONCURRENCY }, () => worker()));
  await fs.writeFile(MANIFEST_PATH, `${JSON.stringify({ generatedAt: new Date().toISOString(), media }, null, 2)}\n`);
  console.log(`Wrote ${media.length} browser-safe media records.`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
