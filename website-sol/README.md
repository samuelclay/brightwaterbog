# Bright Water Bog website

Interactive sculpture-trail website for Bright Water Bog. The application is
fully contained in this directory; it reads source media from the parent repo
only when the local import commands are run.

## Local development

```bash
npm install
npm run media:catalog
npm run media:build
npm run dev
```

Astro prints the local URL, normally `http://localhost:4321/`.

## Editorial workflow

- Sculpture and collection records live in `src/content/works/`.
- Source-folder assignments live in `content/media-sources.json`.
- Per-file chapter, alt-text, focal-point, and publication overrides live in
  `content/media-overrides.json`.
- Visitor details live in `content/visit.json`.
- `npm run media:catalog` discovers source files without changing overrides.
- `npm run media:build` writes privacy-stripped web derivatives to
  `public/media/` and a browser-safe manifest to `src/generated/`.

Newly discovered media is visible in the local draft site but remains
`reviewed: false`. Before a work can be marked `published`, review its history,
identity, media section, and alt text. The production content check rejects
editorial placeholders and unreviewed media.

The four media chapters are fixed: **Now, Aerial, Then, Construction**.
Construction is always last. Empty chapters disappear from the interface.

## Checks and builds

```bash
npm run check
npm run build
npm run test:e2e
```

`npm run build` intentionally includes drafts for local and `workers.dev`
previews. `npm run build:production` performs strict publication validation and
omits drafts.

## Cloudflare

The site is a pre-rendered Astro build deployed through Workers Static Assets;
there is no Worker entrypoint or runtime database.

```bash
npm run deploy              # draft workers.dev preview
npm run deploy:production   # strict public build
```

For Git builds, use `npm ci && npm run build` on preview branches. Switch the
production build command to `npm run build:production` after the visitor address,
access copy, histories, and media reviews are complete. Configure
`PUBLIC_CLOUDFLARE_ANALYTICS_TOKEN` only in production.
