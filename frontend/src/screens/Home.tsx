import { useEffect, useState } from "react";
import { api, TodayWordResponse, Weekly } from "../api";
import { WeeklyMessage } from "../components/WeeklyMessage";
import type { Ctx } from "../navigation";

/* ------------------------------------------------------------------ */
/* ホーム                                                              */
/* ------------------------------------------------------------------ */
export function Home({ me, go }: Ctx) {
  const [weekly, setWeekly] = useState<Weekly | null>(null);
  const [today, setToday] = useState<TodayWordResponse | null>(null);

  useEffect(() => {
    api.weekly().then(setWeekly).catch(() => {});
    api.todayWord().then(setToday).catch(() => {});
  }, []);

  return (
    <section>
      <h2>こんにちは、{me.display_name}</h2>
      <p className="lead">
        今日の気持ちを、2つの質問に声や文字で残す場所です。記録はまず、あなた自身のためにあります。
      </p>
      {weekly && (
        <div className="card highlight">
          <WeeklyMessage weekly={weekly} />
          <p className="muted">（{weekly.week_start} 〜 {weekly.week_end} の週）</p>
        </div>
      )}
      {today?.kind === "word" && today.todays_word ? (
        <div className="card">
          <h3>今日の一言</h3>
          <p>{today.todays_word}</p>
          <button onClick={() => go("today")}>くわしく見る</button>
        </div>
      ) : today?.kind === "voice_notice" ? (
        <div className="card">
          <h3>🎙 今日の声の記録</h3>
          <p>{today.notice}</p>
          <button onClick={() => go("record")}>文字でも一言残す</button>
        </div>
      ) : today?.has_record ? (
        <div className="card">
          <h3>今日の記録は保存されています</h3>
          <p className="muted">今日の一言はまだできていません。</p>
          <button className="primary" onClick={() => go("today")}>今日の一言を受け取る</button>
        </div>
      ) : (
        <div className="card">
          <h3>今日の記録はまだです</h3>
          <p className="muted">1分で残せます。声でも、文字でも大丈夫です。</p>
          <button className="primary" onClick={() => go("record")}>記録する</button>
        </div>
      )}
    </section>
  );
}
