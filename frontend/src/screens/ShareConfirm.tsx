import { useCallback, useEffect, useRef, useState } from "react";
import { api, COMMENT_MAX_CHARS, RecordItem, SharePreview } from "../api";
import { charCount, errText, localDateStr } from "../lib/format";
import { FeedbackQuote } from "../components/FeedbackQuote";
import type { Ctx } from "../navigation";

/** 選択肢の短い説明：その日の内容が分かる程度（長い文は切る） */
const clip = (s: string, n = 14) => {
  const chars = [...s.trim()];
  return chars.length > n ? `${chars.slice(0, n).join("")}…` : chars.join("");
};

/** 記録の選択肢の表示：「11月22日（今日）　庭のバラ／元気」（本人だけが見る選択肢） */
function recordLabel(r: RecordItem, today: string): string {
  const [, m, d] = r.date.split("-").map(Number);
  const day = `${m}月${d}日${r.date === today ? "（今日）" : ""}`;
  const parts = [r.gratitude, r.mood || r.transcript].map((x) => clip(x || "")).filter(Boolean);
  return parts.length ? `${day}　${parts.join("／")}` : day;
}

/* ------------------------------------------------------------------ */
/* 家族への共有                                                          */
/* 届くのは、その日の鏡のことば（今日の一言＋観察）の引用と、あなたのコメント。  */
/* 引用は書きかえられない。回答そのもの（2問の答え・声）は届かない。          */
/* 選んだ家族それぞれに、別々の便りとして届く。同じ日を同じ人に送るのは1回だけ。 */
/* ------------------------------------------------------------------ */
export function ShareConfirm({
  family,
  go,
  openDiary,
  initialRecordId,
  onInitialUsed,
}: Ctx & {
  /** 最初に選ぶ記録（記録一覧の「⋯」から開いたとき）。無ければ今日、なければ最新 */
  initialRecordId?: number | null;
  /** initialRecordId を受け取ったら呼ぶ（App 側の値を消して、一度だけにする） */
  onInitialUsed?: () => void;
}) {
  // 開いた時点の指定だけを使う（App 側の値はすぐ消えるので、ここで持っておく）
  const initial = useRef(initialRecordId ?? null);
  useEffect(() => {
    if (initialRecordId != null) onInitialUsed?.();
  }, [initialRecordId, onInitialUsed]);
  // どの日の記録を共有するか。初期は指定の記録、なければ今日の記録（なければ最新の記録）
  const [records, setRecords] = useState<RecordItem[] | null>(null);
  const [recordId, setRecordId] = useState<number | null>(null);
  const [preview, setPreview] = useState<SharePreview | null>(null);
  const [toUsers, setToUsers] = useState<string[]>([]);
  const [comment, setComment] = useState("");
  // 「写真も一緒に届ける」。既定はオフ（記録に写真があっても、本人がオンにしたときだけ届く）
  const [includePhoto, setIncludePhoto] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const today = localDateStr();

  useEffect(() => {
    api
      .records()
      .then((rs) => {
        setRecords(rs);
        // 一覧は新しい日付順。指定の記録があればそれ、なければ今日の記録、なければ最新
        const picked = rs.find((r) => r.id === initial.current) ?? rs.find((r) => r.date === today) ?? rs[0];
        setRecordId(picked?.id ?? null);
      })
      .catch(() => setRecords([]));
  }, [today]);

  /** 選んだ日の引用と宛先を読み直す。未送信の相手が1人だけなら、はじめからチェックしておく */
  const loadPreview = useCallback(async (id: number) => {
    const p = await api.sharePreview(id);
    setPreview(p);
    const open = p.recipients.filter((r) => !r.already_sent);
    setToUsers(open.length === 1 && p.recipients.length === 1 ? [open[0].id] : []);
    return p;
  }, []);

  useEffect(() => {
    if (recordId === null) return;
    setPreview(null);
    setIncludePhoto(false);
    setError("");
    loadPreview(recordId).catch((e) => setError(`この日を開けませんでした: ${errText(e)}`));
  }, [recordId, loadPreview]);

  const count = charCount(comment.trim());
  const over = count > COMMENT_MAX_CHARS;
  const recipients = preview?.recipients ?? [];
  const allSent = recipients.length > 0 && recipients.every((r) => r.already_sent);
  const sendable = !!preview?.feedback && toUsers.length > 0 && !over && !busy;

  const toggleTo = (id: string, on: boolean) =>
    setToUsers((cur) => (on ? [...cur.filter((x) => x !== id), id] : cur.filter((x) => x !== id)));

  const send = async () => {
    if (!preview || recordId === null) return;
    setBusy(true);
    setError("");
    try {
      const { letters } = await api.sendLetters(
        recordId,
        toUsers,
        comment,
        includePhoto && !!preview.record_photo_url,
      );
      const names = letters.map((l) => l.to_name).join("・");
      const withPhoto = letters.some((l) => l.photo_url);
      // 送れたら家族の日記へ移り、送った便りを並んだ状態で見せる（お知らせは日記の上部に一度だけ）。
      // 共有画面へ戻ってきたときは、開き直しのプレビューで宛先が「送信済み」になる
      openDiary({
        ids: letters.map((l) => l.id),
        notice:
          `✓ ${names}へ${withPhoto ? "、写真と一緒に" : ""}届けました。` +
          (letters.length > 1 ? "それぞれに別の便りとして届いています。" : ""),
      });
    } catch (e) {
      setError(`送れませんでした: ${errText(e)}`);
      // 送信済みの相手が増えていた（別の端末で送ったなど）ときに表示を合わせる
      loadPreview(recordId).catch(() => {});
      setBusy(false);
    }
  };

  return (
    <section>
      <h2>家族への共有</h2>
      <p className="lead">
        共有は任意です。届くのは、その日の鏡のことば（今日の一言と観察）と、あなたが添えるコメントだけです。
        記録の回答そのものは届きません。「送る」を押すまで、誰にも届きません。
      </p>
      {family.length === 0 && (
        <div className="card">
          <p>まだ家族と連携していません。便りは、連携した家族にだけ送れます。</p>
          <button onClick={() => go("settings")}>設定・家族で連携する</button>
        </div>
      )}
      {family.length > 0 && records !== null && records.length === 0 && (
        <div className="card">
          <p>まだ記録がありません。記録すると、その日の鏡のことばを家族に届けられます。</p>
          <button onClick={() => go("record")}>記録する</button>
        </div>
      )}
      {family.length > 0 && records !== null && records.length > 0 && (
        <div className="card">
          <label className="q" htmlFor="share-record">どの日のことばを届けますか</label>
          <select
            id="share-record"
            value={recordId ?? ""}
            onChange={(e) => setRecordId(Number(e.target.value))}
            disabled={busy}
          >
            {records.map((r) => (
              <option key={r.id} value={r.id}>{recordLabel(r, today)}</option>
            ))}
          </select>

          {preview && preview.feedback && (
            <>
              <p className="muted share-quote-note">届くことば（書きかえはできません）</p>
              <FeedbackQuote feedback={preview.feedback} />

              <label className="q" htmlFor="share-comment">ひとこと添える（なくても送れます）</label>
              <textarea
                id="share-comment"
                rows={3}
                value={comment}
                placeholder="たとえば「バラが咲いたよ」"
                aria-describedby="share-comment-count"
                onChange={(e) => setComment(e.target.value)}
              />
              <p id="share-comment-count" className={over ? "reply-count over" : "reply-count"}>
                {count} / {COMMENT_MAX_CHARS}字
              </p>

              <fieldset className="recipients">
                <legend className="q">届ける家族</legend>
                {recipients.map((r) => (
                  <label key={r.id} className={`toggle-label${r.already_sent ? " is-sent" : ""}`}>
                    <input
                      type="checkbox"
                      checked={r.already_sent || toUsers.includes(r.id)}
                      disabled={r.already_sent || busy}
                      onChange={(e) => toggleTo(r.id, e.target.checked)}
                    />
                    <span>
                      {r.display_name}
                      {r.already_sent && <span className="sent-mark">（この日の便りは送信済み）</span>}
                    </span>
                  </label>
                ))}
                <p className="muted">
                  {allSent
                    ? "この日の便りは、連携している家族みんなに送ってあります。送り直すときは、家族の日記で前の便りを取り消してください。"
                    : "選んだ人それぞれに、別々の便りとして届きます。同じ日の便りを同じ人に送れるのは1回だけです。"}
                </p>
              </fieldset>

              {preview.record_photo_url && (
                <div className="photo-toggle">
                  <label className="toggle-label">
                    <input
                      type="checkbox"
                      checked={includePhoto}
                      onChange={(e) => setIncludePhoto(e.target.checked)}
                    />
                    <span>写真も一緒に届ける</span>
                  </label>
                  <p className="muted">
                    {includePhoto
                      ? "この写真も便りと一緒に届きます。"
                      : "記録の写真は届きません（ことばとコメントだけが届きます）。"}
                  </p>
                  {includePhoto && (
                    <img className="preview share-photo" src={preview.record_photo_url} alt="一緒に届ける写真" />
                  )}
                </div>
              )}

              <div className="row">
                <button className="primary" onClick={send} disabled={!sendable}>
                  {toUsers.length > 1 ? `選んだ${toUsers.length}人に送る` : "送る"}
                </button>
              </div>
              <p className="muted">
                送った便りは書きかえられません。取り消すと、家族の画面からも消えます（取り消しは「家族の日記」から）。
              </p>
            </>
          )}
          {preview && !preview.feedback && <p className="notice">{preview.unavailable}</p>}
          {error && <p className="error">{error}</p>}
        </div>
      )}
    </section>
  );
}
