#!/usr/bin/env node

import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import YAML from "yaml";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const WORKS_DIR = path.join(ROOT, "src/content/works");
const PRODUCTION = process.argv.includes("--production");
const CHAPTERS = ["now", "aerial", "then", "construction"];

function parseMarkdown(text, filename) {
  const match = text.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/);
  if (!match) throw new Error(`${filename} has no valid frontmatter`);
  return { data: YAML.parse(match[1]), body: match[2].trim() };
}

async function readJson(filename) {
  return JSON.parse(await fs.readFile(filename, "utf8"));
}

async function main() {
  const errors = [];
  const warnings = [];
  const works = [];
  for (const filename of (await fs.readdir(WORKS_DIR)).filter((name) => name.endsWith(".md"))) {
    const slug = filename.replace(/\.md$/, "");
    const parsed = parseMarkdown(await fs.readFile(path.join(WORKS_DIR, filename), "utf8"), filename);
    works.push({ slug, ...parsed });
  }

  const sourceConfig = await readJson(path.join(ROOT, "content/media-sources.json"));
  const visit = await readJson(path.join(ROOT, "content/visit.json"));
  let catalog = { media: [] };
  try {
    catalog = await readJson(path.join(ROOT, "content/media-catalog.json"));
  } catch {
    warnings.push("Run npm run media:catalog before building media.");
  }

  const slugs = new Set(works.map((work) => work.slug));
  const keys = new Set();
  for (const work of works) {
    const key = `${work.data.section}:${work.data.order}`;
    if (keys.has(key)) errors.push(`Duplicate order ${key}`);
    keys.add(key);
    if (work.data.section === "trail" && !work.data.map) errors.push(`${work.slug} needs a normalized map position`);
    if (!sourceConfig.works[work.slug]) errors.push(`${work.slug} has no media-source entry`);
  }

  for (const [slug, groups] of Object.entries(sourceConfig.works)) {
    if (!slugs.has(slug)) errors.push(`Media sources reference unknown work ${slug}`);
    for (const group of groups) {
      if (!CHAPTERS.includes(group.chapter)) errors.push(`${slug} has invalid media chapter ${group.chapter}`);
    }
  }

  for (const item of catalog.media) {
    if (!slugs.has(item.workSlug)) errors.push(`Catalog media ${item.id} references unknown work ${item.workSlug}`);
    if (!CHAPTERS.includes(item.chapter)) errors.push(`Catalog media ${item.id} has invalid chapter ${item.chapter}`);
  }

  if (PRODUCTION) {
    const published = works.filter((work) => work.data.status === "published");
    if (!published.some((work) => work.data.section === "trail")) errors.push("Production requires at least one published trail work");
    for (const work of published) {
      const media = catalog.media.filter((item) => item.workSlug === work.slug && item.published);
      if (!media.length) errors.push(`${work.slug} is published without media`);
      if (media.some((item) => !item.reviewed)) errors.push(`${work.slug} has unreviewed published media`);
      if (media.some((item) => !item.alt || /archive (image|video) \d+$/i.test(item.alt))) errors.push(`${work.slug} has generated or missing alt text`);
      if (work.body.length < 80 || /\b(add|verify|confirm)\b/i.test(work.body)) errors.push(`${work.slug} needs a verified history paragraph`);
    }
    if (visit.status !== "published") errors.push("Visitor details must be published for production");
    for (const key of ["address", "directionsUrl", "parking", "access"]) {
      if (!visit[key] || /\badd\b/i.test(visit[key])) errors.push(`Visitor field ${key} is incomplete`);
    }
  }

  for (const warning of warnings) console.warn(`Warning: ${warning}`);
  if (errors.length) {
    for (const error of errors) console.error(`Error: ${error}`);
    process.exitCode = 1;
    return;
  }
  console.log(`Validated ${works.length} work records${PRODUCTION ? " for production" : ""}.`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
