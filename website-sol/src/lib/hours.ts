import * as SunCalc from "suncalc";
import type { VisitConfig } from "./types";

export type VisitWindow = {
  opensAt: Date;
  closesAt: Date;
  opensLabel: string;
  closesLabel: string;
  timezoneLabel: string;
};

export function formatLocalTime(date: Date, timezone: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: timezone,
  }).format(date);
}

export function calculateVisitWindow(now: Date, config: VisitConfig): VisitWindow {
  const times = SunCalc.getTimes(now, config.latitude, config.longitude);
  if (!times.dawn || !times.dusk) {
    throw new Error("Civil dawn and dusk are unavailable for this date and location");
  }
  const opensAt = new Date(times.dawn.getTime() + config.dawnOffsetMinutes * 60_000);
  const closesAt = new Date(times.dusk.getTime() + config.duskOffsetMinutes * 60_000);
  return {
    opensAt,
    closesAt,
    opensLabel: formatLocalTime(opensAt, config.timezone),
    closesLabel: formatLocalTime(closesAt, config.timezone),
    timezoneLabel: "Shutesbury time",
  };
}
