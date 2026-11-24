/* ------------------------------------------------------------------ */
/* 水面の細い波線（装飾）。帯と水鏡で使う。                                */
/* 横に2周期ぶん描いてあるので、幅 200% にして -50% 動かすと継ぎ目なく流れる */
/* ------------------------------------------------------------------ */
export function WaveLines({ className }: { className: string }) {
  return (
    <svg className={className} viewBox="0 0 780 64" preserveAspectRatio="none" aria-hidden="true" focusable="false">
      <g fill="none" stroke="#DCEBF3">
        <path
          d="M0 22 Q 48.75 14 97.5 22 T 195 22 T 292.5 22 T 390 22 T 487.5 22 T 585 22 T 682.5 22 T 780 22"
          strokeOpacity=".28"
          strokeWidth="1.2"
        />
        <path d="M0 38 Q 65 30 130 38 T 260 38 T 390 38 T 520 38 T 650 38 T 780 38" strokeOpacity=".2" strokeWidth="1.2" />
        <path
          d="M0 52 Q 39 47 78 52 T 156 52 T 234 52 T 312 52 T 390 52 T 468 52 T 546 52 T 624 52 T 702 52 T 780 52"
          strokeOpacity=".14"
          strokeWidth="1"
        />
      </g>
    </svg>
  );
}
