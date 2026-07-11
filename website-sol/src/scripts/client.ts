import PhotoSwipeLightbox from "photoswipe/lightbox";
import type PhotoSwipe from "photoswipe";
import { calculateVisitWindow } from "../lib/hours";
import { cumulativePillProgress, sectionProgress } from "../lib/gallery-progress";
import type { VisitConfig } from "../lib/types";

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

function closeLightboxSafely(pswp: PhotoSwipe) {
  if (pswp.opener.isOpening) {
    pswp.on("openingAnimationEnd", () => pswp.close());
  } else {
    pswp.close();
  }
}

function initTrail() {
  const stops = [...document.querySelectorAll<HTMLElement>('.work-stop--trail[data-work-stop]')];
  if (!stops.length) return;
  const currentLabels = [...document.querySelectorAll<HTMLElement>("[data-current-stop]")];
  const currentNumbers = [...document.querySelectorAll<HTMLElement>("[data-current-number]")];
  const markers = [...document.querySelectorAll<SVGElement>("[data-map-slug]")];
  let activeIndex = 0;

  const activate = (index: number) => {
    if (index < 0 || index >= stops.length) return;
    activeIndex = index;
    const stop = stops[index];
    const title = stop.dataset.workTitle || "";
    const slug = stop.dataset.workSlug || "";
    const progress = stops.length <= 1 ? 1 : index / (stops.length - 1);
    document.documentElement.style.setProperty("--route-progress", String(progress));
    document.documentElement.style.setProperty("--active-accent", stop.dataset.accent || "#e75c52");
    currentLabels.forEach((label) => (label.textContent = title));
    currentNumbers.forEach((number) => (number.textContent = String(index + 1)));
    markers.forEach((marker) => marker.classList.toggle("is-active", marker.dataset.mapSlug === slug));
    stops.forEach((candidate, candidateIndex) => candidate.classList.toggle("is-active", candidateIndex === index));
  };

  const observer = new IntersectionObserver(
    (entries) => {
      const visible = entries
        .filter((entry) => entry.isIntersecting)
        .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (!visible) return;
      activate(stops.indexOf(visible.target as HTMLElement));
    },
    { rootMargin: "-28% 0px -50%", threshold: [0, 0.15, 0.35, 0.65] },
  );
  stops.forEach((stop) => observer.observe(stop));

  document.querySelectorAll<HTMLAnchorElement>("[data-map-link]").forEach((link) => {
    link.addEventListener("click", (event) => {
      const target = stops.find((stop) => stop.dataset.workSlug === link.dataset.mapSlug);
      if (!target) return;
      event.preventDefault();
      target.scrollIntoView({ behavior: reducedMotion.matches ? "auto" : "smooth", block: "start" });
      const dialog = document.querySelector<HTMLDialogElement>("[data-map-dialog]");
      if (dialog?.open) dialog.close();
    });
  });

  const initialSlug = document.body.dataset.initialStop;
  if (initialSlug) {
    const target = stops.find((stop) => stop.dataset.workSlug === initialSlug) || document.getElementById(initialSlug);
    if (target) requestAnimationFrame(() => target.scrollIntoView({ behavior: "auto", block: "start" }));
  }
  activate(activeIndex);
}

function initMapDialog() {
  const dialog = document.querySelector<HTMLDialogElement>("[data-map-dialog]");
  const open = document.querySelector<HTMLButtonElement>("[data-open-map]");
  if (!dialog || !open) return;
  open.addEventListener("click", () => dialog.showModal());
  dialog.querySelector<HTMLButtonElement>("[data-close-map]")?.addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
}

