// Fullscreen photo viewer with pinch/wheel zoom, pan, carousel swipe between
// photos, and dismiss on tap / swipe-away / Esc. Works with mouse, touch, and
// trackpad.

interface Item {
  full: string;
  hi: string;
  zoom: string;
  w: number;
  h: number;
  era: string;
}

function init() {
  const root = document.querySelector<HTMLElement>("[data-lightbox-root]");
  const stage = document.querySelector<HTMLElement>("[data-lightbox-stage]");
  const img = document.querySelector<HTMLImageElement>("[data-lightbox-img]");
  if (!root || !stage || !img) return;

  const eraPill = document.querySelector<HTMLElement>("[data-lightbox-era]");
  const btnClose = document.querySelector<HTMLButtonElement>("[data-lightbox-close]");
  const btnPrev = document.querySelector<HTMLButtonElement>("[data-lightbox-prev]");
  const btnNext = document.querySelector<HTMLButtonElement>("[data-lightbox-next]");

  let items: Item[] = [];
  let index = 0;
  let scale = 1;
  let tx = 0;
  let ty = 0;
  let hiLoaded = false;
  let zoomRequested = false;
  let zoomApplied = false;
  let sliding = false;

  // Neighbor photo element shown during carousel swipes/slides.
  let peer: HTMLImageElement | null = null;
  let peerDir: 1 | -1 = 1;

  const MIN = 1;
  const MAX = 6;
  const SLIDE_MS = 320;
  const SLIDE_EASE = "cubic-bezier(0.22, 0.61, 0.36, 1)";
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  let scrollY = 0;
  function lockScroll() {
    scrollY = window.scrollY;
    // Fixing the body removes the page scrollbar; pad by its width so the
    // page doesn't shift wider behind the lightbox and snap back on close.
    const gutter = window.innerWidth - document.documentElement.clientWidth;
    document.body.style.position = "fixed";
    document.body.style.top = `-${scrollY}px`;
    document.body.style.width = "100%";
    document.body.style.paddingRight = gutter > 0 ? `${gutter}px` : "";
  }
  function unlockScroll() {
    document.body.style.position = "";
    document.body.style.top = "";
    document.body.style.width = "";
    document.body.style.paddingRight = "";
    // Suspend html { scroll-behavior: smooth } for the restore, which
    // otherwise animates from the top of the page on every close.
    const html = document.documentElement;
    const prevBehavior = html.style.scrollBehavior;
    html.style.scrollBehavior = "auto";
    window.scrollTo(0, scrollY);
    html.style.scrollBehavior = prevBehavior;
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
  function resetZoomAnimated() {
    scale = 1;
    tx = 0;
    ty = 0;
    img.style.transition = reduce ? "none" : "transform 0.3s ease";
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

  const stageWidth = () => stage.getBoundingClientRect().width;

  function upgradeHi(item: Item) {
    const hi = new Image();
    hi.onload = () => {
      if (items[index] === item && !zoomApplied) {
        img.src = item.hi;
        hiLoaded = true;
      }
    };
    hi.src = item.hi;
  }

  // The 2600px tier runs out of pixels fast once zoomed on a hi-DPR phone.
  // The first zoom gesture on each photo fetches the near-original-width
  // render and swaps it in after decode, so the swap never paints a flash.
  function upgradeZoom() {
    if (zoomRequested) return;
    zoomRequested = true;
    const item = items[index];
    if (!item?.zoom || item.zoom === item.hi) return;
    const z = new Image();
    z.onload = () => {
      const swap = () => {
        if (items[index] === item) {
          img.src = item.zoom;
          zoomApplied = true;
        }
      };
      z.decode ? z.decode().then(swap, swap) : swap();
    };
    z.src = item.zoom;
  }

  function removePeer() {
    peer?.remove();
    peer = null;
  }

  // Preload every photo in the carousel as soon as it opens — nearest
  // neighbors first, a few at a time — so slides never animate in empty.
  const preloadedUrls = new Set<string>();
  let preloadToken = 0;
  function preloadAll(list: Item[], start: number) {
    const token = ++preloadToken;
    const n = list.length;
    const seen = new Set([start]);
    const seq: number[] = [];
    for (let d = 1; seen.size < n; d++) {
      for (const idx of [(start + d) % n, (start - d + n) % n]) {
        if (!seen.has(idx)) {
          seen.add(idx);
          seq.push(idx);
        }
      }
    }
    let i = 0;
    const next = () => {
      if (token !== preloadToken) return;
      while (i < seq.length && preloadedUrls.has(list[seq[i]].full)) i++;
      if (i >= seq.length) return;
      const url = list[seq[i++]].full;
      const im = new Image();
      im.onload = () => {
        preloadedUrls.add(url);
        next();
      };
      im.onerror = next;
      im.src = url;
    };
    for (let k = 0; k < 3; k++) next();
  }

  // Create (or keep) the neighbor photo sitting one stage-width away in the
  // given direction, ready to slide in.
  function ensurePeer(dir: 1 | -1) {
    if (peer && peerDir === dir) return;
    removePeer();
    const it = items[(index + dir + items.length) % items.length];
    const g = document.createElement("img");
    g.className = img.className;
    g.alt = "";
    g.src = it.full;
    g.style.transition = "none";
    g.style.transform = `translateX(${dir * stageWidth()}px)`;
    stage.appendChild(g);
    peer = g;
    peerDir = dir;
  }

  // The pill sits inside the photo's bottom-left corner (like the thumbnail
  // pill) and rides along with it — swipes, slides, zoom, dismiss drags. The
  // photo moves via transforms and CSS transitions, so the only way to stay
  // glued to it is to re-read its box every frame while the lightbox is open.
  let eraRaf = 0;
  function placeEra() {
    if (!eraPill || eraPill.hidden) return;
    const r = img.getBoundingClientRect();
    eraPill.style.left = `${r.left + 8}px`;
    eraPill.style.top = `${r.bottom - eraPill.offsetHeight - 8}px`;
    // Fade with the photo on dismiss drags, but leave the inline opacity
    // unset otherwise so the .is-zoomed CSS fade still wins.
    const o = img.style.opacity;
    eraPill.style.opacity = o && o !== "1" ? o : "";
  }
  function startEraTracking() {
    cancelAnimationFrame(eraRaf);
    const loop = () => {
      placeEra();
      eraRaf = requestAnimationFrame(loop);
    };
    eraRaf = requestAnimationFrame(loop);
  }
  function stopEraTracking() {
    cancelAnimationFrame(eraRaf);
  }
  function setEra(item: Item) {
    if (!eraPill) return;
    eraPill.textContent = item.era;
    eraPill.hidden = !item.era;
  }

  function render(i: number) {
    index = (i + items.length) % items.length;
    const item = items[index];
    hiLoaded = false;
    zoomRequested = false;
    zoomApplied = false;
    sliding = false;
    removePeer();
    resetTransform();
    setEra(item);
    img.style.opacity = "1";
    img.src = item.full;
    upgradeHi(item);
    const single = items.length < 2;
    if (btnPrev) btnPrev.disabled = single;
    if (btnNext) btnNext.disabled = single;
  }

  // Carousel slide: the current photo exits one way while the neighbor comes
  // in from the other side, continuing from wherever a swipe dragged them.
  function slideTo(dir: 1 | -1) {
    if (sliding || items.length < 2) return;
    sliding = true;
    const width = stageWidth();
    const nextIndex = (index + dir + items.length) % items.length;
    const item = items[nextIndex];
    ensurePeer(dir);
    const p = peer!;
    p.getBoundingClientRect(); // commit the start position before animating
    const t = reduce ? "none" : `transform ${SLIDE_MS}ms ${SLIDE_EASE}`;
    img.style.transition = t;
    p.style.transition = t;
    img.style.transform = `translateX(${-dir * width}px)`;
    p.style.transform = "translateX(0px)";
    const finish = () => {
      index = nextIndex;
      hiLoaded = false;
      zoomRequested = false;
      zoomApplied = false;
      resetTransform();
      setEra(item);
      // The peer keeps covering the stage until the new src is decoded, so
      // the swap from peer to the canonical img never flashes.
      img.style.opacity = "0";
      img.src = item.full;
      let revealed = false;
      const reveal = () => {
        if (revealed) return;
        revealed = true;
        img.style.opacity = "1";
        if (peer === p) peer = null;
        p.remove();
        sliding = false;
        upgradeHi(item);
      };
      img.decode().then(reveal, reveal);
      // decode() is paint-driven and can stall (e.g. hidden tabs) — the peer
      // already showed this src, so revealing without it is safe.
      setTimeout(reveal, 400);
    };
    if (reduce) finish();
    else setTimeout(finish, SLIDE_MS + 20);
  }

  // iOS Safari pinch-zooms the page itself even over touch-action: none, so a
  // pinch on a photo zoomed both the photo and the page. While the lightbox is
  // open, swallow the browser's own zoom gestures at the document level —
  // gesturestart/change are Safari's proprietary pinch events, and the
  // multi-touch touchmove guard covers the rest. Page zoom stays available
  // everywhere outside the lightbox.
  const stopNativeGesture = (e: Event) => e.preventDefault();
  const stopMultiTouch = (e: TouchEvent) => {
    if (e.touches.length > 1) e.preventDefault();
  };
  function blockNativeZoom() {
    document.addEventListener("gesturestart" as "wheel", stopNativeGesture);
    document.addEventListener("gesturechange" as "wheel", stopNativeGesture);
    document.addEventListener("touchmove", stopMultiTouch, { passive: false });
  }
  function unblockNativeZoom() {
    document.removeEventListener("gesturestart" as "wheel", stopNativeGesture);
    document.removeEventListener("gesturechange" as "wheel", stopNativeGesture);
    document.removeEventListener("touchmove", stopMultiTouch);
  }

  function open(list: Item[], start: number) {
    if (!list.length) return;
    items = list;
    lockScroll();
    blockNativeZoom();
    root.hidden = false;
    root.setAttribute("aria-hidden", "false");
    requestAnimationFrame(() => root.classList.add("is-open"));
    render(start);
    preloadAll(list, index);
    startEraTracking();
    document.addEventListener("keydown", onKey);
  }

  function close() {
    preloadToken++; // stop launching new preloads once closed
    unblockNativeZoom();
    stopEraTracking();
    if (eraPill) eraPill.hidden = true;
    root.classList.remove("is-open");
    document.removeEventListener("keydown", onKey);
    const done = () => {
      root.hidden = true;
      root.setAttribute("aria-hidden", "true");
      unlockScroll();
      removePeer();
      sliding = false;
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
    if (scale > 1.02) upgradeZoom();
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === "Escape") close();
    else if (e.key === "ArrowRight") slideTo(1);
    else if (e.key === "ArrowLeft") slideTo(-1);
  }

  // ---- pointer gestures (mouse + touch unified) ----
  const pts = new Map<number, { x: number; y: number }>();
  let startX = 0;
  let startY = 0;
  let baseTx = 0;
  let baseTy = 0;
  let moved = false;
  let pinched = false; // two fingers landed during this gesture — never a tap
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
    if (sliding) return;
    try {
      stage.setPointerCapture(e.pointerId);
    } catch (_) {} // synthetic or already-released pointers can't be captured
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pts.size === 2) {
      const [a, b] = [...pts.values()];
      pinchStartDist = dist(a, b);
      pinchStartScale = scale;
      mode = "pan";
      pinched = true;
      moved = true; // two fingers is a pinch, never the start of a tap
    } else {
      startX = e.clientX;
      startY = e.clientY;
      baseTx = tx;
      baseTy = ty;
      moved = false;
      pinched = false;
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
      // Carousel drag: the photo follows the finger and the neighbor for the
      // current drag direction rides along one stage-width away.
      const dir: 1 | -1 = dx <= 0 ? 1 : -1;
      ensurePeer(dir);
      img.style.transition = "none";
      img.style.transform = `translateX(${dx}px)`;
      peer!.style.transition = "none";
      peer!.style.transform = `translateX(${dx + dir * stageWidth()}px)`;
    } else if (mode === "dismiss") {
      img.style.transition = "none";
      img.style.transform = `translateY(${dy}px)`;
      img.style.opacity = String(Math.max(0.2, 1 - Math.abs(dy) / 400));
    }
  });

  function endPointer(e: PointerEvent) {
    if (!pts.has(e.pointerId)) return;
    pts.delete(e.pointerId);
    if (pts.size === 1) {
      // Pinch is ending: one finger lifted, one still down. Re-baseline the
      // survivor so its next moves pan from where the pinch left off instead
      // of jumping against the pre-pinch start point.
      const [rest] = [...pts.values()];
      startX = rest.x;
      startY = rest.y;
      baseTx = tx;
      baseTy = ty;
      return;
    }
    if (pts.size >= 1) return; // still pinching/panning

    const wasPinch = pinched;
    pinched = false;
    const dx = e.clientX - startX;
    const dy = e.clientY - startY;

    if (scale > 1.02) {
      // Tap while zoomed animates back out — but only a real tap; the end of
      // a pinch (or the pan after one) must never re-trigger zoom steps.
      if (!moved && !wasPinch) resetZoomAnimated();
      mode = "none";
      return;
    }
    if (wasPinch) {
      // Pinched back out to 1x: the gesture is over, not a tap or swipe.
      mode = "none";
      return;
    }

    if (mode === "swipe") {
      if (dx < -60) slideTo(1);
      else if (dx > 60) slideTo(-1);
      else snapBack();
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
    const t = reduce ? "none" : "transform 0.25s ease, opacity 0.25s ease";
    img.style.transition = t;
    img.style.transform = scale > 1.02 ? `translate(${tx}px,${ty}px) scale(${scale})` : "";
    img.style.opacity = "1";
    if (peer) {
      const p = peer;
      const d = peerDir;
      peer = null;
      p.style.transition = t;
      p.style.transform = `translateX(${d * stageWidth()}px)`;
      setTimeout(() => p.remove(), 300);
    }
  }

  stage.addEventListener("pointerup", endPointer);
  stage.addEventListener("pointercancel", endPointer);

  // Wheel / trackpad zoom (desktop). Scale by the actual scroll delta rather
  // than a fixed step per event — trackpads fire dozens of small events per
  // gesture, which a fixed step turns into an instant jump to MAX. Trackpad
  // pinch arrives as wheel events with ctrlKey set and needs a finer touch.
  stage.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      if (sliding) return;
      const dy = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY; // lines → px
      const factor = Math.exp(-dy * (e.ctrlKey ? 0.012 : 0.0022));
      zoomAt(e.clientX, e.clientY, scale * factor);
    },
    { passive: false },
  );

  btnClose?.addEventListener("click", close);
  btnPrev?.addEventListener("click", () => slideTo(-1));
  btnNext?.addEventListener("click", () => slideTo(1));

  // ---- wire up triggers ----
  function itemsFromStrip(track: Element): Item[] {
    return Array.from(track.querySelectorAll<HTMLElement>("[data-lightbox]")).map((el) => ({
      full: el.dataset.full ?? "",
      hi: el.dataset.fullHi ?? el.dataset.full ?? "",
      zoom: el.dataset.fullZoom ?? el.dataset.fullHi ?? el.dataset.full ?? "",
      w: Number(el.dataset.w) || 0,
      h: Number(el.dataset.h) || 0,
      era: el.dataset.era ?? "",
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
