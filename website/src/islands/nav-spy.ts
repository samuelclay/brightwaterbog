// Scroll spy for the header nav: a single pill slides to sit behind whichever
// section you're looking at. Nothing is highlighted over the hero — the pill
// fades in when the trail begins, the same moment the map card appears.

function init() {
  const nav = document.querySelector<HTMLElement>("[data-navspy]");
  const pill = nav?.querySelector<HTMLElement>("[data-nav-pill]");
  if (!nav || !pill) return;

  const pairs = Array.from(nav.querySelectorAll<HTMLAnchorElement>('a[href^="#"]'))
    .map((link) => ({ link, section: document.getElementById(link.hash.slice(1)) }))
    .filter((p): p is { link: HTMLAnchorElement; section: HTMLElement } => p.section !== null);
  if (!pairs.length) return;

  const topbar = document.querySelector<HTMLElement>(".topbar");
  // A section takes over once its top rises past this line — near enough to the
  // top that it agrees with where an anchor click lands.
  const LINE = 0.28;

  function activeIndex() {
    const vh = window.innerHeight;

    // base rule: the last section whose top has crossed the line
    let idx = -1;
    for (let i = 0; i < pairs.length; i++) {
      if (pairs[i].section.getBoundingClientRect().top <= vh * LINE) idx = i;
    }

    // Visit is short and sits at the very bottom, so on a tall window its top
    // never climbs to that line — the page runs out of scroll first. Two
    // catches, both looking only *past* the base pick so an earlier section
    // can't steal the highlight back:
    //   1. a later section sitting entirely on screen wins outright — the
    //      moment you can see all of Visit, it's what you're looking at;
    //   2. hitting the bottom of the document always selects the last section,
    //      for the short windows where Visit is too tall to fully fit.
    const headerBottom = topbar?.getBoundingClientRect().bottom ?? 0;
    for (let i = pairs.length - 1; i > idx; i--) {
      const r = pairs[i].section.getBoundingClientRect();
      if (r.top >= headerBottom && r.bottom <= vh) {
        idx = i;
        break;
      }
    }
    const doc = document.documentElement;
    if (window.scrollY + vh >= doc.scrollHeight - 2) idx = pairs.length - 1;

    return idx;
  }

  function movePill(i: number) {
    if (i < 0) {
      nav!.classList.remove("is-spying");
      return;
    }
    const { link } = pairs[i];
    pill!.style.width = `${link.offsetWidth}px`;
    pill!.style.height = `${link.offsetHeight}px`;
    pill!.style.transform = `translate(${link.offsetLeft}px, -50%)`;
    nav!.classList.add("is-spying");
  }

  let current = -1;
  let ticking = false;

  function update() {
    ticking = false;
    const i = activeIndex();
    if (i === current) return;
    current = i;
    pairs.forEach((p, n) =>
      n === i
        ? p.link.setAttribute("aria-current", "location")
        : p.link.removeAttribute("aria-current"),
    );
    movePill(i);
  }

  function onScroll() {
    if (!ticking) {
      ticking = true;
      requestAnimationFrame(update);
    }
  }

  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", () => {
    movePill(current); // the links reflow; the pill has to follow
    onScroll();
  });
  // Fonts land after first paint and change every link's width.
  document.fonts?.ready.then(() => movePill(current));
  update();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
