export const clamp = (value: number, minimum = 0, maximum = 1) => Math.min(maximum, Math.max(minimum, value));

export function sectionProgress(scrollLeft: number, start: number, end: number): number {
  if (end <= start) return scrollLeft >= start ? 1 : 0;
  return clamp((scrollLeft - start) / (end - start));
}

export function cumulativePillProgress(activeIndex: number, localProgress: number, pillIndex: number): number {
  if (pillIndex < activeIndex) return 1;
  if (pillIndex > activeIndex) return 0;
  return clamp(localProgress);
}
