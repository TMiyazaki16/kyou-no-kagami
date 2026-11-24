import { useEffect, useState } from "react";
import { api, RecordItem, Weekly } from "../api";
import { WeekCard } from "../components/WeekCard";

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
        <WeekCard weekly={weekly}>
          <p className="muted">
            連続記録のプレッシャーはありません。できた日を数えて、そっと置いておく場所です。
          </p>
        </WeekCard>
      )}
      {/* 1日1枚のカードを、時の流れ（水の筋）に沿って縦に並べる */}
      <h3 className="section-title">これまでの一言</h3>
      {records.filter((r) => r.todays_word).length === 0 && (
        <p className="muted">まだありません。</p>
      )}
      <ul className="stream">
        {records
          .filter((r) => r.todays_word)
          .map((r) => (
            <li key={r.id}>
              <div className="card">
                <p className="date">{r.date}</p>
                <p>{r.todays_word}</p>
              </div>
            </li>
          ))}
      </ul>
    </section>
  );
}
