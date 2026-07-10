// Drives the scrollytelling: reveals each stop as it enters view and moves the
// minimap marker/lit-path/caption to track the stop nearest the viewport center.

interface NodeRef {
  slug: string;
  el: SVGCircleElement;
  x: number;
  y: number;
}

function init() {
  const stage = document.querySelector<HTMLElement>("[data-scrollstage]");
  const minimap = document.querySelector<HTMLElement>("[data-minimap]");
  if (!stage || !minimap) return;

  const stops = Array.from(stage.querySelectorAll<HTMLElement>("[data-stop]"));
  if (!stops.length) return;

  const nodeEls = Array.from(minimap.querySelectorAll<SVGCircleElement>(".mm-node"));
  const nodes: NodeRef[] = nodeEls.map((el) => ({
    slug: el.getAttribute("data-node") ?? "",
    el,
    x: parseFloat(el.getAttribute("cx") ?? "0"),
    y: parseFloat(el.getAttribute("cy") ?? "0"),
  }));
  const bySlug = new Map(nodes.map((n, i) => [n.slug, i]));

  const marker = minimap.querySelector<SVGGElement>("[data-marker]");
  const lit = minimap.querySelector<SVGPathElement>("[data-lit]");
  const basePath = minimap.querySelector<SVGPathElement>(".mm-path");
  const pathLen = basePath?.getTotalLength() ?? 0;
  const nowEl = minimap.querySelector<HTMLElement>("[data-now]");
  const countEl = minimap.querySelector<HTMLElement>("[data-count]");
  const root = document.documentElement;

  // --- progressive reveal: light up a stop once it's meaningfully in view ---
  const reveal = new IntersectionObserver(
    (entries) => {
      for (const e of entries) {
        if (e.isIntersecting) e.target.classList.add("is-active");
      }
    },
    { threshold: 0.3 },
  );
  stops.forEach((s) => reveal.observe(s));

  // --- minimap tracking: which stop is nearest viewport center + progress ---
  let ticking = false;
  let lastIndex = -1;

  function update() {
    ticking = false;
    const centerY = window.innerHeight * 0.42;

    // Find the stop whose head is closest to the sight line.
    let index = 0;
    let best = Infinity;
    const tops: number[] = [];
    stops.forEach((s, i) => {
      const head = (s.querySelector(".stop__head") as HTMLElement) ?? s;
      const r = head.getBoundingClientRect();
      tops[i] = r.top;
      const d = Math.abs(r.top - centerY);
      if (d < best) {
        best = d;
        index = i;
      }
    });

    // Progress from this stop toward the next (0..1) for smooth marker glide.
    let progress = 0;
    if (index < stops.length - 1) {
      const span = tops[index + 1] - tops[index];
      if (span > 0) progress = Math.min(1, Math.max(0, (centerY - tops[index]) / span));
    }

    const stop = stops[index];
    const slug = stop.getAttribute("data-slug") ?? "";
    const glass = stop.getAttribute("data-glass") ?? "amber";
    const ni = bySlug.get(slug) ?? index;

    // Fraction along the whole trail, then ride the actual curve.
    const along = (ni + progress) / Math.max(1, nodes.length - 1);
    if (marker && basePath && pathLen) {
      const pt = basePath.getPointAtLength(along * pathLen);
      marker.style.transform = `translate(${pt.x}px, ${pt.y}px)`;
    } else if (marker) {
      const a = nodes[ni];
      marker.style.transform = `translate(${a.x}px, ${a.y}px)`;
    }

    // Lit came path up to current position.
    if (lit) lit.style.strokeDashoffset = String(1 - along);

    // Node states.
    if (ni !== lastIndex) {
      lastIndex = ni;
      root.style.setProperty("--stop-glass", `var(--${glass})`);
      nodes.forEach((n, i) => {
        n.el.classList.toggle("is-active", i === ni);
        n.el.classList.toggle("is-visited", i < ni);
      });
      const num = stop.getAttribute("data-order") ?? "";
      const title = stop.querySelector(".stop__title")?.textContent ?? "";
      const photoCount = stop.querySelectorAll(".frame").length;
      if (nowEl) nowEl.textContent = title;
      if (countEl) {
        countEl.textContent =
          photoCount > 0 ? `no. ${num} · ${photoCount} photos` : `no. ${num}`;
      }
    }
  }

  function onScroll() {
    if (!ticking) {
      ticking = true;
      requestAnimationFrame(update);
    }
  }

  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", onScroll, { passive: true });
  update();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
