import { useEffect, useRef } from "react";
import type { DiaryNotice } from "../lib/diarySeen";
import { excerpt, localDateTimeStr, relativeTimeStr } from "../lib/format";

/* ------------------------------------------------------------------ */
/* 通知パネル（ヘッダーのベルから開く。画面は切り替えない）                 */
/* 中身は「自分の便りに家族が付けたリアクション・返信」だけ（新しい順）       */
/* 開いた時点でバッジは消える（既読はこの端末の localStorage だけ。           */
/* サーバーには送らないので、家族に「見たかどうか」は伝わらない）            */
/* 行を押すと、家族の日記の該当の便りへ移る                                */
/* ------------------------------------------------------------------ */

const PANEL_TITLE = "あなたの便りへの反応";

/** 何をしたか（いいね／スタンプ「…」／返信「…」） */
function what(n: DiaryNotice): JSX.Element {
  if (n.kind === "like") return <>「いいね」しました</>;
  if (n.kind === "stamp") return <>スタンプ「{n.text}」を付けました</>;
  return <>返信しました「{excerpt(n.text, 30)}」</>;
}

/** どの便りへの反応か（便りの日付と本文の冒頭） */
function about(n: DiaryNotice): string {
  const sent = n.letter.sent_at ? new Date(n.letter.sent_at) : null;
  const date = sent ? `${sent.getMonth() + 1}月${sent.getDate()}日の便り` : "便り";
  return `${date}「${excerpt(n.letter.final_text, 18)}」`;
}

export function NotificationPanel({
  notices,
  onClose,
  onPick,
}: {
  /** null は読み込み中 */
  notices: DiaryNotice[] | null;
  onClose: () => void;
  onPick: (letterId: number) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);

  // 開いたらパネルへフォーカス。Esc で閉じる。Tab はパネルの中を回す。後ろはスクロールさせない
  useEffect(() => {
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      } else if (e.key === "Tab") {
        const list = Array.from(ref.current?.querySelectorAll<HTMLButtonElement>("button") ?? []);
        if (list.length === 0) {
          e.preventDefault();
          return;
        }
        const first = list[0];
        const last = list[list.length - 1];
        if (e.shiftKey && (document.activeElement === first || document.activeElement === ref.current)) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    document.body.classList.add("sheet-open");
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.classList.remove("sheet-open");
    };
  }, [onClose]);

  const hasNew = (notices ?? []).some((n) => n.isNew);

  return (
    <div className="notice-backdrop" onClick={onClose}>
      <div
        className="notice-panel"
        id="notice-panel"
        role="dialog"
        aria-modal="true"
        aria-label={PANEL_TITLE}
        tabIndex={-1}
        ref={ref}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="notice-head">
          <h2 className="notice-title">{PANEL_TITLE}</h2>
          <button className="notice-close" onClick={onClose} aria-label="通知を閉じる">
            閉じる
          </button>
        </div>

        {notices === null && <p className="muted notice-empty">読み込み中…</p>}
        {notices !== null && !hasNew && <p className="notice-empty">新しい通知はありません</p>}

        {notices !== null && notices.length > 0 && (
          <ul className="notice-list">
            {notices.map((n) => (
              <li key={n.key}>
                <button className={`notice-item${n.isNew ? " new" : ""}`} onClick={() => onPick(n.letter.id)}>
                  <span className="notice-avatar" aria-hidden="true">
                    {[...n.fromName][0] ?? "？"}
                  </span>
                  <span className="notice-text">
                    <span className="notice-what">
                      {n.isNew && <span className="notice-new">新着</span>}
                      <strong>{n.fromName}</strong>が{what(n)}
                    </span>
                    <span className="notice-about">{about(n)}</span>
                    <time className="notice-time" dateTime={new Date(n.at).toISOString()} title={localDateTimeStr(new Date(n.at))}>
                      {relativeTimeStr(new Date(n.at))}
                    </time>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
