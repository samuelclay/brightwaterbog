import { defineCollection, z } from "astro:content";
import { glob } from "astro/loaders";

// One entry per sculpture / stop. The Markdown body is the (AI-drafted, then
// human-edited) history prose. Photo folders are listed explicitly because the
// on-disk folder numbers do NOT match the walking-path order, and eras map to
// different folders (e.g. the four-stages piece is one scanned folder but four
// modern split folders).
const sculptures = defineCollection({
  loader: glob({ pattern: "**/*.md", base: "./src/content/sculptures" }),
  schema: z.object({
    title: z.string(),
    // Optional short label under the title, e.g. "Torch №1".
    kicker: z.string().optional(),
    artist: z.string().default("Julian Janowitz"),
    // Freeform, e.g. "c. 1998" or "1994–1998".
    year: z.string().optional(),
    // Position on the outdoor trail (1 = first). Omit for indoor / unplaced.
    pathOrder: z.number().optional(),
    section: z.enum(["outdoor", "indoor", "unplaced"]).default("outdoor"),
    // "placeholder" renders a coming-soon card (e.g. the new mailbox).
    status: z.enum(["present", "placeholder"]).default("present"),
    // True GPS (median of that folder's geotagged photos); drives minimap nodes.
    gps: z.object({ lat: z.number(), lon: z.number() }).optional(),
    // Signature glass color for this piece's minimap node + accents.
    glass: z
      .enum(["amber", "cobalt", "teal", "garnet", "violet", "rose", "gold"])
      .default("amber"),
    // Photo source folders, relative to their era root.
    // Section order in the strip: Now → Aerial → Then → Construction.
    // now   = recent photos (photos/apple-photos-stained-glass/selected)
    // aerial = overhead / drone shots (photos/scanned/<folder>)
    // then  = Julian's scanned build-era prints (photos/scanned/<folder>)
    // construction = in-progress / build shots (photos/scanned/<folder>) — LAST
    scannedFolders: z.array(z.string()).default([]),
    modernFolders: z.array(z.string()).default([]),
    aerialFolders: z.array(z.string()).default([]),
    constructionFolders: z.array(z.string()).default([]),
    // One-sentence summary used in nav / previews.
    summary: z.string().optional(),
  }),
});

export const collections = { sculptures };
