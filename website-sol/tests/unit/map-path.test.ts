import { describe, expect, it } from "vitest";
import { smoothPath } from "../../src/lib/map-path";

describe("smoothPath", () => {
  it("handles an empty or two-point route", () => {
    expect(smoothPath([])).toBe("");
    expect(smoothPath([{ x: 1, y: 2 }, { x: 3, y: 4 }])).toBe("M 1 2 L 3 4");
  });

  it("passes through a multi-stop route with a smooth command", () => {
    const path = smoothPath([{ x: 0, y: 0 }, { x: 10, y: 20 }, { x: 20, y: 10 }]);
    expect(path).toBe("M 0 0 Q 10 20 15 15 T 20 10");
  });
});
