import { useCallback, useEffect, useState } from "react";
import { api, Letters as LettersData, REPLY_MAX_CHARS, Reply, Share } from "../api";
import { charCount, errText, localDateStr, localDateTimeStr } from "../lib/format";

const LIKE = "いいね";

const sentDate = (l: Share) => (l.sent_at ? localDateStr(new Date(l.sent_at)) : "");

/** 返信のやりとり（古い順）。受け取った側・送った側の両方で使う */
function ReplyThread({ replies, fallbackName }: { replies: Reply[]; fallbackName: string }) {
  if (replies.length === 0) return null;
  return (
    <ul className="reply-thread" aria-label="返信">
      {replies.map((r) => (
        <li key={r.id} className="reply-item">
          <p className="reply-meta">
            {r.from_name ?? fallbackName} ・ {localDateTimeStr(new Date(r.created_at))}
          </p>
          <p className="reply-text">{r.body}</p>
        </li>
      ))}
    </ul>
  );
}

function LetterPhoto({ url, alt }: { url?: string | null; alt: string }) {
  return url ? <img className="letter-photo" src={url} alt={alt} loading="lazy" /> : null;
}

/* ------------------------------------------------------------------ */
/* 届いた便り                                                          */
/* 受け取った便り：リアクション（いいね＋スタンプ、付け外しできる）とテキスト返信 */
/* 送った便り：それへのリアクションと返信を見る（既読は出さない）             */
/* ------------------------------------------------------------------ */
export function Letters() {
  const [letters, setLetters] = useState<LettersData>({ received: [], sent: [] });
  const [reactionOptions, setReactionOptions] = useState<string[]>([]);
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState<number | null>(null);
  const [errors, setErrors] = useState<Record<number, string>>({});

  const load = useCallback(() => {
    api.letters().then(setLetters).catch(() => setLetters({ received: [], sent: [] }));
  }, []);

  useEffect(() => {
    load();
    api.stamps().then(setReactionOptions).catch(() => {});
  }, [load]);

  const replaceReceived = (letter: Share) =>
    setLetters((cur) => ({ ...cur, received: cur.received.map((l) => (l.id === letter.id ? letter : l)) }));

  const run = async (shareId: number, what: string, fn: () => Promise<void>) => {
    setBusy(shareId);
    setErrors((e) => ({ ...e, [shareId]: "" }));
    try {
      await fn();
    } catch (e) {
      setErrors((cur) => ({ ...cur, [shareId]: `${what}できませんでした: ${errText(e)}` }));
    } finally {
      setBusy(null);
    }
  };

  const toggle = (shareId: number, reaction: string) =>
    run(shareId, "リアクション", async () => {
      replaceReceived((await api.toggleReaction(shareId, reaction)).letter);
    });

  const reply = (shareId: number) =>
    run(shareId, "返信", async () => {
      const res = await api.addReply(shareId, (drafts[shareId] ?? "").trim());
      replaceReceived(res.letter);
      setDrafts((d) => ({ ...d, [shareId]: "" }));
    });

  return (
    <section>
      <h2>届いた便り</h2>
      {letters.received.length === 0 && (
        <p className="muted">まだ便りは届いていません。相手が「送る」と決めたものだけが、ここに届きます。</p>
      )}
      {letters.received.map((l) => {
        const text = drafts[l.id] ?? "";
        const count = charCount(text.trim());
        const over = count > REPLY_MAX_CHARS;
        return (
          <div className="card letter-card" key={l.id}>
            <p className="date">
              {l.from_name}から ・ {sentDate(l)}
            </p>
            <p className="letter-body">{l.final_text}</p>
            <LetterPhoto url={l.photo_url} alt={`${l.from_name}が便りと一緒に届けた写真`} />
            {/* リアクションは大きなチップ。押すと付き、もう一度押すと外れる（数は出さない） */}
            <div className="stamp-chips" role="group" aria-label="リアクション">
              {reactionOptions.map((s) => {
                const on = (l.stamps ?? []).some((x) => x.stamp === s);
                return (
                  <button
                    key={s}
                    className={`stamp-chip${on ? " active" : ""}${s === LIKE ? " like" : ""}`}
                    aria-pressed={on}
                    disabled={busy === l.id}
                    onClick={() => toggle(l.id, s)}
                  >
                    {s === LIKE && <span aria-hidden="true">{on ? "♥" : "♡"}</span>}
                    {s}
                  </button>
                );
              })}
            </div>
            <ReplyThread replies={l.replies ?? []} fallbackName="あなた" />
            <label className="q reply-label" htmlFor={`reply-${l.id}`}>
              {l.from_name}に返信する
            </label>
            <textarea
              id={`reply-${l.id}`}
              className="reply-input"
              rows={2}
              value={text}
              placeholder="ひとこと書いてください"
              aria-describedby={`reply-count-${l.id}`}
              onChange={(e) => setDrafts((d) => ({ ...d, [l.id]: e.target.value }))}
            />
            <div className="reply-actions">
              <span id={`reply-count-${l.id}`} className={over ? "reply-count over" : "reply-count"}>
                {count} / {REPLY_MAX_CHARS}字
              </span>
              <button
                className="primary"
                disabled={busy === l.id || count === 0 || over}
                onClick={() => reply(l.id)}
              >
                返信する
              </button>
            </div>
            {errors[l.id] && <p className="error">{errors[l.id]}</p>}
          </div>
        );
      })}

      <h3 className="section-title">あなたの便りへの返事</h3>
      {letters.sent.length === 0 && (
        <p className="muted">まだ便りを送っていません。送りたいときは「共有の確認」からどうぞ。</p>
      )}
      {letters.sent.map((l) => {
        const stamps = l.stamps ?? [];
        const replies = l.replies ?? [];
        return (
          <div className="card sent-card" key={l.id}>
            <p className="date">
              {l.to_name}へ ・ {sentDate(l)}
            </p>
            <p className="muted sent-body">{l.final_text}</p>
            {l.photo_url && (
              <>
                <LetterPhoto url={l.photo_url} alt="便りと一緒に届けた写真" />
                <p className="muted photo-caption">写真も一緒に届けました</p>
              </>
            )}
            {stamps.length > 0 && (
              <p className="big replies">
                {stamps.map((s) => (
                  <span key={s.id} className="stamp reply-bubble">
                    {s.stamp === LIKE && <span aria-hidden="true">♥ </span>}
                    {s.from_name ?? l.to_name}から「{s.stamp}」
                  </span>
                ))}
              </p>
            )}
            <ReplyThread replies={replies} fallbackName={l.to_name} />
            {stamps.length === 0 && replies.length === 0 && <p className="muted">返事はまだです。</p>}
          </div>
        );
      })}
      <p className="muted">※ 家族のあいだで記録の回数を比べる表示は、このアプリにはありません。</p>
    </section>
  );
}
