#!/usr/bin/env node

import { createHash } from "node:crypto";
import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import YAML from "yaml";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SOURCES_PATH = path.join(ROOT, "content/media-sources.json");
const OVERRIDES_PATH = path.join(ROOT, "content/media-overrides.json");
const OUTPUT_PATH = path.join(ROOT, "content/media-catalog.json");
const WORKS_DIR = path.join(ROOT, "src/content/works");
const IMAGE_EXTENSIONS = new Set([".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".heic"]);
const VIDEO_EXTENSIONS = new Set([".mov", ".mp4", ".m4v", ".webm"]);
const CHAPTERS = ["now", "aerial", "then", "construction"];

async function readJson(filename) {
  return JSON.parse(await fs.readFile(filename, "utf8"));
}

async function sha256(filename) {
  const hash = createHash("sha256");
  const handle = await fs.open(filename, "r");
  try {
    for await (const chunk of handle.readableWebStream()) {
      hash.update(Buffer.from(chunk));
    }
  } finally {
    await handle.close();
  }
  return hash.digest("hex");
}

async function loadTitles() {
  const titles = new Map();
  for (const filename of await fs.readdir(WORKS_DIR)) {
    if (!filename.endsWith(".md")) continue;
    const slug = filename.replace(/\.md$/, "");
    const text = await fs.readFile(path.join(WORKS_DIR, filename), "utf8");
    const match = text.match(/^---\n([\s\S]*?)\n---/);
    const data = match ? YAML.parse(match[1]) : {};
    titles.set(slug, data.title || slug.replaceAll("-", " "));
  }
  return titles;
}

function defaultAlt(title, chapter, index) {
  const chapterLabel = {
    now: "present-day",
    aerial: "aerial",
    then: "historical",
    construction: "construction",
  }[chapter];
  return `${title}, ${chapterLabel} archive image ${index}`;
}

async function main() {
  const sources = await readJson(SOURCES_PATH);
  const overrides = await readJson(OVERRIDES_PATH);
  const titles = await loadTitles();
  const discovered = [];
  const warnings = [];

  for (const [workSlug, groups] of Object.entries(sources.works)) {
    let workIndex = 0;
    for (const group of groups) {
      if (!CHAPTERS.includes(group.chapter)) {
        throw new Error(`Invalid chapter ${group.chapter} for ${workSlug}`);
      }
      const absoluteDirectory = path.resolve(ROOT, group.path);
      let filenames;
      try {
        filenames = await fs.readdir(absoluteDirectory);
      } catch (error) {
        if (error.code === "ENOENT") {
          warnings.push(`Missing source directory: ${group.path}`);
          continue;
        }
        throw error;
      }
      for (const filename of filenames.sort()) {
        const extension = path.extname(filename).toLowerCase();
        const kind = IMAGE_EXTENSIONS.has(extension)
          ? "image"
          : VIDEO_EXTENSIONS.has(extension)
            ? "video"
            : null;
        if (!kind || filename.startsWith(".")) continue;

        const absoluteSource = path.join(absoluteDirectory, filename);
        const source = path.relative(ROOT, absoluteSource).split(path.sep).join("/");
        const digest = await sha256(absoluteSource);
        const override = overrides.bySource[source] || {};
        workIndex += 1;
        discovered.push({
          id: digest.slice(0, 16),
          sha256: digest,
          source,
          workSlug: override.workSlug || workSlug,
          chapter: override.chapter || group.chapter,
          kind,
          alt: override.alt || defaultAlt(titles.get(workSlug), override.chapter || group.chapter, workIndex),
          reviewed: override.reviewed === true,
          published: override.published !== false,
          focalPoint: override.focalPoint || { x: 50, y: 50 },
        });
      }
    }
  }

  const byHash = new Map();
  const duplicates = [];
  for (const item of discovered) {
    if (byHash.has(item.sha256)) {
      duplicates.push({ source: item.source, duplicateOf: byHash.get(item.sha256).source });
      continue;
    }
    byHash.set(item.sha256, item);
  }

  const media = [...byHash.values()].sort((a, b) => {
    return (
      a.workSlug.localeCompare(b.workSlug) ||
      CHAPTERS.indexOf(a.chapter) - CHAPTERS.indexOf(b.chapter) ||
      a.source.localeCompare(b.source)
    );
  });

  await fs.writeFile(
    OUTPUT_PATH,
    `${JSON.stringify({ generatedAt: new Date().toISOString(), media, duplicates, warnings }, null, 2)}\n`,
  );
  console.log(`Cataloged ${media.length} media files across ${new Set(media.map((item) => item.workSlug)).size} works.`);
  if (duplicates.length) console.log(`Skipped ${duplicates.length} byte-identical duplicates.`);
  for (const warning of warnings) console.warn(`Warning: ${warning}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
