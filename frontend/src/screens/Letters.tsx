import { useCallback, useEffect, useRef, useState } from "react";
import { api, Letters as LettersData, REPLY_MAX_CHARS, Reply, Share, Stamp } from "../api";
import { charCount, errText, localDateTimeStr } from "../lib/format";
import { markDiarySeen } from "../lib/diarySeen";
import type { Ctx } from "../navigation";

const LIKE = "いいね";

const sentTime = (l: Share) => (l.sent_at ? Date.parse(l.sent_at) || 0 : 0);
const sentLabel = (l: Share) => (l.sent_at ? localDateTimeStr(new Date(l.sent_at)) : "");

/** 自分の便りも家族の便りも、送った時刻の新しい順に1本へまとめる（送信済みの便りだけ。API が sent のものしか返さない） */
export function mergeTimeline(data: LettersData): Share[] {
  const byId = new Map<number, Share>();
  for (const l of [...data.received, ...data.sent]) byId.set(l.id, l);
  return [...byId.values()].sort((a, b) => sentTime(b) - sentTime(a) || b.id - a.id);
}

/* ---- アイコン（装飾。意味は隣の文字で伝える） ------------------------- */
const svgProps = {
  viewBox: "0 0 24 24",
  "aria-hidden": true,
  stroke: "currentColor",
  strokeWidth: 1.9,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

const HeartIcon = ({ filled }: { filled: boolean }) => (
  <svg className="act-icon" {...svgProps} fill={filled ? "currentColor" : "none"}>
    <path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z" />
  </svg>
);

const StampIcon = () => (
  <svg className="act-icon" {...svgProps} fill="none">
    <circle cx="12" cy="12" r="8" />
    <path d="M9 14.5c.8 1 1.8 1.5 3 1.5s2.2-.5 3-1.5" />
    <path d="M9.5 10h.01M14.5 10h.01" />
  </svg>
);

const ReplyIcon = () => (
  <svg className="act-icon" {...svgProps} fill="none">
    <path d="M5 6h14a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-8l-4 3v-3H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1z" />
  </svg>
);

/* ---- 部品 ---------------------------------------------------------- */

/** 名前の頭文字の丸（アイコン画像の代わり。装飾） */
const Avatar = ({ name, mine }: { name: string; mine: boolean }) => (
  <span className={`post-avatar${mine ? " mine" : ""}`} aria-hidden="true">
    {[...name][0] ?? "？"}
  </span>
);

/** 付いたリアクション（だれが・どれを）。数は出さない */
function ReactionList({ stamps }: { stamps: Stamp[] }) {
  if (stamps.length === 0) return null;
  return (
    <ul className="chips" aria-label="付いたリアクション">
      {stamps.map((s) => (
        <li key={s.id} className="chip">
          {s.stamp === LIKE && <span aria-hidden="true">♥ </span>}
          {s.from_name}「{s.stamp}」
        </li>
      ))}
    </ul>
  );
}

/** 返信のスレッド（古い順）。投稿の下に字下げして並べる */
function ReplyThread({ replies }: { replies: Reply[] }) {
  if (replies.length === 0) return null;
  return (
    <ul className="reply-thread" aria-label="返信">
      {replies.map((r) => (
        <li key={r.id} className="reply-item">
          <p className="reply-meta">
            <strong>{r.from_name}</strong> ・ {localDateTimeStr(new Date(r.created_at))}
          </p>
          <p className="reply-text">{r.body}</p>
        </li>
      ))}
    </ul>
  );
}

/* ------------------------------------------------------------------ */
/* 家族の日記                                                          */
/* 本人が「送る」と決めた便りだけが、送った人に関係なく時系列で1本に並ぶ。      */
/* 家族から届いた便り：♡いいね・定型スタンプ・返信ができる                    */
/* 自分の便り：家族が付けたリアクションと返信をその下に表示（既読は出さない）   */
/* ------------------------------------------------------------------ */
export function Letters({ me, onSeen }: Ctx & { onSeen?: () => void }) {
  const [posts, setPosts] = useState<Share[] | null>(null);
  const [stampOptions, setStampOptions] = useState<string[]>([]);
  const [openPanel, setOpenPanel] = useState<Record<number, "stamps" | "reply" | undefined>>({});
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState<number | null>(null);
  const [errors, setErrors] = useState<Record<number, string>>({});
  const replyRefs = useRef<Record<number, HTMLTextAreaElement | null>>({});

  const load = useCallback(() => {
    api
      .letters()
      .then((data) => {
        setPosts(mergeTimeline(data));
        // 開いた時点で見えている、自分の投稿への反応を既読にする（この端末だけ。サーバーには送らない）
        markDiarySeen(data.sent, me.id);
        onSeen?.();
      })
      .catch(() => setPosts([]));
  }, [me.id, onSeen]);

  useEffect(() => {
    load();
    // 「いいね」はハートのボタンにするので、定型スタンプの一覧からは外す
    api.stamps().then((s) => setStampOptions(s.filter((x) => x !== LIKE))).catch(() => {});
  }, [load]);

  const replacePost = (letter: Share) => setPosts((cur) => (cur ?? []).map((l) => (l.id === letter.id ? letter : l)));

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
      replacePost((await api.toggleReaction(shareId, reaction)).letter);
    });

  const reply = (shareId: number) =>
    run(shareId, "返信", async () => {
      const res = await api.addReply(shareId, (drafts[shareId] ?? "").trim());
      replacePost(res.letter);
      setDrafts((d) => ({ ...d, [shareId]: "" }));
      setOpenPanel((p) => ({ ...p, [shareId]: undefined }));
    });

  const togglePanel = (shareId: number, panel: "stamps" | "reply") => {
    const next = openPanel[shareId] === panel ? undefined : panel;
    setOpenPanel((p) => ({ ...p, [shareId]: next }));
    if (next === "reply")
      setTimeout(() => {
        const el = replyRefs.current[shareId];
        el?.focus({ preventScroll: true });
        // 下部タブに隠れないよう、入力欄と「返信する」を画面の中ほどへ
        el?.scrollIntoView({ block: "center", behavior: "smooth" });
      }, 0);
  };

  return (
    <section>
      <h2>家族の日記</h2>
      <p className="lead">あなたと家族が「送る」と決めた便りだけが、ここに並びます。</p>
      {posts !== null && posts.length === 0 && (
        <p className="muted">まだ便りはありません。送りたいときは「共有の確認」からどうぞ。</p>
      )}

      <ol className="stream">
        {(posts ?? []).map((l) => {
          const mine = l.from_user === me.id;
          const stamps = l.stamps ?? [];
          const replies = l.replies ?? [];
          const myStamps = new Set(stamps.filter((s) => s.from_user === me.id).map((s) => s.stamp));
          const liked = myStamps.has(LIKE);
          const panel = openPanel[l.id];
          const text = drafts[l.id] ?? "";
          const count = charCount(text.trim());
          const over = count > REPLY_MAX_CHARS;
          return (
            <li key={l.id} className={mine ? "mine" : "family"}>
              <article className={`card post${mine ? " mine" : " family"}`} aria-label={`${l.from_name}の便り`}>
                <header className="post-head">
                  <Avatar name={l.from_name} mine={mine} />
                  <p className="post-meta">
                    <span className="post-name">{l.from_name}</span>
                    {mine && <span className="post-you">（あなた）</span>}
                    <span className="post-to"> → {mine ? l.to_name : "あなた"}</span>
                    <span className="post-date">{sentLabel(l)}</span>
                  </p>
                </header>

                <p className="letter-body">{l.final_text}</p>
                {l.photo_url && (
                  <img
                    className="letter-photo"
                    src={l.photo_url}
                    alt={mine ? "便りと一緒に届けた写真" : `${l.from_name}が便りと一緒に届けた写真`}
                    loading="lazy"
                  />
                )}

                {/* 自分の投稿：家族が付けたリアクション。家族の投稿：自分が付けたものはボタンの状態で見える */}
                {mine && <ReactionList stamps={stamps} />}

                {!mine && (
                  <>
                    <div className="action-bar" role="group" aria-label="この便りへの反応">
                      <button
                        className={`act-btn like${liked ? " on" : ""}`}
                        aria-pressed={liked}
                        disabled={busy === l.id}
                        onClick={() => toggle(l.id, LIKE)}
                      >
                        <HeartIcon filled={liked} />
                        <span>いいね</span>
                      </button>
                      <button
                        className={`act-btn${panel === "stamps" ? " open" : ""}${
                          [...myStamps].some((s) => s !== LIKE) ? " on-soft" : ""
                        }`}
                        aria-expanded={panel === "stamps"}
                        aria-controls={`stamps-${l.id}`}
                        onClick={() => togglePanel(l.id, "stamps")}
                      >
                        <StampIcon />
                        <span>スタンプ</span>
                      </button>
                      <button
                        className={`act-btn${panel === "reply" ? " open" : ""}`}
                        aria-expanded={panel === "reply"}
                        aria-controls={`reply-box-${l.id}`}
                        onClick={() => togglePanel(l.id, "reply")}
                      >
                        <ReplyIcon />
                        <span>返信</span>
                      </button>
                    </div>

                    {/* 付けたスタンプはパネルを閉じていても見えるようにする（押すと外せる） */}
                    {panel !== "stamps" && [...myStamps].some((s) => s !== LIKE) && (
                      <div className="stamp-chips given" aria-label="付けたスタンプ">
                        {stampOptions
                          .filter((s) => myStamps.has(s))
                          .map((s) => (
                            <button
                              key={s}
                              className="stamp-chip active"
                              aria-pressed
                              disabled={busy === l.id}
                              onClick={() => toggle(l.id, s)}
                            >
                              {s}
                            </button>
                          ))}
                      </div>
                    )}

                    {panel === "stamps" && (
                      <div className="stamp-chips" id={`stamps-${l.id}`} role="group" aria-label="定型スタンプ">
                        {stampOptions.map((s) => {
                          const on = myStamps.has(s);
                          return (
                            <button
                              key={s}
                              className={`stamp-chip${on ? " active" : ""}`}
                              aria-pressed={on}
                              disabled={busy === l.id}
                              onClick={() => toggle(l.id, s)}
                            >
                              {s}
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </>
                )}

                <ReplyThread replies={replies} />

                {!mine && panel === "reply" && (
                  <div className="reply-box" id={`reply-box-${l.id}`}>
                    <label className="visually-hidden" htmlFor={`reply-${l.id}`}>
                      {l.from_name}に返信する
                    </label>
                    <textarea
                      id={`reply-${l.id}`}
                      ref={(el) => {
                        replyRefs.current[l.id] = el;
                      }}
                      className="reply-input"
                      rows={2}
                      value={text}
                      placeholder={`${l.from_name}に返信する`}
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
                  </div>
                )}

                {mine && stamps.length === 0 && replies.length === 0 && (
                  <p className="muted post-empty">返事はまだです。</p>
                )}
                {errors[l.id] && <p className="error">{errors[l.id]}</p>}
              </article>
            </li>
          );
        })}
      </ol>
      <p className="muted">※ 家族のあいだで記録の回数を比べる表示は、このアプリにはありません。</p>
    </section>
  );
}
