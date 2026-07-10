import { defineCollection } from "astro:content";
import { glob } from "astro/loaders";
import { z } from "astro/zod";

const works = defineCollection({
  loader: glob({ pattern: "**/*.md", base: "./src/content/works" }),
  schema: z.object({
    title: z.string().min(1),
    artists: z.array(z.string().min(1)).default([]),
    section: z.enum(["trail", "indoor", "poetry"]),
    order: z.number().int().positive(),
    status: z.enum(["draft", "published"]).default("draft"),
    eyebrow: z.string().optional(),
    summary: z.string().min(1),
    accent: z.string().regex(/^#[0-9a-fA-F]{6}$/),
    map: z
      .object({
        x: z.number().min(0).max(100),
        y: z.number().min(0).max(130),
      })
      .optional(),
    sourceNote: z.string().optional(),
  }),
});

export const collections = { works };
