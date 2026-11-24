/* ------------------------------------------------------------------ */
/* ヘッダー右上のベル：自分の投稿に付いた新しいリアクション＋返信の数        */
/* タップで通知パネル（NotificationPanel）を開く。開くとバッジが消える       */
/* 既読はこの端末だけ。家族に「見たかどうか」は伝わらない                    */
/* 数の丸は赤にしない（淡い水色の丸に深い藍の数字。styles.css）              */
/* ------------------------------------------------------------------ */
export function DiaryBell({ count, open, onToggle }: { count: number; open: boolean; onToggle: () => void }) {
  const label = count > 0 ? `通知：あなたの便りに新しい反応が${count}件` : "通知を開く";
  return (
    <button
      className={`diary-bell${open ? " open" : ""}`}
      aria-label={label}
      title={label}
      aria-haspopup="dialog"
      aria-expanded={open}
      aria-controls={open ? "notice-panel" : undefined}
      onClick={onToggle}
    >
      <svg
        viewBox="0 0 24 24"
        aria-hidden="true"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z" />
        <path d="M10 20.5a2 2 0 0 0 4 0" />
      </svg>
      {count > 0 && (
        <span className="diary-bell-count" aria-hidden="true">
          {count > 99 ? "99+" : count}
        </span>
      )}
    </button>
  );
}
