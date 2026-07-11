// Fullscreen photo viewer with pinch/wheel zoom, pan, swipe between photos, and
// dismiss on tap / swipe-away / Esc. Works with mouse, touch, and trackpad.

interface Item {
  full: string;
  hi: string;
  w: number;
  h: number;
}

function init() {
  const root = document.querySelector<HTMLElement>("[data-lightbox-root]");
  const stage = document.querySelector<HTMLElement>("[data-lightbox-stage]");
  const img = document.querySelector<HTMLImageElement>("[data-lightbox-img]");
  if (!root || !stage || !img) return;

  const btnClose = document.querySelector<HTMLButtonElement>("[data-lightbox-close]");
  const btnPrev = document.querySelector<HTMLButtonElement>("[data-lightbox-prev]");
  const btnNext = document.querySelector<HTMLButtonElement>("[data-lightbox-next]");

  let items: Item[] = [];
  let index = 0;
  let scale = 1;
  let tx = 0;
  let ty = 0;
  let hiLoaded = false;

  const MIN = 1;
  const MAX = 6;
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  let scrollY = 0;
  function lockScroll() {
    scrollY = window.scrollY;
    document.body.style.position = "fixed";
    document.body.style.top = `-${scrollY}px`;
    document.body.style.width = "100%";
  }
  function unlockScroll() {
    document.body.style.position = "";
    document.body.style.top = "";
    document.body.style.width = "";
    window.scrollTo(0, scrollY);
  }

  function apply() {
    img.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`;
    root.classList.toggle("is-zoomed", scale > 1.02);
  }
  function resetTransform() {
    scale = 1;
    tx = 0;
    ty = 0;
    img.style.transition = "none";
    apply();
  }

  function clampPan() {
    const rect = stage.getBoundingClientRect();
    const iw = img.clientWidth * scale;
    const ih = img.clientHeight * scale;
    const maxX = Math.max(0, (iw - rect.width) / 2 + 40);
    const maxY = Math.max(0, (ih - rect.height) / 2 + 40);
    tx = Math.min(maxX, Math.max(-maxX, tx));
    ty = Math.min(maxY, Math.max(-maxY, ty));
  }

  function render(i: number) {
    index = (i + items.length) % items.length;
    const item = items[index];
    hiLoaded = false;
    resetTransform();
    img.src = item.full;
    // Upgrade to the high-res source in the background.
    const hi = new Image();
    hi.onload = () => {
      if (items[index] === item) {
        img.src = item.hi;
        hiLoaded = true;
      }
    };
    hi.src = item.hi;
    const single = items.length < 2;
    if (btnPrev) btnPrev.disabled = single;
    if (btnNext) btnNext.disabled = single;
  }

  function open(list: Item[], start: number) {
    if (!list.length) return;
    items = list;
    lockScroll();
    root.hidden = false;
    root.setAttribute("aria-hidden", "false");
    requestAnimationFrame(() => root.classList.add("is-open"));
    render(start);
    document.addEventListener("keydown", onKey);
  }

  function close() {
    root.classList.remove("is-open");
    document.removeEventListener("keydown", onKey);
    const done = () => {
      root.hidden = true;
      root.setAttribute("aria-hidden", "true");
      unlockScroll();
      img.src = "";
      img.style.opacity = "1";
    };
    if (reduce) done();
    else setTimeout(done, 260);
  }

  function zoomAt(px: number, py: number, next: number) {
    const target = Math.min(MAX, Math.max(MIN, next));
    const rect = img.getBoundingClientRect();
    const cx = px - (rect.left + rect.width / 2);
    const cy = py - (rect.top + rect.height / 2);
    const f = target / scale;
    tx -= cx * (f - 1);
    ty -= cy * (f - 1);
    scale = target;
    if (scale <= 1.02) {
      scale = 1;
      tx = 0;
      ty = 0;
    } else {
      clampPan();
    }
    img.style.transition = reduce ? "none" : "transform 0.12s ease-out";
    apply();
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === "Escape") close();
    else if (e.key === "ArrowRight") render(index + 1);
    else if (e.key === "ArrowLeft") render(index - 1);
  }

  // ---- pointer gestures (mouse + touch unified) ----
  const pts = new Map<number, { x: number; y: number }>();
  let startX = 0;
  let startY = 0;
  let baseTx = 0;
  let baseTy = 0;
  let moved = false;
  let pinchStartDist = 0;
  let pinchStartScale = 1;
  let mode: "none" | "pan" | "swipe" | "dismiss" = "none";

  const dist = (a: { x: number; y: number }, b: { x: number; y: number }) =>
    Math.hypot(a.x - b.x, a.y - b.y);
  const mid = (a: { x: number; y: number }, b: { x: number; y: number }) => ({
    x: (a.x + b.x) / 2,
    y: (a.y + b.y) / 2,
  });

  stage.addEventListener("pointerdown", (e) => {
    stage.setPointerCapture(e.pointerId);
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    moved = false;
    if (pts.size === 2) {
      const [a, b] = [...pts.values()];
      pinchStartDist = dist(a, b);
      pinchStartScale = scale;
      mode = "pan";
    } else {
      startX = e.clientX;
      startY = e.clientY;
      baseTx = tx;
      baseTy = ty;
      mode = scale > 1.02 ? "pan" : "none";
    }
  });

  stage.addEventListener("pointermove", (e) => {
    if (!pts.has(e.pointerId)) return;
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY });

    if (pts.size === 2) {
      const [a, b] = [...pts.values()];
      const m = mid(a, b);
      const d = dist(a, b);
      if (pinchStartDist > 0) zoomAt(m.x, m.y, pinchStartScale * (d / pinchStartDist));
      moved = true;
      return;
    }

    const dx = e.clientX - startX;
    const dy = e.clientY - startY;
    if (Math.abs(dx) > 6 || Math.abs(dy) > 6) moved = true;

    if (scale > 1.02) {
      tx = baseTx + dx;
      ty = baseTy + dy;
      clampPan();
      img.style.transition = "none";
      apply();
      return;
    }

    // Not zoomed: decide swipe (horizontal) vs dismiss (vertical).
    if (mode === "none" && moved) {
      mode = Math.abs(dx) > Math.abs(dy) ? "swipe" : "dismiss";
    }
    if (mode === "swipe") {
      img.style.transition = "none";
      img.style.transform = `translateX(${dx}px)`;
    } else if (mode === "dismiss") {
      img.style.transition = "none";
      img.style.transform = `translateY(${dy}px)`;
      img.style.opacity = String(Math.max(0.2, 1 - Math.abs(dy) / 400));
    }
  });

  function endPointer(e: PointerEvent) {
    pts.delete(e.pointerId);
    if (pts.size >= 1) return; // still pinching/panning

    const dx = e.clientX - startX;
    const dy = e.clientY - startY;

    if (scale > 1.02) {
      if (!moved) resetTransform(); // tap while zoomed → zoom back out (stays open)
      mode = "none";
      return;
    }

    if (mode === "swipe") {
      if (dx < -60) render(index + 1);
      else if (dx > 60) render(index - 1);
      snapBack();
    } else if (mode === "dismiss") {
      if (Math.abs(dy) > 90) close();
      else snapBack();
    } else if (!moved) {
      // Clean tap: on the image → zoom in at that point; on the backdrop → close.
      const r = img.getBoundingClientRect();
      const onImage =
        e.clientX >= r.left && e.clientX <= r.right &&
        e.clientY >= r.top && e.clientY <= r.bottom;
      if (onImage) zoomAt(e.clientX, e.clientY, 2.6);
      else close();
    }
    mode = "none";
  }

  function snapBack() {
    img.style.transition = reduce ? "none" : "transform 0.25s ease, opacity 0.25s ease";
    img.style.transform = scale > 1.02 ? `translate(${tx}px,${ty}px) scale(${scale})` : "";
    img.style.opacity = "1";
  }

  stage.addEventListener("pointerup", endPointer);
  stage.addEventListener("pointercancel", endPointer);

  // Wheel / trackpad zoom (desktop).
  stage.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      const step = e.deltaY < 0 ? 1.18 : 1 / 1.18;
      zoomAt(e.clientX, e.clientY, scale * step);
    },
    { passive: false },
  );

  btnClose?.addEventListener("click", close);
  btnPrev?.addEventListener("click", () => render(index - 1));
  btnNext?.addEventListener("click", () => render(index + 1));

  // ---- wire up triggers ----
  function itemsFromStrip(track: Element): Item[] {
    return Array.from(track.querySelectorAll<HTMLElement>("[data-lightbox]")).map((el) => ({
      full: el.dataset.full ?? "",
      hi: el.dataset.fullHi ?? el.dataset.full ?? "",
      w: Number(el.dataset.w) || 0,
      h: Number(el.dataset.h) || 0,
    }));
  }

  document.addEventListener("click", (e) => {
    const frame = (e.target as HTMLElement).closest<HTMLElement>("[data-lightbox]");
    if (frame) {
      const track = frame.closest("[data-strip-track]");
      if (!track) return;
      const list = itemsFromStrip(track);
      const start = Array.from(track.querySelectorAll("[data-lightbox]")).indexOf(frame);
      open(list, Math.max(0, start));
      return;
    }
    const grid = (e.target as HTMLElement).closest<HTMLElement>("[data-gallery-json]");
    if (grid) {
      try {
        const list = JSON.parse(grid.dataset.galleryJson ?? "[]") as Item[];
        open(list, 0);
      } catch (_) {}
    }
  });

  // Click on the dim backdrop (outside the image) closes.
  root.addEventListener("pointerdown", (e) => {
    if (e.target === root) close();
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
