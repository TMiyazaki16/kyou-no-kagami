import type { Weekly } from "../api";

export function WeeklyMessage({ weekly }: { weekly: Weekly }) {
  return <p className="big">{weekly.message}{weekly.days_recorded > 0 ? " 🎉" : " 🌱"}</p>;
}
