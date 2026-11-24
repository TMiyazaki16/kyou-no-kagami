import { useEffect, useState } from "react";
import { api, RecordItem } from "../api";

/* ------------------------------------------------------------------ */
/* 記録一覧 (M2)                                                       */
/* ------------------------------------------------------------------ */
export function RecordList() {
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
          <h3 className="date">{r.date}</h3>
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
