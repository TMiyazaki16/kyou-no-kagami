import { useCallback, useEffect, useState } from "react";
import { Analysis, api, TodayWordResponse } from "../api";
import { errText } from "../lib/format";
import { MirrorWord } from "../components/MirrorWord";
import type { Ctx } from "../navigation";

/* ------------------------------------------------------------------ */
/* 今日の一言 (M2)                                                     */
/* ------------------------------------------------------------------ */
export function TodayWord({ go }: Ctx) {
  const [today, setToday] = useState<TodayWordResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    api
      .todayWord()
      .then(setToday)
      .catch(() => setToday(null))
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

  const retry = async (recordId: number) => {
    setError("");
    try {
      await api.analyze(recordId);
      load();
    } catch (e) {
      setError(`今日の一言を作れませんでした（${errText(e)}）。少し時間をおいて、もう一度お試しください。`);
    }
  };

  if (loading) return <p>読み込み中…</p>;
  if (!today || today.kind === null) {
    const recordId = today && today.kind === null ? today.record_id : null;
    return (
      <section>
        <h2>今日の一言</h2>
        {recordId ? (
          <div className="card">
            <p>今日の記録は保存されています。一言はまだできていません。</p>
            <button className="primary big-btn" onClick={() => retry(recordId)}>今日の一言を作る</button>
            {error && <p className="error">{error}</p>}
          </div>
        ) : (
          <p className="muted">今日の記録がまだありません。記録すると、あなたの言葉をもとにした一言がここに届きます。</p>
        )}
      </section>
    );
  }

  const ana: Analysis = today;
  return (
    <section>
      <h2>今日の一言</h2>
      {ana.kind === "voice_notice" ? (
        <div className="card">
          <p className="big">🎙 {ana.notice}</p>
          <button className="primary big-btn" onClick={() => go("record")}>文字でも一言残す</button>
        </div>
      ) : (
        /* 開いたとき、水面に映るように現れる（動きを減らす設定では静止） */
        <MirrorWord text={ana.todays_word ?? ""} large appear />
      )}
      <div className="card">
        <h3>AIが観察したこと</h3>
        <ul className="rings">
          {ana.observations.map((o, i) => (
            <li key={i}>{o}</li>
          ))}
        </ul>
        <p className="muted card-foot">AIは観察だけを返します。判断や診断はしません。</p>
      </div>
    </section>
  );
}
