import { getCollection, render, type CollectionEntry } from "astro:content";
import mediaManifestJson from "../generated/media-manifest.json";
import visitJson from "../../content/visit.json";
import type { MediaItem, MediaManifest, VisitConfig, WorkSection } from "./types";

export type WorkRecord = {
  slug: string;
  data: CollectionEntry<"works">["data"];
  Content: Awaited<ReturnType<typeof render>>["Content"];
  media: MediaItem[];
};

const mediaManifest = mediaManifestJson as MediaManifest;
export const visitConfig = visitJson as VisitConfig;

export function showDraftContent(): boolean {
  return import.meta.env.DEV || import.meta.env.PUBLIC_SHOW_DRAFTS === "true";
}

export async function getWorkRecords(section?: WorkSection): Promise<WorkRecord[]> {
  const includeDrafts = showDraftContent();
  const entries = await getCollection("works");
  const records = await Promise.all(
    entries.map(async (entry) => {
      const { Content } = await render(entry);
      const media = mediaManifest.media.filter((item) => item.workSlug === entry.id);
      return { slug: entry.id, data: entry.data, Content, media };
    }),
  );

  return records
    .filter((record) => !section || record.data.section === section)
    .filter((record) => includeDrafts || (record.data.status === "published" && record.media.length > 0))
    .sort((a, b) => a.data.order - b.data.order);
}

export function heroMediaFor(work: WorkRecord | undefined): MediaItem | undefined {
  if (!work) return undefined;
  const current = work.media.filter((item) => item.chapter === "now" && item.kind === "image");
  return current.at(-1) || work.media.find((item) => item.kind === "image");
}
