import { describe, expect, it } from "vitest";
import { cumulativePillProgress, sectionProgress } from "../../src/lib/gallery-progress";

describe("gallery progress", () => {
  it("measures local section progress", () => {
    expect(sectionProgress(100, 100, 300)).toBe(0);
    expect(sectionProgress(200, 100, 300)).toBe(0.5);
    expect(sectionProgress(400, 100, 300)).toBe(1);
  });

  it("keeps past pills full and future pills empty", () => {
    expect([0, 1, 2, 3].map((index) => cumulativePillProgress(2, 0.35, index))).toEqual([1, 1, 0.35, 0]);
  });
});
