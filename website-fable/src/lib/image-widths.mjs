// Every width the site requests through imageUrl()/srcset(). Production
// images are pre-rendered at build time (scripts/prerender-images.mjs) at
// exactly these widths — a call site asking for a width missing from
// ALL_WIDTHS will 404 in prod while working fine against the dev resizer.
// Plain .mjs so both the Vite build and the node prerender script can import.

export const STRIP_WIDTHS = [500, 760, 1040]; // photo strips / thumbs
export const FULL_WIDTHS = [1200, 1800, 2600]; // hero srcset + lightbox fulls
export const EXTRA_WIDTHS = [640, 2200]; // PoemGallery thumb, hero primary src
export const ZOOM_WIDTHS = [4000]; // lightbox zoom-in upgrade (≈ original width; smaller photos render at their own width)

export const ALL_WIDTHS = [...new Set([...STRIP_WIDTHS, ...FULL_WIDTHS, ...EXTRA_WIDTHS, ...ZOOM_WIDTHS])].sort(
  (a, b) => a - b,
);
