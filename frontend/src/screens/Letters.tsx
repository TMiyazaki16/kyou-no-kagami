import { useCallback, useEffect, useState } from "react";
import { api, Letters as LettersData, Share } from "../api";
import { errText, localDateStr } from "../lib/format";

/* ------------------------------------------------------------------ */
/* 届いた便り                                                          */
/* ------------------------------------------------------------------ */
export function Letters() {
  const [letters, setLetters] = useState<LettersData>({ received: [], sent: [] });
  const [stampOptions, setStampOptions] = useState<string[]>([]);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api.letters().then(setLetters).catch(() => setLetters({ received: [], sent: [] }));
  }, []);

  useEffect(() => {
    load();
    api.stamps().then(setStampOptions).catch(() => {});
  }, [load]);

  const stamp = async (shareId: number, s: string) => {
    setError("");
    try {
      await api.addStamp(shareId, s);
      load();
    } catch (e) {
      setError(`スタンプを送れませんでした: ${errText(e)}`);
    }
  };

  const sentDate = (l: Share) => (l.sent_at ? localDateStr(new Date(l.sent_at)) : "");

  return (
    <section>
      <h2>届いた便り</h2>
      {error && <p className="error">{error}</p>}
      {letters.received.length === 0 && (
        <p className="muted">まだ便りは届いていません。相手が「送る」と決めたものだけが、ここに届きます。</p>
      )}
      {letters.received.map((l) => (
        <div className="card" key={l.id}>
          <p className="date">
            {l.from_name}から ・ {sentDate(l)}
          </p>
          <p>{l.final_text}</p>
          <div className="row wrap">
            {stampOptions.map((s) => (
              <button key={s} onClick={() => stamp(l.id, s)}>{s}</button>
            ))}
          </div>
          {l.stamps && l.stamps.length > 0 && (
            <p className="muted">
              返したスタンプ：{l.stamps.map((s) => `「${s.stamp}」`).join(" ")}
            </p>
          )}
        </div>
      ))}

      <h3>あなたの便りへの返事</h3>
      {letters.sent.length === 0 && (
        <p className="muted">まだ便りを送っていません。送りたいときは「共有の確認」からどうぞ。</p>
      )}
      {letters.sent.map((l) => (
        <div className="card" key={l.id}>
          <p className="date">
            {l.to_name}へ ・ {sentDate(l)}
          </p>
          <p className="muted">{l.final_text}</p>
          {l.stamps && l.stamps.length > 0 ? (
            <p className="big">
              {l.stamps.map((s) => (
                <span key={s.id} className="stamp">
                  {s.from_name ?? l.to_name}から「{s.stamp}」
                </span>
              ))}
            </p>
          ) : (
            <p className="muted">返事はまだです。</p>
          )}
        </div>
      ))}
      <p className="muted">※ 家族のあいだで記録の回数を比べる表示は、このアプリにはありません。</p>
    </section>
  );
}
