// Single place that turns a photo key (path relative to the repo photos/ dir)
// into a URL. In dev it points at the local sharp resizer (scripts/dev-image-server.mjs);
// in prod it points at Cloudflare image transformations in front of the R2 bucket.
//
// Env (all optional; sensible defaults for local dev):
//   PUBLIC_DEV_IMG   dev resizer origin           (default http://localhost:8788)
//   PUBLIC_IMG_ZONE  CF zone that does transforms (e.g. https://img.brightwaterbog.com)
//   PUBLIC_R2_BASE   public R2 origin for originals (e.g. https://pub-xxxx.r2.dev)

const DEV = import.meta.env.DEV;
const DEV_IMG = import.meta.env.PUBLIC_DEV_IMG ?? "http://localhost:8788";
const IMG_ZONE = import.meta.env.PUBLIC_IMG_ZONE ?? "";
const R2_BASE = import.meta.env.PUBLIC_R2_BASE ?? "";

export type Fit = "cover" | "contain";

export interface ImageOpts {
  /** Target width in CSS pixels (before DPR). */
  w: number;
  fit?: Fit;
  /** Quality 1–100. */
  q?: number;
  /**
   * Cache-busting fingerprint of the source photo (e.g. "3024x4032").
   * Replacing a photo under the same key changes its dims, which changes the
   * URL — otherwise browsers/CDNs keep serving the old render for up to their
   * full TTL, and JS-initiated loads (the lightbox) ignore hard reloads.
   */
  v?: string;
}

const enc = (key: string) => key.split("/").map(encodeURIComponent).join("/");

export function imageUrl(key: string, { w, fit = "cover", q = 78, v }: ImageOpts): string {
  const ver = v ? `&v=${encodeURIComponent(v)}` : "";
  if (DEV || !IMG_ZONE || !R2_BASE) {
    return `${DEV_IMG}/img/${enc(key)}?w=${w}&fit=${fit}&q=${q}${ver}`;
  }
  const opts = `width=${w},fit=${fit},format=auto,quality=${q}`;
  const src = `${R2_BASE}/${enc(key)}${v ? `?v=${encodeURIComponent(v)}` : ""}`;
  return `${IMG_ZONE}/cdn-cgi/image/${opts}/${src}`;
}

/** Build a srcset across widths for responsive <img>. */
export function srcset(key: string, widths: number[], fit: Fit = "cover", q = 78, v?: string): string {
  return widths.map((w) => `${imageUrl(key, { w, fit, q, v })} ${w}w`).join(", ");
}

// Width ladders used around the site.
export const STRIP_WIDTHS = [500, 760, 1040];
export const FULL_WIDTHS = [1200, 1800, 2600];
