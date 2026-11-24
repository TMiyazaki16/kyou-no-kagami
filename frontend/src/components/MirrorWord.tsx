import { ReactNode, useEffect, useId, useRef, useState } from "react";
import { QuotedText } from "./QuotedText";
import { WaveLines } from "./WaveLines";

/* ------------------------------------------------------------------ */
/* 水鏡：今日の一言を、水面に映る文字として出す（ホーム・今日の一言）          */
/* - 本文の下に水平線を引き、最後の行が上下逆さに映る。映り込みは装飾なので     */
/*   aria-hidden（本文は1回だけ読み上げられる）                              */
/* - appear：開いたとき、少し下から浮かび、ゆらぎ（SVG フィルタ）が 2.8 秒で   */
/*   静まり、水平線から波紋が3つ広がる。静まったらフィルタを外す（性能のため）  */
/* - 動きを減らす設定では、ゆらぎも波紋も出さない                             */
/* - 映り込みのゆらぎは静止したもの（常に動くフィルタは古い端末で重いので使わない）*/
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
  const wobbleId = `wobble-${uid}`;
  const softId = `ripple-soft-${uid}`;
  const labelId = `mirror-label-${uid}`;
  const [moving, setMoving] = useState(() => appear && !prefersReducedMotion());
  const defsRef = useRef<SVGSVGElement>(null);

  useEffect(() => {
    if (!moving) return;
    defsRef.current?.querySelectorAll("animate").forEach((a) => (a as SVGAnimationElement).beginElement());
    const t = window.setTimeout(() => setMoving(false), APPEAR_MS);
    return () => window.clearTimeout(t);
  }, []); // 開いたときに1回だけ

  const word = <QuotedText text={text} />;

  return (
    <article
      className={`mirror${large ? " large" : ""}${moving ? " appear" : ""}`}
      aria-labelledby={label ? labelId : undefined}
    >
      <svg ref={defsRef} width="0" height="0" style={{ position: "absolute" }} aria-hidden="true" focusable="false">
        <defs>
          {moving && (
            <filter id={wobbleId} x="-5%" y="-20%" width="110%" height="140%">
              <feTurbulence type="fractalNoise" baseFrequency="0.012 0.09" numOctaves={1} seed={4} result="n">
                <animate
                  attributeName="baseFrequency"
                  dur="2.8s"
                  values="0.012 0.09;0.012 0.05;0.012 0.02"
                  begin="indefinite"
                  fill="freeze"
                />
              </feTurbulence>
              <feDisplacementMap in="SourceGraphic" in2="n" scale={0} xChannelSelector="R" yChannelSelector="G">
                <animate
                  attributeName="scale"
                  dur="2.8s"
                  values="9;5;2;0"
                  keyTimes="0;0.3;0.65;1"
                  begin="indefinite"
                  fill="freeze"
                />
              </feDisplacementMap>
            </filter>
          )}
          <filter id={softId} x="-5%" y="-10%" width="110%" height="120%">
            <feTurbulence type="fractalNoise" baseFrequency="0.008 0.14" numOctaves={1} seed={9} result="n" />
            <feDisplacementMap in="SourceGraphic" in2="n" scale={7} xChannelSelector="R" yChannelSelector="G" />
            <feGaussianBlur stdDeviation={0.6} />
          </filter>
        </defs>
      </svg>

      {label && (
        <h3 className="mirror-label" id={labelId}>
          {label}
        </h3>
      )}
      <p className="mirror-word" style={moving ? { filter: `url(#${wobbleId})` } : undefined}>
        {word}
      </p>
      <div className="mirror-surface" aria-hidden="true">
        <div className="mirror-horizon" />
        <div className="mirror-reflect">
          <p className="mirror-word" style={{ filter: `url(#${softId})` }}>
            {word}
          </p>
        </div>
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
