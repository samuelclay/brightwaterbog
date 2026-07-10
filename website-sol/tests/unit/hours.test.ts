import { describe, expect, it } from "vitest";
import * as SunCalc from "suncalc";
import { calculateVisitWindow } from "../../src/lib/hours";
import type { VisitConfig } from "../../src/lib/types";

const config: VisitConfig = {
  status: "draft",
  address: "",
  directionsUrl: "",
  parking: "",
  access: "",
  latitude: 42.4984,
  longitude: -72.4205,
  timezone: "America/New_York",
  dawnOffsetMinutes: -60,
  duskOffsetMinutes: 60,
};

describe("visit window", () => {
  it("opens one hour before civil dawn and closes one hour after civil dusk", () => {
    const date = new Date("2026-07-09T16:00:00Z");
    const solar = SunCalc.getTimes(date, config.latitude, config.longitude);
    const result = calculateVisitWindow(date, config);
    expect(result.opensAt.getTime()).toBe(solar.dawn!.getTime() - 3_600_000);
    expect(result.closesAt.getTime()).toBe(solar.dusk!.getTime() + 3_600_000);
    expect(result.opensLabel).toMatch(/^\d{2}:\d{2}$/);
    expect(result.closesLabel).toMatch(/^\d{2}:\d{2}$/);
  });
});
