import { useEffect, useState } from "react";
import { api, DevLatest } from "../api";

/* ------------------------------------------------------------------ */
/* 仕組みを見る（開発ビュー）                                          */
/* ------------------------------------------------------------------ */
const FALLBACK_TEXT: Record<string, string> = {
  no_api_key:
    "GEMINI_API_KEY が未設定のため、ローカルモックで生成しました（本人の言葉を引用する決定的な生成です）。",
  gemini_error:
    "Gemini API の呼び出しでエラーが起きたため、ローカルモックに切り替えました（エラー内容はレスポンスの gemini_error に出ています）。",
  guard_ng_word:
    "Gemini の出力に病名・医療的な助言にあたる語が含まれていたため破棄し、ローカルモックに差し替えました。",
  guard_no_quote:
    "Gemini の出力が本人の言葉を引用していなかったため不採用とし、ローカルモックに差し替えました。",
  guard_baseline_change:
    "最初の7日間なのに、Gemini の一言が変化に触れていた（「いつもより」「最近」など）ため不採用とし、ローカルモックに差し替えました。",
};

export function DevView() {
  const [dev, setDev] = useState<DevLatest | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    api
      .devLatest()
      .then(setDev)
      .catch(() => setDev(null))
      .finally(() => setLoaded(true));
  }, []);

  if (!loaded) return <p>読み込み中…</p>;
  const ana = dev?.analysis;
  if (!ana)
    return (
      <section>
        <h2>仕組みを見る</h2>
        <p className="muted">まだ解析がありません。記録すると、ここにAIへの入力と出力が表示されます。</p>
      </section>
    );

  const isGemini = ana.engine === "gemini";
  const explanation = isGemini && ana.kind === "voice_notice"
    ? `Gemini API（${String((ana.request as { model?: string }).model ?? dev?.gemini_model)}）を呼び出しましたが、反映できる言葉がありませんでした。`
    : isGemini
    ? `Gemini API（${String((ana.request as { model?: string }).model ?? dev?.gemini_model)}）で生成し、サーバー側のガード（NG語・引用チェック）を通過しました。`
    : FALLBACK_TEXT[ana.fallback_reason] ?? "ローカルモックで生成しました。";

  return (
    <section>
      <h2>仕組みを見る</h2>
      <div className="card">
        <p>
          実行エンジン：{" "}
          <span className={isGemini ? "badge gemini" : "badge mock"}>{ana.engine}</span>
        </p>
        <p className="muted">{explanation}</p>
        {ana.kind === "voice_notice" && (
          <p className="muted">文字の入力も文字起こしもなかったため、汎用文は出さずに案内文を返しました。</p>
        )}
        <p>対象の記録：{ana.date}（記録をはじめて {ana.baseline_day} 日目）</p>
        <p className="muted">
          最初の7日間は、観察文をサーバー側の定型文に差し替えています。健康への配慮が必要な文脈では、
          AIに書かせず定型文だけをサーバーが付けます。
        </p>
      </div>
      <div className="card">
        <h3>AIへの入力（リクエスト）</h3>
        <pre>{JSON.stringify(ana.request, null, 2)}</pre>
      </div>
      <div className="card">
        <h3>AIからの出力（レスポンス）</h3>
        <pre>{JSON.stringify(ana.response, null, 2)}</pre>
      </div>
    </section>
  );
}
