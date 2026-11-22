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

  const startRec = async () => {
    setError("");
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
        共有は、あなたが「共有の確認」で内容を見て、送ると決めたときだけ行われます。
      </p>
      {existing && (
        <p className="muted">
          今日はもう記録があります。保存すると、今日の記録が新しい内容に置きかわります（声や写真は、新しく付けたときだけ差しかわります）。
        </p>
      )}

      <div className="card">
        <h3>声で残す</h3>
        {!recording ? (
          <button className="primary" onClick={startRec}>🎙 録音をはじめる</button>
        ) : (
          <button className="danger" onClick={stopRec}>⏹ 録音をとめる</button>
        )}
        {audioBlob && !recording && <p className="ok">✓ 音声が用意できました</p>}
        {!audioBlob && existing?.audio_url && <p className="muted">今日の声の記録は保存済みです。</p>}
        <p className="muted">声で答えた内容は、下の欄に文字でも書いてもらえると、AIが言葉を拾いやすくなります。</p>
      </div>

      <div className="card">
        <label className="q">① 今日、感謝できることは？</label>
        <textarea
          value={gratitude}
          onChange={(e) => setGratitude(e.target.value)}
          placeholder="例：娘が電話をくれたこと"
          rows={2}
        />
        <label className="q">② 今日の気分・体調を一言で言うと？</label>
        <textarea
          value={mood}
          onChange={(e) => setMood(e.target.value)}
          placeholder="例：少し疲れたけど元気"
          rows={2}
        />
        <label className="q">写真（任意）</label>
        <input
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
        <button className="primary big-btn" onClick={save} disabled={saving}>
          {saving ? "保存して、AIに渡しています…" : "保存して「今日の一言」を受け取る"}
        </button>
      )}
    </section>
  );
}
