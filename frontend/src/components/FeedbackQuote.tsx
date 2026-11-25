import type { Feedback } from "../api";
import { QuotedText } from "./QuotedText";

/** 引用の見出し：「11月20日の鏡のことば」（送った日ではなく、記録の日） */
export function feedbackLabel(date: string): string {
  const [, m, d] = date.split("-").map(Number);
  return m && d ? `${m}月${d}日の鏡のことば` : "鏡のことば";
}

/* ------------------------------------------------------------------ */
/* その日の AI フィードバック（今日の一言＋観察）の引用カード              */
/* 共有画面（送る前の確認）と家族の日記（届いた便り）で使う。読み取り専用で、  */
/* 文面はサーバーが写し取ったまま出す（ここで書きかえない）                  */
/* ------------------------------------------------------------------ */
export function FeedbackQuote({ feedback }: { feedback: Feedback }) {
  return (
    <figure className="feedback-quote">
      <figcaption className="feedback-quote-label">{feedbackLabel(feedback.date)}</figcaption>
      <blockquote>
        <p className="feedback-quote-word">
          <QuotedText text={feedback.todays_word} />
        </p>
        {feedback.observations.length > 0 && (
          <ul className="feedback-quote-obs" aria-label="観察">
            {feedback.observations.map((o, i) => (
              <li key={i}>{o}</li>
            ))}
          </ul>
        )}
      </blockquote>
    </figure>
  );
}
