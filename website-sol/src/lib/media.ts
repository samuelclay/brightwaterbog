import type { ImageMedia, ImageVariant, MediaItem } from "./types";

export const srcset = (variants: ImageVariant[]) => variants.map((variant) => `${variant.src} ${variant.width}w`).join(", ");

export const thumbnailFor = (item: MediaItem): ImageVariant => {
  if (item.kind === "video") return item.poster;
  return item.variants.webp[Math.min(1, item.variants.webp.length - 1)] || item.zoom;
};

export const imageSrcset = (item: ImageMedia, format: "avif" | "webp") => srcset(item.variants[format]);
