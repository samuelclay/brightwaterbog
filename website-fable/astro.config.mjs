// @ts-check
import { defineConfig } from "astro/config";

// Static site → builds to dist/, deploys to Cloudflare Pages.
// Images are served from Cloudflare R2 (prod) or the local dev image server
// (dev) via src/lib/imageUrl.ts — Astro never bundles the photos.
export default defineConfig({
  site: "https://brightwaterbog.pages.dev",
  devToolbar: { enabled: false },
});
