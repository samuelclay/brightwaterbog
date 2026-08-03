// Drives the scrollytelling: reveals each stop as it enters view, and tracks the
// stop nearest the viewport center to highlight the active marker and update
// the map caption.

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
    // Track from the same line an anchor click lands on (scroll-margin-top),
    // so choosing a marker activates that stop, not the one after it.
    const refY =
      parseFloat(getComputedStyle(stops[0]).scrollMarginTop) || window.innerHeight * 0.08;

    const tops = stops.map((s) => {
      const head = (s.querySelector(".stop__head") as HTMLElement) ?? s;
      return head.getBoundingClientRect().top;
    });

    // Last head at/above the reference line, plus fractional progress toward
    // the next head. The active stop flips at the halfway point.
    let seg = 0;
    while (seg < n - 1 && tops[seg + 1] <= refY) seg++;
    let progress = 0;
    if (seg < n - 1) {
      const span = tops[seg + 1] - tops[seg];
      if (span > 0) progress = Math.min(1, Math.max(0, (refY - tops[seg]) / span));
    }
    const index = progress >= 0.5 && seg < n - 1 ? seg + 1 : seg;

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
