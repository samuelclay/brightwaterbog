#!/usr/bin/env node
// One-time scaffolder for content/sculptures/*.md.
// Writes an AI-drafted starter file per stop (frontmatter + prose) but NEVER
// overwrites a file that already exists — so hand edits are safe. Re-run any
// time to add newly listed stops. Pass --force to rewrite everything.

import { writeFile, mkdir, readdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import matter from "gray-matter";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CONTENT = path.resolve(HERE, "..", "src", "content", "sculptures");
const FORCE = process.argv.includes("--force");

// Each stop: slug + frontmatter + drafted prose body. GPS is the median of the
// folder's geotagged photos (from the modern manifests). Prose is a DRAFT —
// square brackets mark facts for Samuel to fill in.
const STOPS = [
  // ---------- OUTDOOR TRAIL (counterclockwise from the entrance) ----------
  {
    slug: "stargate",
    fm: {
      title: "Stargate",
      artist: "Julian Janowitz",
      year: "c. 1995",
      pathOrder: 1,
      section: "outdoor",
      glass: "cobalt",
      gps: { lat: 42.499053, lon: -72.421408 },
      scannedFolders: ["sculpture_02_stargate"],
      modernFolders: ["sculpture_02_stargate"],
      summary: "The first stop on the trail — a ring of colored glass that frames the sky.",
    },
    body: `The trail begins here. Stargate stands at the entrance like a threshold you step through into Julian's world, a ring of leaded glass that catches the first and last light of the day.

[Add the story: when Julian built it, what the glass came from, why it opens the path.]`,
  },
  {
    slug: "tetris",
    fm: {
      title: "Tetris",
      artist: "Julian Janowitz",
      year: "c. 1996",
      pathOrder: 2,
      section: "outdoor",
      glass: "violet",
      gps: { lat: 42.498495, lon: -72.421242 },
      scannedFolders: ["sculpture_03_tetris"],
      modernFolders: ["sculpture_03_tetris"],
      summary: "Interlocking blocks of glass stacked like the falling pieces of the game.",
    },
    body: `Blocks of colored glass lock together like the game's falling tetrominoes, each pane a different hue.

[Add the story behind Tetris — the idea, the build, any repairs over the years.]`,
  },
  {
    slug: "hoopla-pyramid",
    fm: {
      title: "Hoopla Pyramid",
      artist: "Samuel Clay & Brittany Janis",
      year: "[year]",
      pathOrder: 3,
      section: "outdoor",
      glass: "gold",
      gps: { lat: 42.498188, lon: -72.419753 },
      scannedFolders: [],
      modernFolders: ["sculpture_04_hoopla_pyramid"],
      summary: "The one piece not by Julian — built by Samuel Clay and Brittany Janis.",
    },
    body: `The only sculpture on the trail not made by Julian. Samuel Clay and Brittany Janis built the Hoopla Pyramid [year / occasion].

[Add the story: the idea, the build, how it fits into Julian's trail.]`,
  },
  {
    slug: "julians-mailbox",
    fm: {
      title: "The Mailbox",
      kicker: "Julian's mailbox",
      artist: "Julian Janowitz",
      year: "[year]",
      pathOrder: 4,
      section: "outdoor",
      glass: "amber",
      gps: { lat: 42.497722, lon: -72.421738 },
      scannedFolders: [],
      modernFolders: ["sculpture_11_mailbox", "sculpture_15_stone_mailbox"],
      summary: "The stained-glass mailbox that greets you before the gun.",
    },
    body: `Even the mailbox is a piece of art here — glass set into stone at the edge of the path.

[Add the story of the mailbox and the stone base.]`,
  },
  {
    slug: "new-mailbox",
    fm: {
      title: "New Mailbox",
      kicker: "In progress",
      artist: "Samuel Clay",
      pathOrder: 5,
      section: "outdoor",
      status: "placeholder",
      glass: "rose",
      gps: { lat: 42.49776, lon: -72.4217 },
      scannedFolders: [],
      modernFolders: [],
      summary: "A new mailbox in the works — photos coming soon.",
    },
    body: `A new mailbox is being built. Check back — photos will appear here as it comes together.`,
  },
  {
    slug: "wood-seal-and-eel",
    fm: {
      title: "Wood Seal & Eel",
      artist: "Julian Janowitz",
      year: "[year]",
      pathOrder: 6,
      section: "outdoor",
      glass: "teal",
      gps: { lat: 42.497945, lon: -72.421197 },
      scannedFolders: [],
      modernFolders: ["wood_seal_and_eel"],
      summary: "A carved seal and eel near the mailbox — confirm placement on the path.",
    },
    body: `[Confirm where this sits on the walk.] A seal and an eel worked in wood and glass.

[Add the story.]`,
  },
  {
    slug: "the-gun",
    fm: {
      title: "The Gun",
      artist: "Julian Janowitz",
      year: "[year]",
      pathOrder: 7,
      section: "outdoor",
      glass: "garnet",
      gps: { lat: 42.498017, lon: -72.42115 },
      scannedFolders: ["sculpture_05_gun"],
      modernFolders: ["sculpture_05_gun"],
      summary: "A stained-glass gun — provocative, unmistakable.",
    },
    body: `[Add the story of the gun — what it means, when Julian made it.]`,
  },
  {
    slug: "geometric-torch",
    fm: {
      title: "Geometric Torch",
      kicker: "Torch №1",
      artist: "Julian Janowitz",
      year: "[year]",
      pathOrder: 8,
      section: "outdoor",
      glass: "amber",
      gps: { lat: 42.498097, lon: -72.420762 },
      scannedFolders: [],
      modernFolders: ["sculpture_06_torch_1_geometric"],
      summary: "First of the five torches that line the heart of the trail.",
    },
    body: `The first of five torches. This one is all straight edges and facets — a geometric flame.

[Add the story of the torch series and this piece.]`,
  },
  {
    slug: "torch-panel",
    fm: {
      title: "Torch Panel",
      artist: "Julian Janowitz",
      pathOrder: 9,
      section: "outdoor",
      status: "placeholder",
      glass: "gold",
      scannedFolders: [],
      modernFolders: [],
      summary: "A torch panel between the torches — photos to be identified.",
    },
    body: `[Which photos belong to the torch panel? Point me at the folder and I'll wire them in.]`,
  },
  {
    slug: "torch-2-fire",
    fm: {
      title: "Torch №2",
      kicker: "Fire",
      artist: "Julian Janowitz",
      year: "[year]",
      pathOrder: 10,
      section: "outdoor",
      glass: "garnet",
      gps: { lat: 42.498045, lon: -72.42057 },
      scannedFolders: [],
      modernFolders: ["sculpture_07_torch_2_fire"],
      summary: "The fire torch, standing beside the four stages of evolution.",
    },
    body: `The second torch stands beside the Four Stages of Evolution — a torch of fire.

[Add the story.]`,
  },
  {
    slug: "four-stages-of-evolution",
    fm: {
      title: "Four Stages of Evolution",
      artist: "Julian Janowitz",
      year: "[year]",
      pathOrder: 11,
      section: "outdoor",
      glass: "violet",
      gps: { lat: 42.49786, lon: -72.42054 },
      scannedFolders: ["sculpture_12_four_stages_of_evolution"],
      modernFolders: [
        "sculpture_12_four_stages_of_evolution_1",
        "sculpture_13_four_stages_of_evolution_2",
        "sculpture_13_four_stages_of_evolution_3",
        "sculpture_13_four_stages_of_evolution_4",
      ],
      summary: "Four sculptures read as one — a sequence of change across the panels.",
    },
    body: `Four separate sculptures that belong together, read left to right as a single sequence of change.

[Add the story: what the four stages represent, how Julian built them as a set.]`,
  },
  {
    slug: "torch-3-land-bridge",
    fm: {
      title: "Torch №3",
      kicker: "Land Bridge",
      artist: "Julian Janowitz",
      year: "[year]",
      pathOrder: 12,
      section: "outdoor",
      glass: "teal",
      gps: { lat: 42.498205, lon: -72.420342 },
      scannedFolders: ["sculpture_08_torch_3_land_bridge"],
      modernFolders: ["sculpture_08_torch_3_land_bridge"],
      summary: "The third torch, at the land bridge.",
    },
    body: `The third torch marks the land bridge.

[Add the story.]`,
  },
  {
    slug: "shed-torch",
    fm: {
      title: "Shed Torch",
      kicker: "Torch №4",
      artist: "Julian Janowitz",
      year: "[year]",
      pathOrder: 13,
      section: "outdoor",
      glass: "amber",
      gps: { lat: 42.498425, lon: -72.420263 },
      scannedFolders: ["sculpture_09_torch_4_shed"],
      modernFolders: ["sculpture_09_torch_4_shed"],
      summary: "The fourth torch, by the shed.",
    },
    body: `The fourth torch stands by the shed.

[Add the story.]`,
  },
  {
    slug: "tulip-torch",
    fm: {
      title: "Tulip Torch",
      kicker: "Torch №5",
      artist: "Julian Janowitz",
      year: "[year]",
      pathOrder: 14,
      section: "outdoor",
      glass: "rose",
      gps: { lat: 42.498505, lon: -72.419897 },
      scannedFolders: ["sculpture_10_torch_5_tulip"],
      modernFolders: ["sculpture_10_torch_5_tulip"],
      summary: "The last torch — a flame shaped like a tulip.",
    },
    body: `The fifth and final torch blooms like a tulip.

[Add the story.]`,
  },
  {
    slug: "aspire-to-grace",
    fm: {
      title: "Aspire to Grace",
      artist: "Julian Janowitz",
      year: "[year]",
      pathOrder: 15,
      section: "outdoor",
      glass: "violet",
      gps: { lat: 42.498325, lon: -72.419738 },
      scannedFolders: ["sculpture_11_aspire_to_grace"],
      modernFolders: ["sculpture_11_aspire_to_grace"],
      summary: "A tall reaching form near the top of the loop.",
    },
    body: `[Add the story of Aspire to Grace — the title, the form, when it was made.]`,
  },
  {
    slug: "porch-light",
    fm: {
      title: "Porch Light",
      artist: "Julian Janowitz",
      year: "[year]",
      pathOrder: 16,
      section: "outdoor",
      glass: "amber",
      gps: { lat: 42.498222, lon: -72.419638 },
      scannedFolders: ["sculpture_14_porch"],
      modernFolders: ["sculpture_14_porch"],
      summary: "Glass worked into the porch of the house itself.",
    },
    body: `The trail comes back toward the house, where the porch light is glass too.

[Add the story.]`,
  },
  {
    slug: "the-well",
    fm: {
      title: "The Well",
      artist: "Julian Janowitz",
      year: "[year]",
      pathOrder: 17,
      section: "outdoor",
      glass: "cobalt",
      gps: { lat: 42.498092, lon: -72.41945 },
      scannedFolders: ["sculpture_14_well"],
      modernFolders: ["sculpture_14_well"],
      summary: "A wellhead near the house — confirm placement on the path.",
    },
    body: `[Confirm where the well falls on the walk.]

[Add the story.]`,
  },
  {
    slug: "dam-light",
    fm: {
      title: "Dam Light",
      artist: "Julian Janowitz",
      year: "[year]",
      pathOrder: 18,
      section: "outdoor",
      glass: "cobalt",
      gps: { lat: 42.498908, lon: -72.419845 },
      scannedFolders: [],
      modernFolders: ["sculpture_01_dam"],
      summary: "Out at the dam — the path doubles back to reach it.",
    },
    body: `The path doubles back to the dam, where a light sits over the water.

[Add the story.]`,
  },
  {
    slug: "the-dancers",
    fm: {
      title: "The Dancers",
      artist: "Julian Janowitz",
      year: "[year]",
      pathOrder: 19,
      section: "outdoor",
      glass: "rose",
      gps: { lat: 42.497355, lon: -72.419987 },
      scannedFolders: ["sculpture_01_dancers"],
      modernFolders: ["sculpture_16_dancers"],
      summary: "Figures in motion — confirm placement on the path.",
    },
    body: `[Confirm where the dancers fall on the walk.] Figures caught mid-motion in glass.

[Add the story.]`,
  },

  // ---------- INDOOR STAINED GLASS ----------
  {
    slug: "dining-room-flower",
    fm: {
      title: "Dining Room Flower",
      artist: "Julian Janowitz",
      section: "indoor",
      glass: "rose",
      scannedFolders: ["dining_room_flower_1", "dining_room_flower_2"],
      modernFolders: [],
      summary: "A flower panel in the dining room.",
    },
    body: `[Add the story of the dining room flower.]`,
  },
  {
    slug: "dinosaur",
    fm: {
      title: "Dinosaur",
      artist: "Julian Janowitz",
      section: "indoor",
      glass: "teal",
      scannedFolders: ["dinosaur"],
      modernFolders: [],
      summary: "An indoor dinosaur in glass.",
    },
    body: `[Add the story.]`,
  },
  {
    slug: "gourd",
    fm: {
      title: "Gourd",
      artist: "Julian Janowitz",
      section: "indoor",
      glass: "gold",
      scannedFolders: ["gourd"],
      modernFolders: [],
      summary: "A gourd rendered in stained glass.",
    },
    body: `[Add the story.]`,
  },
  {
    slug: "quartz-crystal",
    fm: {
      title: "Quartz Crystal",
      artist: "Julian Janowitz",
      section: "indoor",
      glass: "violet",
      scannedFolders: ["quartz_crystal"],
      modernFolders: [],
      summary: "A faceted crystal piece.",
    },
    body: `[Add the story.]`,
  },
  {
    slug: "saguaro-cactus",
    fm: {
      title: "Saguaro Cactus",
      artist: "Julian Janowitz",
      section: "indoor",
      glass: "teal",
      scannedFolders: ["saguaro_cactus"],
      modernFolders: [],
      summary: "A saguaro cactus in glass.",
    },
    body: `[Add the story.]`,
  },
  {
    slug: "sea-lamp",
    fm: {
      title: "Sea Lamp",
      artist: "Julian Janowitz",
      section: "indoor",
      glass: "cobalt",
      scannedFolders: ["sea_lamp"],
      modernFolders: [],
      summary: "A lamp with a sea motif.",
    },
    body: `[Add the story.]`,
  },
  {
    slug: "lower-bedroom",
    fm: {
      title: "Lower Bedroom",
      artist: "Julian Janowitz",
      section: "indoor",
      glass: "amber",
      scannedFolders: ["lower_bedroom"],
      modernFolders: [],
      summary: "Glass in the lower bedroom.",
    },
    body: `[Add the story / identify these pieces.]`,
  },
  {
    slug: "unfinished-shed-dancers",
    fm: {
      title: "Unfinished Shed Dancers",
      artist: "Julian Janowitz",
      section: "indoor",
      glass: "rose",
      scannedFolders: ["unfinished_shed_dancers_2"],
      modernFolders: [],
      summary: "Dancers left unfinished in the shed.",
    },
    body: `[Add the story.]`,
  },

  // ---------- UNPLACED / TO SORT ----------
  {
    slug: "jo-bird",
    fm: {
      title: "Jo Bird",
      artist: "Julian Janowitz",
      section: "unplaced",
      glass: "gold",
      scannedFolders: ["sculpture_04_jo_bird"],
      modernFolders: [],
      summary: "Scanned photos of Jo Bird — no location yet; place on the path or indoors.",
    },
    body: `[Where does Jo Bird belong — on the trail, or indoors? No GPS on the scans yet.]`,
  },
  {
    slug: "mystery-miscellaneous",
    fm: {
      title: "Mystery Miscellaneous",
      artist: "Julian Janowitz",
      section: "unplaced",
      glass: "violet",
      scannedFolders: ["mystery_miscellaneous"],
      modernFolders: [],
      summary: "Unsorted scans waiting to be identified and filed.",
    },
    body: `[A holding area for scans not yet identified. Sort these into the trail or the indoor section.]`,
  },
];

async function main() {
  await mkdir(CONTENT, { recursive: true });
  const existing = new Set(
    existsSync(CONTENT) ? await readdir(CONTENT) : [],
  );
  let written = 0;
  let skipped = 0;
  for (const stop of STOPS) {
    const file = `${stop.slug}.md`;
    if (existing.has(file) && !FORCE) {
      skipped++;
      continue;
    }
    const contents = matter.stringify(`\n${stop.body}\n`, stop.fm);
    await writeFile(path.join(CONTENT, file), contents);
    written++;
  }
  console.log(
    `scaffold-content: wrote ${written}, skipped ${skipped} existing (${STOPS.length} stops total)`,
  );
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
