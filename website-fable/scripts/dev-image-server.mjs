#!/usr/bin/env node
// Local stand-in for Cloudflare image transformations. Serves resized WebP
// straight from the repo photos/ tree so dev matches prod URL shape:
//   GET /img/<key>?w=800&fit=cover&q=78
// Cropping (cover/contain) is done client-side with CSS object-fit, so this
// only resizes by width. Results are cached on disk under .img-cache/.

import http from "node:http";
import { createHash } from "node:crypto";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SITE = path.resolve(HERE, "..");
const REPO = path.resolve(SITE, "..");
const PHOTOS = path.join(REPO, "photos");
const CACHE = path.join(SITE, ".img-cache");
const PORT = Number(process.env.DEV_IMG_PORT ?? 8788);

await mkdir(CACHE, { recursive: true });

function send(res, status, body, headers = {}) {
  res.writeHead(status, headers);
  res.end(body);
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://localhost:${PORT}`);
    if (!url.pathname.startsWith("/img/")) return send(res, 404, "not found");

    const key = decodeURIComponent(url.pathname.slice("/img/".length));
    const w = Math.min(4000, Math.max(16, Number(url.searchParams.get("w")) || 800));
    const q = Math.min(100, Math.max(1, Number(url.searchParams.get("q")) || 78));

    // Resolve + contain within PHOTOS (no traversal).
    const abs = path.resolve(PHOTOS, key);
    if (!abs.startsWith(PHOTOS + path.sep) || !existsSync(abs)) {
      return send(res, 404, `no such image: ${key}`);
    }

    const hash = createHash("sha1").update(`${key}|${w}|${q}`).digest("hex");
    const cached = path.join(CACHE, `${hash}.webp`);
    let buf;
    if (existsSync(cached)) {
      buf = await readFile(cached);
    } else {
      buf = await sharp(abs)
        .rotate() // honor EXIF orientation
        .resize({ width: w, withoutEnlargement: true })
        .webp({ quality: q })
        .toBuffer();
      await writeFile(cached, buf);
    }
    send(res, 200, buf, {
      "Content-Type": "image/webp",
      "Cache-Control": "public, max-age=86400",
      "Access-Control-Allow-Origin": "*",
    });
  } catch (err) {
    send(res, 500, String(err?.message ?? err));
  }
});

server.listen(PORT, () => {
  console.log(`dev-image-server: http://localhost:${PORT}/img/<key>?w=800`);
});
