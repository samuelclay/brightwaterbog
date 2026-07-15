// Cloudflare Pages worker: inject per-recipe OpenGraph tags for /r/<id>-<slug>
// URLs so WhatsApp/iMessage link previews show the recipe's title and photo.
// Everything else passes straight through to static assets.

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const m = url.pathname.match(/^\/r\/(\d+)/);
    if (!m) return env.ASSETS.fetch(request);

    const [pageRes, ogRes] = await Promise.all([
      env.ASSETS.fetch(new URL("/", url)),
      env.ASSETS.fetch(new URL("/data/og.json", url)),
    ]);
    let html = await pageRes.text();
    const og = await ogRes.json().catch(() => null);
    const r = og && og[m[1]];
    if (r) {
      const tags = [
        `<title>${esc(r.t)} — The Purple Carrot Book</title>`,
        `<meta property="og:type" content="article">`,
        `<meta property="og:site_name" content="The Purple Carrot Book">`,
        `<meta property="og:title" content="${esc(r.t)}">`,
        r.d ? `<meta property="og:description" content="${esc(r.d)}">` : "",
        `<meta property="og:image" content="${url.origin}/images/${r.i}">`,
        `<meta property="og:url" content="${url.origin}${url.pathname}">`,
        `<meta name="twitter:card" content="summary_large_image">`,
      ].filter(Boolean).join("\n");
      html = html.replace("<title>The Purple Carrot Book</title>", tags);
    }
    return new Response(html, {
      headers: { "content-type": "text/html; charset=utf-8" },
    });
  },
};
