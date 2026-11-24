import { ReactNode, useEffect, useId, useState } from "react";
import { QuotedText } from "./QuotedText";
import { WaveLines } from "./WaveLines";

/* ------------------------------------------------------------------ */
/* 水鏡：今日の一言を、静かな水面の上の文字として出す（ホーム・今日の一言）    */
/* - 本文の下に水平線を引き、その下に波線の水面を置く（装飾は aria-hidden）   */
/* - appear：開いたとき、文字が少し下から浮かび、水平線から波紋が3つ広がる。   */
/*   文字そのものは歪ませない                                               */
/* - 映り込み（反転した最後の行）と SVG フィルタのゆらぎは、読みづらいとの    */
/*   判断（2026-11-24 オーナー、#25）で外した                                */
/* - 動きを減らす設定では、浮き上がりも波紋も出さない                         */
/* ------------------------------------------------------------------ */
const APPEAR_MS = 3300;

const prefersReducedMotion = () =>
  typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

export function MirrorWord({
  text,
  label,
  large = false,
  appear = false,
  children,
}: {
  text: string;
  /** カードの見出し（ホームでは「今日の一言」。画面の見出しがあるときは省く） */
  label?: string;
  large?: boolean;
  appear?: boolean;
  children?: ReactNode;
}) {
  const uid = useId().replace(/[^a-zA-Z0-9]/g, "");
  const labelId = `mirror-label-${uid}`;
  const [moving, setMoving] = useState(() => appear && !prefersReducedMotion());

  useEffect(() => {
    if (!moving) return;
    const t = window.setTimeout(() => setMoving(false), APPEAR_MS);
    return () => window.clearTimeout(t);
  }, []); // 開いたときに1回だけ

  return (
    <article
      className={`mirror${large ? " large" : ""}${moving ? " appear" : ""}`}
      aria-labelledby={label ? labelId : undefined}
    >
      {label && (
        <h3 className="mirror-label" id={labelId}>
          {label}
        </h3>
      )}
      <p className="mirror-word">
        <QuotedText text={text} />
      </p>
      <div className="mirror-surface" aria-hidden="true">
        <div className="mirror-horizon" />
        {appear && (
          <svg className="mirror-drops" viewBox="0 0 320 90" focusable="false">
            <ellipse cx="160" cy="26" rx="150" ry="22" />
            <ellipse cx="160" cy="26" rx="150" ry="22" />
            <ellipse cx="160" cy="26" rx="150" ry="22" />
          </svg>
        )}
        <WaveLines className="mirror-lines" />
      </div>
      {children}
    </article>
  );
}
