import { useEffect, useState } from "react";
import { api, RecordItem, Weekly } from "../api";
import { WeeklyMessage } from "../components/WeeklyMessage";

/* ------------------------------------------------------------------ */
/* 振り返り                                                            */
/* ------------------------------------------------------------------ */
export function Reflect() {
  const [weekly, setWeekly] = useState<Weekly | null>(null);
  const [records, setRecords] = useState<RecordItem[]>([]);
  useEffect(() => {
    api.weekly().then(setWeekly).catch(() => {});
    api.records().then(setRecords).catch(() => {});
  }, []);

  return (
    <section>
      <h2>振り返り</h2>
      {weekly && (
        <div className="card week-card">
          <WeeklyMessage weekly={weekly} />
          <p className="muted">
            連続記録のプレッシャーはありません。できた日を数えて、そっと置いておく場所です。
          </p>
        </div>
      )}
      {/* 案B: 1日1枚のカードを日付の見出し付きで縦に並べる（タイムライン） */}
      <h3 className="section-title">これまでの一言</h3>
      {records.filter((r) => r.todays_word).length === 0 && (
        <p className="muted">まだありません。</p>
      )}
      <ul className="timeline">
        {records
          .filter((r) => r.todays_word)
          .map((r) => (
            <li key={r.id} className="card timeline-item">
              <span className="date">{r.date}</span> <span className="timeline-word">{r.todays_word}</span>
            </li>
          ))}
      </ul>
    </section>
  );
}
