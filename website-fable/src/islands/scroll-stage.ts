// Drives the scrollytelling: reveals each stop as it enters view, and tracks the
// stop nearest the viewport center to fill the trail's progress path, highlight
// the active marker, and update the map caption.

function init() {
  const stage = document.querySelector<HTMLElement>("[data-scrollstage]");
  const minimap = document.querySelector<HTMLElement>("[data-minimap]");
  if (!stage || !minimap) return;

  const stops = Array.from(stage.querySelectorAll<HTMLElement>("[data-stop]"));
  if (!stops.length) return;

  const markers = Array.from(minimap.querySelectorAll<SVGElement>("[data-node]"));
  const nowEl = minimap.querySelector<HTMLElement>("[data-now]");
  const numEl = minimap.querySelector<HTMLElement>("[data-current-number]");
  const root = document.documentElement;
  const n = stops.length;

  // progressive reveal
  const reveal = new IntersectionObserver(
    (entries) => {
      for (const e of entries) if (e.isIntersecting) e.target.classList.add("is-active");
    },
    { threshold: 0.3 },
  );
  stops.forEach((s) => reveal.observe(s));

  let ticking = false;
  let lastIndex = -1;

  function update() {
    ticking = false;
    const centerY = window.innerHeight * 0.42;

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

    let progress = 0;
    if (index < n - 1) {
      const span = tops[index + 1] - tops[index];
      if (span > 0) progress = Math.min(1, Math.max(0, (centerY - tops[index]) / span));
    }

    const along = (index + progress) / Math.max(1, n - 1);
    root.style.setProperty("--route-progress", along.toFixed(4));

    if (index !== lastIndex) {
      lastIndex = index;
      const stop = stops[index];
      const slug = stop.getAttribute("data-slug") ?? "";
      const glass = stop.getAttribute("data-glass") ?? "amber";
      root.style.setProperty("--stop-glass", `var(--${glass})`);
      markers.forEach((mk) => mk.classList.toggle("is-active", mk.getAttribute("data-node") === slug));
      const title = stop.querySelector(".stop__title")?.textContent ?? "";
      const order = stop.getAttribute("data-order") ?? String(index + 1);
      if (nowEl) nowEl.textContent = title;
      if (numEl) numEl.textContent = order;
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
