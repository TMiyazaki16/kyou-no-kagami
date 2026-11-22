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
        <div className="card highlight">
          <WeeklyMessage weekly={weekly} />
          <p className="muted">
            連続記録のプレッシャーはありません。できた日を数えて、そっと置いておく場所です。
          </p>
        </div>
      )}
      <div className="card">
        <h3>これまでの一言</h3>
        {records.filter((r) => r.todays_word).length === 0 && (
          <p className="muted">まだありません。</p>
        )}
        <ul>
          {records
            .filter((r) => r.todays_word)
            .map((r) => (
              <li key={r.id}>
                <span className="date">{r.date}</span> {r.todays_word}
              </li>
            ))}
        </ul>
      </div>
    </section>
  );
}
