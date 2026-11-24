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

/** 通知の日時（相対表記）：たった今／N分前／N時間前／昨日 HH:MM／N日前（7日未満）／それより前は YYYY-MM-DD */
export function relativeTimeStr(d: Date, now: Date = new Date()): string {
  const min = Math.floor((now.getTime() - d.getTime()) / 60_000);
  if (min < 1) return "たった今";
  if (min < 60) return `${min}分前`;
  if (min < 60 * 24) return `${Math.floor(min / 60)}時間前`;
  const days = Math.round(
    (new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() -
      new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()) /
      86_400_000,
  );
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  if (days <= 1) return `昨日 ${hm}`;
  if (days < 7) return `${days}日前`;
  return localDateStr(d);
}

/** 短い抜粋（改行は空白にまとめ、max 字を超えたら「…」。code point で数える） */
export function excerpt(s: string, max: number): string {
  const chars = [...s.replace(/\s+/g, " ").trim()];
  return chars.length > max ? `${chars.slice(0, max).join("")}…` : chars.join("");
}
