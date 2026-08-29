// @ts-check
import { defineConfig } from "astro/config";

// Static site → builds to dist/, deploys to Cloudflare Pages (make deploy).
// Images are pre-rendered WebP shipped with the deploy (prod) or the local
// dev image server (dev) via src/lib/imageUrl.ts.
export default defineConfig({
  site: "https://bwb.samuelclay.com",
  devToolbar: { enabled: false },
});
