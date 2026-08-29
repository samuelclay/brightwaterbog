// Strip clips behave like photos that happen to move: muted, looping, no
// controls. The cost of that is bandwidth, so nothing is fetched until a clip
// scrolls into view — <video> ships with preload="none" and no src, and this
// attaches the source on first intersection, then plays/pauses with visibility.
//
// Honors prefers-reduced-motion: those visitors keep the poster still, which
// is a frame from a third of the way in rather than a black opening frame.

const SELECTOR = "video[data-strip-video]";

function init() {
  const videos = Array.from(document.querySelectorAll<HTMLVideoElement>(SELECTOR));
  if (!videos.length) return;

  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  const attach = (v: HTMLVideoElement) => {
    if (v.dataset.attached) return;
    v.dataset.attached = "1";
    v.src = v.dataset.stripVideo ?? "";
  };

  const io = new IntersectionObserver(
    (entries) => {
      for (const e of entries) {
        const v = e.target as HTMLVideoElement;
        if (e.isIntersecting) {
          attach(v);
          // play() rejects when the tab is backgrounded or the decoder is
          // busy; there is nothing to recover, and the poster still shows.
          void v.play().catch(() => {});
        } else if (!v.paused) {
          v.pause();
        }
      }
    },
    // A little margin so a clip is running by the time it's properly on screen.
    { rootMargin: "100px", threshold: 0.1 },
  );

  for (const v of videos) io.observe(v);

  // A backgrounded tab keeps decoding otherwise.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) return;
    for (const v of videos) if (!v.paused) v.pause();
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
