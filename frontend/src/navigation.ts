import type { FamilyMember, Me } from "./api";

export type Screen =
  | "home"
  | "record"
  | "today"
  | "list"
  | "reflect"
  | "share"
  | "letters"
  | "dev"
  | "settings"
  | "disclaimer";

export const NAV: { id: Screen; label: string }[] = [
  { id: "home", label: "ホーム" },
  { id: "record", label: "記録" },
  { id: "today", label: "今日の一言" },
  { id: "list", label: "記録一覧" },
  { id: "reflect", label: "振り返り" },
  { id: "share", label: "共有の確認" },
  { id: "letters", label: "届いた便り" },
  { id: "dev", label: "仕組みを見る" },
  { id: "settings", label: "設定・家族" },
  { id: "disclaimer", label: "免責表示" },
];

/** 各画面に渡す共通の props（ログイン中の本人・連携家族・画面遷移・再取得） */
export interface Ctx {
  me: Me;
  family: FamilyMember[];
  go: (s: Screen) => void;
  refreshMe: () => Promise<void>;
}

/**
 * 下部タブに出す主要画面（案B）。並びと文言は NAV のものを使う。
 * ここにない画面（今日の一言・記録一覧・共有の確認・設定・家族・仕組みを見る・免責表示）は
 * タブの「その他」から開くシートに、NAV の並びのまま出す。画面の数・名前・意味は変えない。
 */
export const TAB_IDS: Screen[] = ["home", "record", "reflect", "letters"];
export const MORE_LABEL = "その他";
