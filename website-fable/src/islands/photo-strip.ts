// Per-strip segmented progress bar. As you scroll a strip horizontally through
// Then / Now / Construction, each segment fills; the current segment is marked
// active; clicking a segment scrolls the strip to that group.

function setup(strip: HTMLElement) {
  const track = strip.querySelector<HTMLElement>("[data-strip-track]");
  const segs = Array.from(strip.querySelectorAll<HTMLElement>("[data-seg]"));
  if (!track || segs.length < 2) return;

  const groups = segs
    .map((seg) => ({
      seg,
      fill: seg.querySelector<HTMLElement>("[data-seg-fill]"),
      el: track.querySelector<HTMLElement>(`[data-group="${seg.dataset.seg}"]`),
    }))
    .filter((g) => g.el);

  function relLeft(el: HTMLElement) {
    return el.getBoundingClientRect().left - track!.getBoundingClientRect().left + track!.scrollLeft;
  }

  let ticking = false;
  function update() {
    ticking = false;
    // Interpolate a read head across the whole scrollable range: at scroll 0 it
    // sits at the LEFT edge of the first photo, at max scroll at the RIGHT edge
    // of the last — so the final segment reaches 100% even when the last photos
    // can't scroll to the viewport center.
    const maxScroll = track!.scrollWidth - track!.clientWidth;
    const firstLeft = relLeft(groups[0].el!);
    const lastEl = groups[groups.length - 1].el!;
    const lastRight = relLeft(lastEl) + lastEl.offsetWidth;
    const span = Math.max(1, lastRight - firstLeft);
    const p = maxScroll > 0 ? track!.scrollLeft / maxScroll : 1;
    const head = firstLeft + p * span;

    let active = 0;
    let bestStart = -Infinity;
    groups.forEach((g, i) => {
      const start = relLeft(g.el!);
      const width = g.el!.offsetWidth;
      const ratio = Math.min(1, Math.max(0, (head - start) / Math.max(1, width)));
      if (g.fill) g.fill.style.width = `${ratio * 100}%`;
      if (start <= head + 0.5 && start > bestStart) {
        bestStart = start;
        active = i;
      }
    });
    groups.forEach((g, i) => g.seg.classList.toggle("is-active", i === active));
  }

  function onScroll() {
    if (!ticking) {
      ticking = true;
      requestAnimationFrame(update);
    }
  }

  track.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", onScroll, { passive: true });

  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  groups.forEach((g) => {
    g.seg.addEventListener("click", () => {
      const left = relLeft(g.el!);
      track.scrollTo({ left, behavior: reduce ? "auto" : "smooth" });
    });
  });

  update();
}

function init() {
  document.querySelectorAll<HTMLElement>("[data-strip]").forEach(setup);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
