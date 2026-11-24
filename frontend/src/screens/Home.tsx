import { useEffect, useState } from "react";
import { api, TodayWordResponse, Weekly } from "../api";
import { MirrorWord } from "../components/MirrorWord";
import { WaterBand } from "../components/WaterBand";
import { WeekCard } from "../components/WeekCard";
import type { Ctx } from "../navigation";

const MicIcon = () => (
  <svg className="btn-icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
    <rect x="9" y="3" width="6" height="11" rx="3" />
    <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
  </svg>
);

const LEAD = "今日の気持ちを、2つの質問に声や文字で残す場所です。記録はまず、あなた自身のためにあります。";

/* ------------------------------------------------------------------ */
/* ホーム                                                              */
/* 上から：水面の帯（あいさつ）→ 今日のカード → 説明 → 週のカード            */
/* 今日のカードは状態で出し分ける（記録前＝記録カード／記録後＝水鏡の一言）    */
/* 記録カードは帯に半分重ねる。水鏡は帯と同じ深い色なので、説明文をはさむ     */
/* ------------------------------------------------------------------ */
export function Home({ me, go }: Ctx) {
  const [weekly, setWeekly] = useState<Weekly | null>(null);
  const [today, setToday] = useState<TodayWordResponse | null>(null);

  useEffect(() => {
    api.weekly().then(setWeekly).catch(() => {});
    api.todayWord().then(setToday).catch(() => {});
  }, []);

  const hasWord = today?.kind === "word" && !!today.todays_word;
  const lead = <p className="lead">{LEAD}</p>;

  return (
    <section>
      <WaterBand title={`こんにちは、${me.display_name}`} />
      {hasWord ? (
        <>
          {lead}
          <MirrorWord text={today?.todays_word ?? ""} label="今日の一言">
            <button className="text-btn" onClick={() => go("today")}>くわしく見る</button>
          </MirrorWord>
        </>
      ) : (
        <>
          {today?.kind === "voice_notice" ? (
            <div className="card lifted">
              <h3>🎙 今日の声の記録</h3>
              <p>{today.notice}</p>
              <button className="primary big-btn" onClick={() => go("record")}>文字でも一言残す</button>
            </div>
          ) : today?.has_record ? (
            <div className="card lifted">
              <h3>今日の記録は保存されています</h3>
              <p className="muted">今日の一言はまだできていません。</p>
              <button className="primary big-btn" onClick={() => go("today")}>今日の一言を受け取る</button>
            </div>
          ) : (
            <div className="card lifted">
              <h3>今日の記録はまだです</h3>
              <p className="muted">1分で残せます。声でも、文字でも大丈夫です。</p>
              <button className="primary big-btn hero" onClick={() => go("record")}>
                <MicIcon />
                記録する
              </button>
            </div>
          )}
          {lead}
        </>
      )}
      {weekly && (
        <WeekCard weekly={weekly}>
          <p className="muted">（{weekly.week_start} 〜 {weekly.week_end} の週）</p>
        </WeekCard>
      )}
    </section>
  );
}
