import { WaveLines } from "./WaveLines";

/* ------------------------------------------------------------------ */
/* 水面の帯（ホーム・ログインの上部）。見出しだけを置き、説明文は帯の外に出す  */
/* （グラデーションの一番明るい端でも白文字 8.28:1。本文は帯に載せない）      */
/* ------------------------------------------------------------------ */
export function WaterBand({ title }: { title: string }) {
  return (
    <div className="band">
      <div className="band-glint" aria-hidden="true" />
      <h2>{title}</h2>
      <WaveLines className="band-waves" />
    </div>
  );
}
