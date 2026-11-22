import { ApiError } from "../api";

/** H-7: 表示用の日付はローカル時刻で YYYY-MM-DD を組み立てる（toISOString は UTC なので使わない） */
export const localDateStr = (d: Date = new Date()) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

export const errText = (e: unknown) => (e instanceof ApiError ? e.detail : String(e));
