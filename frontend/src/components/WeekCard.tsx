import type { ReactNode } from "react";
import type { Weekly } from "../api";
import { WeeklyMessage } from "./WeeklyMessage";

/* ------------------------------------------------------------------ */
/* 週のカード：静止した波紋の印＋「今週は○日できました」                  */
/* 連続日数・曜日ごとの印は出さない（並んだ印は連続記録に見えるため）         */
/* ------------------------------------------------------------------ */
export function WeekCard({ weekly, children }: { weekly: Weekly; children?: ReactNode }) {
  return (
    <div className="card week">
      <svg className="week-mark" viewBox="0 0 56 56" aria-hidden="true" focusable="false">
        <circle cx="28" cy="28" r="6" fill="#27536B" />
        <circle cx="28" cy="28" r="14" fill="none" stroke="#7FB2CC" strokeWidth="2" />
        <circle cx="28" cy="28" r="23" fill="none" stroke="#7FB2CC" strokeOpacity=".5" strokeWidth="1.5" />
      </svg>
      <div>
        <WeeklyMessage weekly={weekly} />
        {children}
      </div>
    </div>
  );
}
