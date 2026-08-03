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
  // Two number badges: the map caption and the mobile cabochon toggle.
  const numEls = Array.from(document.querySelectorAll<HTMLElement>("[data-current-number]"));
  const setNum = (v: string) => numEls.forEach((el) => (el.textContent = v));
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

  // Mobile: the floating card shows only a slice of the map, panned so the
  // active stop's marker stays centered in the window (clamped at the ends).
  const rail = document.querySelector<HTMLElement>("[data-map-rail]");
  const mapToggle = document.querySelector<HTMLButtonElement>("[data-map-toggle]");
  const svg = minimap.querySelector<SVGSVGElement>("svg");
  const mobileMap = window.matchMedia("(max-width: 1080px)");
  const isOpen = () => rail?.classList.contains("is-open") ?? false;

  // Height of the collapsed window, read from --mm-window-h so the pan math
  // can never drift from the frame height in global.css.
  const windowH = () =>
    parseFloat(getComputedStyle(minimap!).getPropertyValue("--mm-window-h")) || 0;

  function panMap() {
    if (!svg) return;
    if (!mobileMap.matches || isOpen()) {
      svg.style.transform = "";
      return;
    }
    const dot = minimap!.querySelector<SVGElement>(".mm-marker.is-active .mm-dot");
    if (!dot) return; // indoor sections keep the last trail position
    const svgH = svg.getBoundingClientRect().height;
    const winH = windowH();
    if (!svgH || !winH) return;
    // Put the active dot on the window's centerline. The clamp only keeps the
    // map from sliding past its own edges; with a window this short it never
    // engages, so the dot stays centered end to end.
    const dotY = (parseFloat(dot.getAttribute("cy") ?? "0") / svg.viewBox.baseVal.height) * svgH;
    const shift = Math.min(Math.max(dotY - winH / 2, 0), Math.max(svgH - winH, 0));
    svg.style.transform = `translateY(${(-shift).toFixed(2)}px)`;
  }

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
      setNum(order);
    }
    panMap();
  }

  function onScroll() {
    if (!ticking) {
      ticking = true;
      requestAnimationFrame(update);
    }
  }

  // Hovering (or keyboard-focusing) a marker previews that sculpture's name in
  // the caption, so you can tell what you're clicking on; leaving restores the
  // scroll-tracked stop.
  markers.forEach((mk) => {
    const preview = () => {
      if (nowEl) nowEl.textContent = mk.getAttribute("data-title") ?? "";
      setNum(mk.getAttribute("data-order") ?? "");
    };
    const restore = () => {
      lastIndex = -1; // force the caption to re-sync on the next update
      update();
    };
    mk.addEventListener("mouseenter", preview);
    mk.addEventListener("focus", preview);
    mk.addEventListener("mouseleave", restore);
    mk.addEventListener("blur", restore);
  });

  // Expand to the full map from the header control; picking a stop (or
  // tapping outside / Esc) folds it back to the moving window.
  if (rail && mapToggle) {
    const setOpen = (open: boolean) => {
      rail.classList.toggle("is-open", open);
      mapToggle.setAttribute("aria-expanded", String(open));
      mapToggle.setAttribute(
        "aria-label",
        open ? "Collapse the trail map" : "Expand the trail map",
      );
      mapToggle.textContent = open ? "✕" : "⤢";
      panMap();
    };
    mapToggle.addEventListener("click", () => setOpen(!isOpen()));
    markers.forEach((mk) =>
      mk.addEventListener("click", () => {
        // Adopt the tapped stop straight away so the map folds back around the
        // marker you picked, rather than panning to the old one and then
        // chasing the smooth scroll. The scroll tracker re-syncs on landing.
        markers.forEach((m) => m.classList.toggle("is-active", m === mk));
        if (nowEl) nowEl.textContent = mk.getAttribute("data-title") ?? "";
        setNum(mk.getAttribute("data-order") ?? "");
        lastIndex = -1;
        setOpen(false);
      }),
    );
    // pointerdown, not click: iOS Safari withholds click on plain elements,
    // which would leave the map stuck open after a tap on the page behind it.
    document.addEventListener("pointerdown", (e) => {
      if (isOpen() && !rail.contains(e.target as Node)) setOpen(false);
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && isOpen()) setOpen(false);
    });
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
