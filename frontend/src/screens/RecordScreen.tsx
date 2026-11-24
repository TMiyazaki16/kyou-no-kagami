import { useEffect, useRef, useState } from "react";
import { api, RecordItem } from "../api";
import { AUDIO_EXT, baseMime, pickRecorderMime } from "../lib/audio";
import { errText } from "../lib/format";
import type { Ctx } from "../navigation";

/* ------------------------------------------------------------------ */
/* 記録 (M1)                                                           */
/* ------------------------------------------------------------------ */
export function RecordScreen({ go }: Ctx) {
  const [gratitude, setGratitude] = useState("");
  const [mood, setMood] = useState("");
  const [photo, setPhoto] = useState<File | null>(null);
  const [photoUrl, setPhotoUrl] = useState<string | null>(null);
  const [audioBlob, setAudioBlob] = useState<Blob | null>(null);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [discarded, setDiscarded] = useState(false);
  const [recording, setRecording] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [savedId, setSavedId] = useState<number | null>(null);
  const [analyzeError, setAnalyzeError] = useState("");
  const [existing, setExisting] = useState<RecordItem | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  // 今日すでに記録があれば、その内容を下書きとして表示する（M-2: 保存すると上書き）
  useEffect(() => {
    Promise.all([api.todayWord(), api.records()])
      .then(([t, recs]) => {
        const rec = recs.find((r) => r.date === t.date) ?? null;
        setExisting(rec);
        if (rec) {
          setGratitude(rec.gratitude);
          setMood(rec.mood);
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => () => {
    if (photoUrl) URL.revokeObjectURL(photoUrl);
  }, [photoUrl]);

  // 録音を保存前に聞くための URL。録り直し・破棄・画面離脱で古い URL を破棄する
  useEffect(() => {
    if (!audioBlob) {
      setAudioUrl(null);
      return;
    }
    const url = URL.createObjectURL(audioBlob);
    setAudioUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [audioBlob]);

  const startRec = async () => {
    setError("");
    setDiscarded(false);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const preferred = pickRecorderMime();
      const rec = preferred ? new MediaRecorder(stream, { mimeType: preferred }) : new MediaRecorder(stream);
      chunksRef.current = [];
      rec.ondataavailable = (e) => chunksRef.current.push(e.data);
      rec.onstop = () => {
        const type = baseMime(rec.mimeType || preferred || "audio/webm");
        setAudioBlob(new Blob(chunksRef.current, { type }));
        stream.getTracks().forEach((t) => t.stop());
      };
      recorderRef.current = rec;
      rec.start();
      setRecording(true);
    } catch {
      setError("マイクを使えませんでした。下の文字入力でも記録できます。");
    }
  };

  const stopRec = () => {
    recorderRef.current?.stop();
    setRecording(false);
  };

  // 未保存の録音をやめる。保存済みの声には触れない（保存しても音声は送らない）
  const discardRec = () => {
    setAudioBlob(null);
    setDiscarded(true);
  };

  const runAnalyze = async (recordId: number) => {
    setAnalyzeError("");
    setSaving(true);
    try {
      await api.analyze(recordId);
      go("today");
    } catch (e) {
      setAnalyzeError(`記録は保存されています。今日の一言の作成だけ、うまくいきませんでした（${errText(e)}）。`);
    } finally {
      setSaving(false);
    }
  };

  const save = async () => {
    if (!gratitude.trim() && !mood.trim() && !audioBlob && !existing?.audio_url) {
      setError("質問への答えを、声か文字で残してください。");
      return;
    }
    setError("");
    setAnalyzeError("");
    const form = new FormData();
    // 記録するのはログイン中の本人（サーバーがトークンから決める）。日付もサーバーが JST で決める（H-7）
    form.append("gratitude", gratitude);
    form.append("mood", mood);
    if (audioBlob) {
      const ext = AUDIO_EXT[baseMime(audioBlob.type)];
      if (!ext) {
        setError(`この端末の録音形式（${audioBlob.type || "不明"}）には対応していません。文字で残してください。`);
        return;
      }
      form.append("audio", audioBlob, `memo.${ext}`);
    }
    if (photo) form.append("photo", photo);
    setSaving(true);
    let rec: RecordItem;
    try {
      rec = await api.createRecord(form);
    } catch (e) {
      setError(`保存できませんでした: ${errText(e)}`);
      setSaving(false);
      return;
    }
    setSavedId(rec.id);
    await runAnalyze(rec.id);
  };

  return (
    <section>
      <h2>今日の記録</h2>
      <div className="notice">🔒 この記録は、まだ誰にも届いていません</div>
      <p className="muted">
        共有は、あなたが「家族への共有」で内容を見て、送ると決めたときだけ行われます。
      </p>
      {existing && (
        <p className="muted">
          今日はもう記録があります。保存すると、今日の記録が新しい内容に置きかわります（声や写真は、新しく付けたときだけ差しかわります）。
        </p>
      )}

      {/* 声で残す：水面に声を落とす。録音中はボタンから同心円の波紋が広がる */}
      <div className="card">
        <h3>声で残す</h3>
        <div className="pond">
          <button className={recording ? "rec-btn on" : "rec-btn"} onClick={recording ? stopRec : startRec}>
            <span className="rec-disc" aria-hidden="true">
              <span className="rec-ring" />
              <span className="rec-ring" />
              <span className="rec-ring" />
              {recording ? (
                <svg viewBox="0 0 24 24" fill="currentColor">
                  <rect x="6" y="6" width="12" height="12" rx="2.5" />
                </svg>
              ) : (
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <rect x="9" y="3" width="6" height="11" rx="3" />
                  <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
                </svg>
              )}
            </span>
            <span className="rec-label">{recording ? "⏹ 録音をとめる" : "🎙 録音をはじめる"}</span>
          </button>
        </div>
        {/* 録音中はプレイヤーを出さない（再生した音をマイクが拾わないように） */}
        {!recording && audioUrl && (
          <>
            <p className="ok">✓ 新しい録音ができました。保存する前に聞いて確かめられます。</p>
            {existing?.audio_url && (
              <p className="muted">保存すると、今日の声はこの新しい録音に差しかわります。</p>
            )}
            <audio controls src={audioUrl} aria-label="新しい録音" />
            <div className="row">
              <button onClick={discardRec}>この録音をやめる</button>
            </div>
          </>
        )}
        {!recording && !audioBlob && discarded && (
          <p className="muted">
            {existing?.audio_url
              ? "新しい録音をやめました。保存済みの声はそのまま残ります。"
              : "録音をやめました。保存しても声は付きません（文字の記録になります）。"}
          </p>
        )}
        {!recording && !audioBlob && existing?.audio_url && (
          <>
            <p className="muted">今日の声の記録は保存済みです。聞いてから、録り直すかどうか決められます。</p>
            <audio controls src={existing.audio_url} aria-label="保存済みの声" />
          </>
        )}
        <p className="muted">声で答えた内容は、下の欄に文字でも書いてもらえると、AIが言葉を拾いやすくなります。</p>
      </div>

      <div className="card">
        <label className="q" htmlFor="q-gratitude">① 今日、感謝できることは？</label>
        <textarea
          id="q-gratitude"
          value={gratitude}
          onChange={(e) => setGratitude(e.target.value)}
          placeholder="例：娘が電話をくれたこと"
          rows={2}
        />
      </div>
      <div className="card">
        <label className="q" htmlFor="q-mood">② 今日の気分・体調を一言で言うと？</label>
        <textarea
          id="q-mood"
          value={mood}
          onChange={(e) => setMood(e.target.value)}
          placeholder="例：少し疲れたけど元気"
          rows={2}
        />
      </div>
      {/* 写真は点線の枠（任意であることが分かる見た目）。
          ブラウザ標準の英語のボタンは隠し、枠全体を押せるラベルにする */}
      <div className="card photo-field">
        <label className="photo-picker" htmlFor="photo-input">
          <svg className="photo-icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 8.5A1.5 1.5 0 0 1 5.5 7h2l1.5-2h6l1.5 2h2A1.5 1.5 0 0 1 20 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 17.5z" />
            <circle cx="12" cy="12.5" r="3.5" />
          </svg>
          <span>写真（任意）</span>
        </label>
        <input
          id="photo-input"
          className="visually-hidden"
          type="file"
          accept="image/jpeg,image/png,image/webp"
          onChange={(e) => {
            const f = e.target.files?.[0] ?? null;
            setPhoto(f);
            setPhotoUrl(f ? URL.createObjectURL(f) : null);
          }}
        />
        {photoUrl && <img className="preview" src={photoUrl} alt="添付写真のプレビュー" />}
      </div>

      {error && <p className="error">{error}</p>}
      {analyzeError && savedId !== null ? (
        <div className="card">
          <p className="error">{analyzeError}</p>
          <button className="primary" onClick={() => runAnalyze(savedId)} disabled={saving}>
            {saving ? "作っています…" : "今日の一言だけ、もう一度作る"}
          </button>
        </div>
      ) : (
        /* 保存ボタンは画面の下（タブのすぐ上）に固定。入力中も位置は変えない（styles.css） */
        <div className="save-bar">
          <button className="primary big-btn" onClick={save} disabled={saving}>
            {saving ? "保存して、AIに渡しています…" : "保存して「今日の一言」を受け取る"}
          </button>
        </div>
      )}
    </section>
  );
}
