import type { Share } from "../api";

/* ------------------------------------------------------------------ */
/* 「家族の日記」の通知バッジ（この端末だけの既読状態）                     */
/* 自分の投稿（送った便り）に家族が付けたリアクション・返信のうち、           */
/* 前回この端末で「家族の日記」を開いたときより後に付いたものを数える。       */
/* 既読状態はサーバーに送らない（家族に「見たかどうか」は伝わらない）。       */
/* 端末の時計のずれに左右されないよう、記録するのは「開いた時点で見えていた   */
/* 反応のうち最も新しいもの」のサーバー時刻にする。                         */
/* ------------------------------------------------------------------ */

const key = (userId: string) => `kagami.diarySeenAt.${userId}`;

const ts = (s: string) => {
  const t = Date.parse(s);
  return Number.isNaN(t) ? 0 : t;
};

/** 通知パネルの1行：自分の投稿に家族が付けたリアクション（いいね・スタンプ）か返信 */
export interface DiaryNotice {
  key: string;
  kind: "like" | "stamp" | "reply";
  fromName: string;
  /** スタンプの種別名（いいね・スタンプ）／返信の本文 */
  text: string;
  /** 反応が付いたサーバー時刻（ミリ秒） */
  at: number;
  letter: Share;
  /** 前回見たとき（パネルを開いた・家族の日記を開いた）より後に付いたもの */
  isNew: boolean;
}

const LIKE = "いいね";

function collect(sent: Share[], userId: string, since: number): DiaryNotice[] {
  const out: DiaryNotice[] = [];
  for (const l of sent) {
    if (l.from_user !== userId) continue;
    for (const s of l.stamps ?? []) {
      if (s.from_user === userId) continue;
      const at = ts(s.created_at);
      out.push({
        key: `s${s.id}`,
        kind: s.stamp === LIKE ? "like" : "stamp",
        fromName: s.from_name ?? "",
        text: s.stamp,
        at,
        letter: l,
        isNew: at > since,
      });
    }
    for (const r of l.replies ?? []) {
      if (r.from_user === userId) continue;
      const at = ts(r.created_at);
      out.push({ key: `r${r.id}`, kind: "reply", fromName: r.from_name ?? "", text: r.body, at, letter: l, isNew: at > since });
    }
  }
  return out;
}

/** 自分の投稿に家族が付けたリアクション・返信の時刻（ミリ秒） */
function reactionTimes(sent: Share[], userId: string): number[] {
  return collect(sent, userId, 0).map((n) => n.at);
}

/** 通知パネルに出す一覧（新しい順）。isNew は「前回見たとき」との比較（読むだけで既読にはしない） */
export function listDiaryNotices(sent: Share[], userId: string): DiaryNotice[] {
  return collect(sent, userId, seenAt(userId)).sort((a, b) => b.at - a.at || b.key.localeCompare(a.key));
}

function seenAt(userId: string): number {
  try {
    return Number(localStorage.getItem(key(userId)) ?? "0") || 0;
  } catch {
    return 0;
  }
}

/** バッジに出す数：前回開いたときより後に付いた、自分の投稿へのリアクション＋返信 */
export function countNewReactions(sent: Share[], userId: string): number {
  const since = seenAt(userId);
  return reactionTimes(sent, userId).filter((t) => t > since).length;
}

/** 「家族の日記」または通知パネルを開いた：いま見えている反応をすべて既読にする（この端末だけ） */
export function markDiarySeen(sent: Share[], userId: string): void {
  const latest = Math.max(seenAt(userId), ...reactionTimes(sent, userId));
  try {
    localStorage.setItem(key(userId), String(latest));
  } catch {
    /* 保存できない環境では、次に開いたときにまた数える */
  }
}
