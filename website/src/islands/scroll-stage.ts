// Drives the scrollytelling: reveals each stop as it enters view, and tracks the
// stop nearest the viewport center to highlight the active marker and update
// the map caption. Also owns the map's three views (see global.css MAP VIEWS):
//   rail   — desktop default, the full map beside the stops
//   card   — desktop minimized: the bottom-right slice card, strips full width
//   mobile — the same card, forced under 1080px
// <html data-map-view> is set before first paint by Base.astro and kept
// current here; the desktop choice is remembered in localStorage.

type MapView = "rail" | "card" | "mobile";
const MIN_KEY = "bwb-map-min";

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

  // The card (mobile, or desktop minimized) shows only a thin slice of the
  // map, panned so the active stop's marker sits on the slice's centerline.
  const rail = document.querySelector<HTMLElement>("[data-map-rail]");
  const mapToggle = document.querySelector<HTMLButtonElement>("[data-map-toggle]");
  // Scope to the frame: the caption's toggle button holds inline icon svgs
  // that come first in DOM order, so a bare "svg" query would pan the icon.
  const svg = minimap.querySelector<SVGSVGElement>(".minimap__frame svg");
  const frame = minimap.querySelector<HTMLElement>(".minimap__frame");
  const mobileMap = window.matchMedia("(max-width: 1080px)");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  const remembered = (): MapView => {
    try {
      return localStorage.getItem(MIN_KEY) === "1" ? "card" : "rail";
    } catch {
      return "rail";
    }
  };
  const view = (): MapView =>
    (root.getAttribute("data-map-view") as MapView | null) ??
    (mobileMap.matches ? "mobile" : remembered());
  const setView = (v: MapView) => root.setAttribute("data-map-view", v);
  if (!root.hasAttribute("data-map-view")) setView(view());

  const isOpen = () => rail?.classList.contains("is-open") ?? false;
  // "Folded" = only the slice is showing (any card view, not opened).
  const isFolded = () => view() !== "rail" && !isOpen();

  // Height of the collapsed window, read from --mm-window-h so the pan math
  // can never drift from the frame height in global.css.
  const windowH = () =>
    parseFloat(getComputedStyle(minimap!).getPropertyValue("--mm-window-h")) || 0;

  function panMap() {
    if (!svg) return;
    if (!isFolded()) {
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

  // The card stays out of sight over the hero and rises into place when the
  // first stop's header reaches its top edge — so the map never overlaps
  // anything above Stargate. The rail view ignores the class (the rail is in
  // flow beside the stops there, and never reaches the hero).
  function syncRailVisibility() {
    if (!rail) return;
    const head = (stops[0].querySelector(".stop__head") as HTMLElement) ?? stops[0];
    // The card keeps its box while hidden (visibility, not display), so its
    // own top edge is a valid threshold either way. Measure the minimap card,
    // not the rail — the rail stretches the whole trail so the card can be
    // sticky inside it (pinned only between Stargate and Dam Light).
    const show = head.getBoundingClientRect().top <= minimap.getBoundingClientRect().top;
    rail.classList.toggle("is-shown", show);
    if (!show && isOpen()) setOpenRef?.(false);
  }
  let setOpenRef: ((open: boolean) => void) | null = null;

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
    syncRailVisibility();
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

  if (rail && mapToggle) {
    const syncToggle = () => {
      const v = view();
      const expanded = v === "rail" || isOpen();
      mapToggle.setAttribute("aria-expanded", String(expanded));
      mapToggle.setAttribute(
        "aria-label",
        v === "rail"
          ? "Minimize the trail map"
          : v === "card"
            ? "Restore the full trail map"
            : isOpen()
              ? "Collapse the trail map"
              : "Expand the trail map",
      );
    };
    // Card views: open the slice to the whole map, or fold it back.
    const setOpen = (open: boolean) => {
      rail.classList.toggle("is-open", open);
      syncToggle();
      panMap();
    };
    setOpenRef = setOpen; // so scrolling back up to the hero also folds it shut

    // Desktop: rail ⇄ card. A FLIP flight — measure the card where it is,
    // switch the layout, measure where it landed, then play it back from the
    // old spot to the new one (translate + the width ratio). The frame folds
    // or unfolds its height on the same curve: `auto` can't transition, so it
    // is pinned at its current pixel height for one frame first, then handed
    // the target — the CSS slice height going to the card, the full map
    // height (svg + padding + border) coming back — and released on landing.
    let flight: number | undefined;
    const land = () => {
      window.clearTimeout(flight);
      minimap.classList.remove("is-flying");
      minimap.style.transform = "";
      if (frame) {
        frame.style.height = "";
        frame.style.overflow = "";
        frame.style.transition = "";
      }
    };
    const setCard = (card: boolean) => {
      if (mobileMap.matches) return;
      const target: MapView = card ? "card" : "rail";
      if (view() === target) return;
      land();
      rail.classList.remove("is-open");
      const animate = !reduceMotion.matches && frame && svg;
      const first = minimap.getBoundingClientRect();
      const frameH = frame?.offsetHeight ?? 0;
      setView(target);
      try {
        localStorage.setItem(MIN_KEY, card ? "1" : "0");
      } catch {}
      syncRailVisibility(); // the card hides itself above the trail
      if (animate) {
        // pin without animating: the frame's height transition is live in
        // both layouts, and would otherwise chase the pin instead of the target
        frame!.style.transition = "none";
        frame!.style.height = `${frameH}px`;
        frame!.style.overflow = "hidden";
        const last = minimap.getBoundingClientRect();
        // the unfolded height, measured now — before the inverse transform,
        // which would scale every descendant rect along with the card
        const cs = getComputedStyle(frame!);
        const fullH =
          svg!.getBoundingClientRect().height +
          parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom) +
          parseFloat(cs.borderTopWidth) + parseFloat(cs.borderBottomWidth);
        minimap.style.transform =
          `translate(${(first.left - last.left).toFixed(1)}px, ${(first.top - last.top).toFixed(1)}px)` +
          ` scale(${(first.width / last.width).toFixed(4)})`;
        void minimap.offsetWidth; // commit the start state before the flight
        frame!.style.transition = "";
        minimap.classList.add("is-flying");
        minimap.style.transform = "";
        // fold to the CSS slice height, or unfold to the full map
        frame!.style.height = card ? "" : `${fullH}px`;
        flight = window.setTimeout(land, 520);
      }
      syncToggle();
      panMap();
    };

    mapToggle.addEventListener("click", () => {
      const v = view();
      if (v === "mobile") setOpen(!isOpen());
      else setCard(v === "rail");
    });
    // While folded the whole card is one big open affordance — the 35px strip
    // is too thin to aim a marker at anyway, so a tap anywhere on it opens
    // instead of jumping to whatever stop happened to be under your thumb.
    // Capture, so it runs before the markers' own click handlers; the header
    // control keeps its own job.
    rail.addEventListener(
      "click",
      (e) => {
        if (!isFolded() || mapToggle.contains(e.target as Node)) return;
        e.preventDefault();
        e.stopPropagation();
        setOpen(true);
      },
      true,
    );
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
    // Crossing the breakpoint: phones are always the card; back on desktop
    // the remembered choice returns. No flight either way.
    mobileMap.addEventListener("change", () => {
      land();
      rail.classList.remove("is-open");
      setView(mobileMap.matches ? "mobile" : remembered());
      syncToggle();
      syncRailVisibility();
      panMap();
    });
    syncToggle();
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
