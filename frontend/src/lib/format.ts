import { ApiError } from "../api";

/** H-7: 表示用の日付はローカル時刻で YYYY-MM-DD を組み立てる（toISOString は UTC なので使わない） */
export const localDateStr = (d: Date = new Date()) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

export const errText = (e: unknown) => (e instanceof ApiError ? e.detail : String(e));

/** 表示用の日時（ローカル時刻の YYYY-MM-DD HH:MM） */
export const localDateTimeStr = (d: Date) =>
  `${localDateStr(d)} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;

/** 文字数（サーバーと同じく code point で数える。絵文字も1字） */
export const charCount = (s: string) => [...s].length;