function initGallery(rail: HTMLElement) {
  const track = rail.querySelector<HTMLElement>("[data-gallery-track]");
  const sections = [...rail.querySelectorAll<HTMLElement>("[data-gallery-section]")];
  const pills = [...rail.querySelectorAll<HTMLButtonElement>("[data-gallery-jump]")];
  if (!track || !sections.length) return;

  let frame = 0;
  const update = () => {
    frame = 0;
    const left = track.scrollLeft;
    let activeIndex = sections.findIndex((_section, index) => {
      const next = sections[index + 1];
      return left < (next?.offsetLeft ?? Number.POSITIVE_INFINITY) - 2;
    });
    if (activeIndex < 0) activeIndex = sections.length - 1;
    const active = sections[activeIndex];
    const next = sections[activeIndex + 1];
    const end = next?.offsetLeft ?? Math.max(active.offsetLeft + active.offsetWidth - track.clientWidth, active.offsetLeft + 1);
    const local = sectionProgress(left, active.offsetLeft, end);
    pills.forEach((pill, index) => {
      const progress = cumulativePillProgress(activeIndex, local, index);
      pill.style.setProperty("--fill", progress.toFixed(4));
      pill.classList.toggle("is-active", index === activeIndex);
      if (index === activeIndex) pill.setAttribute("aria-current", "true");
      else pill.removeAttribute("aria-current");
    });
    rail.classList.toggle("is-at-start", left < 4);
    rail.classList.toggle("is-at-end", left + track.clientWidth >= track.scrollWidth - 4);
  };
  const schedule = () => {
    if (!frame) frame = requestAnimationFrame(update);
  };
  track.addEventListener("scroll", schedule, { passive: true });
  new ResizeObserver(schedule).observe(track);

  pills.forEach((pill) => {
    pill.addEventListener("click", () => {
      const section = sections.find((candidate) => candidate.dataset.gallerySection === pill.dataset.galleryJump);
      if (!section) return;
      track.scrollTo({ left: section.offsetLeft, behavior: reducedMotion.matches ? "auto" : "smooth" });
    });
  });

  const firstCard = () => track.querySelector<HTMLElement>(".media-card");
  rail.querySelector<HTMLButtonElement>("[data-gallery-prev]")?.addEventListener("click", () => {
    track.scrollBy({ left: -(firstCard()?.offsetWidth || track.clientWidth * 0.8), behavior: reducedMotion.matches ? "auto" : "smooth" });
  });
  rail.querySelector<HTMLButtonElement>("[data-gallery-next]")?.addEventListener("click", () => {
    track.scrollBy({ left: firstCard()?.offsetWidth || track.clientWidth * 0.8, behavior: reducedMotion.matches ? "auto" : "smooth" });
  });

  let dragStart = 0;
  let scrollStart = 0;
  let moved = false;
  let dragPointer: number | null = null;
  track.addEventListener("pointerdown", (event) => {
    if (event.pointerType !== "mouse" || event.button !== 0) return;
    dragStart = event.clientX;
    scrollStart = track.scrollLeft;
    moved = false;
    dragPointer = event.pointerId;
  });
  track.addEventListener("pointermove", (event) => {
    if (dragPointer !== event.pointerId || event.buttons !== 1) return;
    const distance = event.clientX - dragStart;
    if (Math.abs(distance) <= 6 && !moved) return;
    if (!moved) {
      moved = true;
      track.classList.add("is-dragging");
      track.setPointerCapture(event.pointerId);
    }
    track.scrollLeft = scrollStart - distance;
  });
  const finishDrag = (event: PointerEvent) => {
    if (track.hasPointerCapture(event.pointerId)) track.releasePointerCapture(event.pointerId);
    track.classList.remove("is-dragging");
    dragPointer = null;
  };
  track.addEventListener("pointerup", finishDrag);
  track.addEventListener("pointercancel", finishDrag);
  track.addEventListener(
    "click",
    (event) => {
      if (moved) {
        event.preventDefault();
        event.stopPropagation();
        moved = false;
      }
    },
    true,
  );
  update();
}

function initLightboxes() {
  document.querySelectorAll<HTMLElement>("[data-gallery-track]").forEach((gallery) => {
    const anchors = [...gallery.querySelectorAll<HTMLAnchorElement>("a[data-pswp]")];
    if (!anchors.length) return;
    const dataSource = anchors.map((anchor) => ({
      src: anchor.href,
      width: Number(anchor.dataset.pswpWidth),
      height: Number(anchor.dataset.pswpHeight),
      srcset: anchor.dataset.pswpSrcset,
      msrc: anchor.querySelector<HTMLImageElement>("img")?.currentSrc,
      alt: anchor.querySelector<HTMLImageElement>("img")?.alt || anchor.dataset.pswpAlt,
      element: anchor,
    }));
    const lightbox = new PhotoSwipeLightbox({
      dataSource,
      pswpModule: () => import("photoswipe"),
      bgOpacity: 0.96,
      loop: false,
      preload: [1, 2],
      imageClickAction(this: PhotoSwipe) { closeLightboxSafely(this); },
      tapAction(this: PhotoSwipe) { closeLightboxSafely(this); },
      doubleTapAction: "zoom",
      bgClickAction(this: PhotoSwipe) { closeLightboxSafely(this); },
      closeOnVerticalDrag: true,
      pinchToClose: true,
      wheelToZoom: false,
      closeTitle: "Close full-screen image",
      zoomTitle: "Zoom image",
      arrowPrevTitle: "Previous image",
      arrowNextTitle: "Next image",
      errorMsg: "This full-size image is unavailable. The preview may still be cached.",
    });
    let currentIndex = 0;
    let closeOnEscape: ((event: KeyboardEvent) => void) | undefined;
    gallery.dataset.lightboxReady = "true";
    anchors.forEach((anchor, index) => {
      anchor.addEventListener("click", (event) => {
        event.preventDefault();
        currentIndex = index;
        if (!closeOnEscape) {
          closeOnEscape = (keyboardEvent) => {
            if (keyboardEvent.key === "Escape") {
              keyboardEvent.preventDefault();
              keyboardEvent.stopImmediatePropagation();
              if (lightbox.pswp) closeLightboxSafely(lightbox.pswp);
            }
          };
          document.addEventListener("keydown", closeOnEscape, { capture: true });
        }
        lightbox.loadAndOpen(index);
      });
    });
    lightbox.on("change", () => {
      currentIndex = lightbox.pswp?.currIndex ?? currentIndex;
    });
    lightbox.on("afterInit", () => {
      const pswp = lightbox.pswp;
      if (!pswp?.element) return;
      pswp.element.addEventListener(
        "click",
        (event) => {
          if ((event.target as HTMLElement).closest(".pswp__button--close")) {
            event.preventDefault();
            event.stopImmediatePropagation();
            closeLightboxSafely(pswp);
          }
        },
        { capture: true },
      );
      pswp.element.addEventListener(
        "wheel",
        (event) => {
          const slide = pswp.currSlide;
          if (slide && slide.currZoomLevel <= slide.zoomLevels.initial + 0.01 && !event.ctrlKey) {
            event.preventDefault();
            closeLightboxSafely(pswp);
          }
        },
        { passive: false },
      );
    });
    lightbox.on("close", () => {
      if (closeOnEscape) document.removeEventListener("keydown", closeOnEscape, { capture: true });
      closeOnEscape = undefined;
      anchors[currentIndex]?.scrollIntoView({ behavior: "auto", block: "nearest", inline: "center" });
    });
    lightbox.init();
  });
}

