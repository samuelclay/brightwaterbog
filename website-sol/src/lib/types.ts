export const MEDIA_CHAPTERS = ["now", "aerial", "then", "construction"] as const;

export type MediaChapter = (typeof MEDIA_CHAPTERS)[number];
export type WorkSection = "trail" | "indoor" | "poetry";

export type ImageVariant = {
  src: string;
  width: number;
  height: number;
};

export type ImageMedia = {
  id: string;
  workSlug: string;
  chapter: MediaChapter;
  kind: "image";
  alt: string;
  reviewed: boolean;
  focalPoint: { x: number; y: number };
  variants: {
    avif: ImageVariant[];
    webp: ImageVariant[];
  };
  zoom: ImageVariant;
};

export type VideoMedia = {
  id: string;
  workSlug: string;
  chapter: MediaChapter;
  kind: "video";
  alt: string;
  reviewed: boolean;
  focalPoint: { x: number; y: number };
  src: string;
  poster: ImageVariant;
  width: number;
  height: number;
  duration: number;
};

export type MediaItem = ImageMedia | VideoMedia;

export type MediaManifest = {
  generatedAt: string | null;
  media: MediaItem[];
};

export type VisitConfig = {
  status: "draft" | "published";
  address: string;
  directionsUrl: string;
  parking: string;
  access: string;
  latitude: number;
  longitude: number;
  timezone: "America/New_York";
  dawnOffsetMinutes: number;
  duskOffsetMinutes: number;
};
