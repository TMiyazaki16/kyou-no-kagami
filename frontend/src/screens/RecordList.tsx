import { useEffect, useRef, useState } from "react";
import { api, RecordItem } from "../api";
import type { Ctx } from "../navigation";

/** 家族に便りとして送れる日か：その日の一言（kind: word）がある日だけ（サーバーの _feedback_snapshot と同じ判定）。
 *  案内文の日・上書き後に解析していない日は送れないので、メニュー自体を出さない（行き止まりを作らない） */
const canShare = (r: RecordItem) => r.word_kind === "word" && !!r.todays_word?.trim();

/** カード右上の「⋯」と、そこから開く小さなメニュー（項目は「家族への共有をする」だけ） */
function CardMenu({ date, onShare }: { date: string; onShare: () => void }) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const btn = useRef<HTMLButtonElement>(null);

  // 開いたら項目へフォーカス。メニューの外を押す・Esc で閉じる
  useEffect(() => {
    if (!open) return;
    wrap.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus();
    const onDown = (e: PointerEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        setOpen(false);
        btn.current?.focus();
      }
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="card-menu" ref={wrap}>
      <button
        ref={btn}
        type="button"
        className="card-menu-btn"
        aria-label={`${date}の記録のメニュー`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor" aria-hidden="true">
          <circle cx="5" cy="12" r="2" />
          <circle cx="12" cy="12" r="2" />
          <circle cx="19" cy="12" r="2" />
        </svg>
      </button>
      {open && (
        <ul className="card-menu-list" role="menu" aria-label={`${date}の記録のメニュー`}>
          <li role="none">
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                onShare();
              }}
            >
              家族への共有をする
            </button>
          </li>
        </ul>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 記録一覧 (M2)                                                       */
/* 共有できる日のカードには右上に「⋯」。その日を選んだ状態で共有画面を開く   */
/* ------------------------------------------------------------------ */
export function RecordList({ openShare }: Ctx) {
  const [records, setRecords] = useState<RecordItem[]>([]);
  useEffect(() => {
    api.records().then(setRecords).catch(() => setRecords([]));
  }, []);

  return (
    <section>
      <h2>記録一覧</h2>
      {records.length === 0 && <p className="muted">まだ記録がありません。</p>}
      {records.map((r) => (
        <div className="card" key={r.id}>
          <div className="card-head">
            <h3 className="date">{r.date}</h3>
            {canShare(r) && <CardMenu date={r.date} onShare={() => openShare(r.id)} />}
          </div>
          <p>🙏 感謝：{r.gratitude || "（未入力）"}</p>
          <p>🌿 気分・体調：{r.mood || "（未入力）"}</p>
          {r.transcript && <p>🎙 声で話したこと：{r.transcript}</p>}
          {r.todays_word && <p className="word">💬 {r.todays_word}</p>}
          {r.audio_url && <audio controls src={r.audio_url} />}
          {r.photo_url && <img className="preview" src={r.photo_url} alt="記録の写真" />}
        </div>
      ))}
    </section>
  );
}
