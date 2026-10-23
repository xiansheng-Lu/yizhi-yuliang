import { validDate } from "./delivery";

/** Business reporting uses calendar dates in Asia/Shanghai, independent of host timezone. */
export function shiftDate(date: string, days: number) {
  if (!validDate(date) || !Number.isInteger(days)) return "";
  const at = new Date(`${date}T12:00:00+08:00`);
  at.setUTCDate(at.getUTCDate() + days);
  return at.toISOString().slice(0, 10);
}
export function validDateRange(start: string, end: string) {
  return validDate(start) && validDate(end) && start <= end;
}
export function percentChange(current: number | null | undefined, previous: number | null | undefined) {
  return typeof current === "number" && Number.isFinite(current) && typeof previous === "number" && Number.isFinite(previous) && previous > 0
    ? (current - previous) / previous : null;
}