function initVideoDialog() {
  const dialog = document.querySelector<HTMLDialogElement>("[data-video-dialog]");
  const player = dialog?.querySelector<HTMLVideoElement>("[data-video-player]");
  const description = dialog?.querySelector<HTMLElement>("[data-video-description]");
  if (!dialog || !player || !description) return;

  const close = () => {
    player.pause();
    player.removeAttribute("src");
    player.removeAttribute("poster");
    player.load();
    if (dialog.open) dialog.close();
  };
  document.querySelectorAll<HTMLButtonElement>("[data-video-src]").forEach((button) => {
    button.addEventListener("click", () => {
      player.src = button.dataset.videoSrc || "";
      player.poster = button.dataset.videoPoster || "";
      description.textContent = button.dataset.videoAlt || "";
      dialog.showModal();
      player.play().catch(() => undefined);
    });
  });
  dialog.querySelector<HTMLButtonElement>("[data-close-video]")?.addEventListener("click", close);
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    close();
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) close();
  });
}

function initVisitHours() {
  const panel = document.querySelector<HTMLElement>("[data-visit-hours]");
  if (!panel) return;
  const config = {
    latitude: Number(panel.dataset.latitude),
    longitude: Number(panel.dataset.longitude),
    timezone: panel.dataset.timezone,
    dawnOffsetMinutes: Number(panel.dataset.dawnOffset),
    duskOffsetMinutes: Number(panel.dataset.duskOffset),
  } as VisitConfig;
  const window = calculateVisitWindow(new Date(), config);
  const opens = panel.querySelector<HTMLTimeElement>("[data-opens-time]");
  const closes = panel.querySelector<HTMLTimeElement>("[data-closes-time]");
  if (opens) {
    opens.textContent = window.opensLabel;
    opens.dateTime = window.opensAt.toISOString();
  }
  if (closes) {
    closes.textContent = window.closesLabel;
    closes.dateTime = window.closesAt.toISOString();
  }
}

function initSharing() {
  const toast = document.querySelector<HTMLElement>("[data-copy-toast]");
  let timeout = 0;
  document.querySelectorAll<HTMLButtonElement>("[data-copy-link]").forEach((button) => {
    button.addEventListener("click", async () => {
      const url = new URL(button.dataset.copyLink || "/", window.location.origin).href;
      try {
        await navigator.clipboard.writeText(url);
        if (toast) {
          toast.classList.add("is-visible");
          window.clearTimeout(timeout);
          timeout = window.setTimeout(() => toast.classList.remove("is-visible"), 1800);
        }
      } catch {
        window.location.href = url;
      }
    });
  });
}

function initAmbientLight() {
  const keyframes = [
    { at: 0, color: [220, 233, 227] },
    { at: 0.42, color: [167, 198, 183] },
    { at: 0.72, color: [61, 76, 101] },
    { at: 1, color: [19, 26, 42] },
  ];
  let frame = 0;
  const update = () => {
    frame = 0;
    const maximum = Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
    const progress = Math.min(1, Math.max(0, window.scrollY / maximum));
    const nextIndex = Math.min(keyframes.length - 1, Math.max(1, keyframes.findIndex((frame) => frame.at >= progress)));
    const start = keyframes[nextIndex - 1];
    const end = keyframes[nextIndex];
    const local = (progress - start.at) / Math.max(0.001, end.at - start.at);
    const color = start.color.map((channel, index) => Math.round(channel + (end.color[index] - channel) * local));
    document.documentElement.style.setProperty("--page-bg", `rgb(${color.join(" ")})`);
    document.documentElement.dataset.lightPhase = progress > 0.62 ? "dark" : "light";
  };
  const schedule = () => {
    if (!frame) frame = requestAnimationFrame(update);
  };
  window.addEventListener("scroll", schedule, { passive: true });
  window.addEventListener("resize", schedule, { passive: true });
  update();
}

document.querySelectorAll<HTMLElement>("[data-media-rail]").forEach(initGallery);
initTrail();
initMapDialog();
initLightboxes();
initVideoDialog();
initVisitHours();
initSharing();
initAmbientLight();

if ("serviceWorker" in navigator && !import.meta.env.DEV) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => undefined));
}
