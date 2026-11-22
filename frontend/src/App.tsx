import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  ApiError,
  Analysis,
  DevLatest,
  FamilyMember,
  Letters as LettersData,
  Me,
  RecordItem,
  session,
  Share,
  TodayWordResponse,
  UNAUTHORIZED_EVENT,
  Weekly,
} from "./api";

type Screen =
  | "home"
  | "record"
  | "today"
  | "list"
  | "reflect"
  | "share"
  | "letters"
  | "dev"
  | "settings"
  | "disclaimer";

const NAV: { id: Screen; label: string }[] = [
  { id: "home", label: "ホーム" },
  { id: "record", label: "記録" },
  { id: "today", label: "今日の一言" },
  { id: "list", label: "記録一覧" },
  { id: "reflect", label: "振り返り" },
  { id: "share", label: "共有の確認" },
  { id: "letters", label: "届いた便り" },
  { id: "dev", label: "仕組みを見る" },
  { id: "settings", label: "設定・家族" },
  { id: "disclaimer", label: "免責表示" },
];

/** H-7: 表示用の日付はローカル時刻で YYYY-MM-DD を組み立てる（toISOString は UTC なので使わない） */
const localDateStr = (d: Date = new Date()) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

const errText = (e: unknown) => (e instanceof ApiError ? e.detail : String(e));

const PLACEHOLDER_PREFIX = "（AI下書きはまだありません";

/** M-5: 録音形式。サーバーの許可リスト（audio/webm, audio/mp4, audio/ogg）に合わせる */
const AUDIO_EXT: Record<string, string> = { "audio/ogg": "ogg", "audio/webm": "webm", "audio/mp4": "m4a" };
const pickRecorderMime = (): string | undefined => {
  if (typeof MediaRecorder === "undefined" || !MediaRecorder.isTypeSupported) return undefined;
  return ["audio/ogg;codecs=opus", "audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find((t) =>
    MediaRecorder.isTypeSupported(t),
  );
};
const baseMime = (t: string) => t.split(";")[0].trim().toLowerCase();

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [family, setFamily] = useState<FamilyMember[]>([]);
  const [checking, setChecking] = useState(session.get() !== null);
  const [screen, setScreen] = useState<Screen>("home");

  const refreshMe = useCallback(async () => {
    const res = await api.me();
    setMe(res.user);
    setFamily(res.family);
  }, []);

  useEffect(() => {
    if (session.get()) {
      refreshMe()
        .catch(() => {
          /* 401 なら authFetch がトークンを消してイベントを出す */
        })
        .finally(() => setChecking(false));
    }
    const onUnauthorized = () => {
      setMe(null);
      setFamily([]);
      setScreen("home");
    };
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, [refreshMe]);

  const onAuthed = (token: string, user: Me) => {
    session.set(token);
    setMe(user);
    setScreen("home");
    refreshMe().catch(() => {});
  };

  const logout = async () => {
    try {
      await api.logout();
    } catch {
      /* サーバーに届かなくても、この端末からはログアウトする */
    }
    session.clear();
    setMe(null);
    setFamily([]);
    setScreen("home");
  };

  if (checking) return <div className="app"><p>読み込み中…</p></div>;

  if (!me) {
    return (
      <div className="app">
        <header className="header">
          <div>
            <h1>きょうのかがみ</h1>
            <p className="tagline">毎日のわたしを映す鏡。届けたい日は、家族にも。</p>
          </div>
        </header>
        <AuthScreen onAuthed={onAuthed} />
      </div>
    );
  }

  const go = (s: Screen) => setScreen(s);
  const ctx: Ctx = { me, family, go, refreshMe };

  return (
    <div className="app">
      <header className="header">
        <h1>きょうのかがみ</h1>
        <div className="user-switch">
          <span className="who">{me.display_name}</span>
          <button onClick={logout}>ログアウト</button>
        </div>
      </header>

      <nav className="nav">
        {NAV.map((n) => (
          <button key={n.id} className={screen === n.id ? "active" : ""} onClick={() => go(n.id)}>
            {n.label}
          </button>
        ))}
      </nav>

      {/* H-5: アカウントが変わったら画面の状態を作り直す */}
      <main className="main" key={me.id}>
        {screen === "home" && <Home {...ctx} />}
        {screen === "record" && <RecordScreen {...ctx} />}
        {screen === "today" && <TodayWord {...ctx} />}
        {screen === "list" && <RecordList />}
        {screen === "reflect" && <Reflect />}
        {screen === "share" && <ShareConfirm {...ctx} />}
        {screen === "letters" && <Letters />}
        {screen === "dev" && <DevView />}
        {screen === "settings" && <Settings {...ctx} />}
        {screen === "disclaimer" && <Disclaimer />}
      </main>
    </div>
  );
}

interface Ctx {
  me: Me;
  family: FamilyMember[];
  go: (s: Screen) => void;
  refreshMe: () => Promise<void>;
}

/* ------------------------------------------------------------------ */
/* ログイン / 登録（未ログイン時）                                       */
/* ------------------------------------------------------------------ */
function AuthScreen({ onAuthed }: { onAuthed: (token: string, user: Me) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    if (mode === "register" && password.length < 8) {
      setError("パスワードは8文字以上にしてください");
      return;
    }
    setBusy(true);
    try {
      const res =
        mode === "login" ? await api.login(email, password) : await api.register(displayName, email, password);
      onAuthed(res.token, res.user);
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="main">
      <section>
        <h2>{mode === "login" ? "ログイン" : "アカウントをつくる"}</h2>
        <p className="lead">
          記録はまず、あなた自身のためのものです。家族に届くのは、あなたが「送る」と決めたものだけです。
        </p>
        <div className="row">
          <button className={mode === "login" ? "active" : ""} onClick={() => setMode("login")}>ログイン</button>
          <button className={mode === "register" ? "active" : ""} onClick={() => setMode("register")}>
            はじめての方（登録）
          </button>
        </div>
        <form className="card" onSubmit={submit}>
          {mode === "register" && (
            <>
              <label className="q" htmlFor="display-name">表示名（家族に見える名前）</label>
              <input
                id="display-name"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                maxLength={30}
                required
                placeholder="例：母さん"
              />
            </>
          )}
          <label className="q" htmlFor="email">メールアドレス</label>
          <input
            id="email"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
          <label className="q" htmlFor="password">
            パスワード{mode === "register" ? "（8文字以上）" : ""}
          </label>
          <input
            id="password"
            type="password"
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            minLength={mode === "register" ? 8 : undefined}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          {error && <p className="error">{error}</p>}
          <button className="primary big-btn" type="submit" disabled={busy}>
            {busy ? "確認しています…" : mode === "login" ? "ログイン" : "登録してはじめる"}
          </button>
        </form>
      </section>
    </main>
  );
}

function WeeklyMessage({ weekly }: { weekly: Weekly }) {
  return <p className="big">{weekly.message}{weekly.days_recorded > 0 ? " 🎉" : " 🌱"}</p>;
}

/* ------------------------------------------------------------------ */
/* ホーム                                                              */
/* ------------------------------------------------------------------ */
function Home({ me, go }: Ctx) {
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

/* ------------------------------------------------------------------ */
/* 記録 (M1)                                                           */
/* ------------------------------------------------------------------ */
function RecordScreen({ go }: Ctx) {
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

/* ------------------------------------------------------------------ */
/* 今日の一言 (M2)                                                     */
/* ------------------------------------------------------------------ */
function TodayWord({ go }: Ctx) {
  const [today, setToday] = useState<TodayWordResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    api
      .todayWord()
      .then(setToday)
      .catch(() => setToday(null))
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

  const retry = async (recordId: number) => {
    setError("");
    try {
      await api.analyze(recordId);
      load();
    } catch (e) {
      setError(`今日の一言を作れませんでした（${errText(e)}）。少し時間をおいて、もう一度お試しください。`);
    }
  };

  if (loading) return <p>読み込み中…</p>;
  if (!today || today.kind === null) {
    const recordId = today && today.kind === null ? today.record_id : null;
    return (
      <section>
        <h2>今日の一言</h2>
        {recordId ? (
          <div className="card">
            <p>今日の記録は保存されています。一言はまだできていません。</p>
            <button className="primary" onClick={() => retry(recordId)}>今日の一言を作る</button>
            {error && <p className="error">{error}</p>}
          </div>
        ) : (
          <p className="muted">今日の記録がまだありません。記録すると、あなたの言葉をもとにした一言がここに届きます。</p>
        )}
      </section>
    );
  }

  const ana: Analysis = today;
  return (
    <section>
      <h2>今日の一言</h2>
      {ana.kind === "voice_notice" ? (
        <div className="card">
          <p className="big">🎙 {ana.notice}</p>
          <button className="primary" onClick={() => go("record")}>文字でも一言残す</button>
        </div>
      ) : (
        <div className="card highlight">
          <p className="big">{ana.todays_word}</p>
        </div>
      )}
      <div className="card">
        <h3>AIが観察したこと</h3>
        <ul>
          {ana.observations.map((o, i) => (
            <li key={i}>{o}</li>
          ))}
        </ul>
        <p className="muted">AIは観察だけを返します。判断や診断はしません。</p>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* 記録一覧 (M2)                                                       */
/* ------------------------------------------------------------------ */
function RecordList() {
  const [records, setRecords] = useState<RecordItem[]>([]);
  useEffect(() => {
    api.records().then(setRecords).catch(() => setRecords([]));
  }, []);

  return (
    <section>
      <h2>記録一覧</h2>
      {records.length === 0 && <p className="muted">まだ記録がありません。</p>}
      {records.map((r) => (
        <div className="card" key={r.id}>
          <p className="date">{r.date}</p>
          <p>🙏 感謝：{r.gratitude || "（未入力）"}</p>
          <p>🌿 気分・体調：{r.mood || "（未入力）"}</p>
          {r.transcript && <p>🎙 声で話したこと：{r.transcript}</p>}
          {r.todays_word && <p className="word">💬 {r.todays_word}</p>}
          {r.audio_url && <audio controls src={r.audio_url} />}
          {r.photo_url && <img className="preview" src={r.photo_url} alt="記録の写真" />}
        </div>
      ))}
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* 振り返り                                                            */
/* ------------------------------------------------------------------ */
function Reflect() {
  const [weekly, setWeekly] = useState<Weekly | null>(null);
  const [records, setRecords] = useState<RecordItem[]>([]);
  useEffect(() => {
    api.weekly().then(setWeekly).catch(() => {});
    api.records().then(setRecords).catch(() => {});
  }, []);

  return (
    <section>
      <h2>振り返り</h2>
      {weekly && (
        <div className="card highlight">
          <WeeklyMessage weekly={weekly} />
          <p className="muted">
            連続記録のプレッシャーはありません。できた日を数えて、そっと置いておく場所です。
          </p>
        </div>
      )}
      <div className="card">
        <h3>これまでの一言</h3>
        {records.filter((r) => r.todays_word).length === 0 && (
          <p className="muted">まだありません。</p>
        )}
        <ul>
          {records
            .filter((r) => r.todays_word)
            .map((r) => (
              <li key={r.id}>
                <span className="date">{r.date}</span> {r.todays_word}
              </li>
            ))}
        </ul>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* 共有の確認                                                          */
/* ------------------------------------------------------------------ */
function ShareConfirm({ family, go }: Ctx) {
  const [share, setShare] = useState<Share | null>(null);
  const [toUser, setToUser] = useState<string>(family.length === 1 ? family[0].id : "");
  const [text, setText] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const sent = share !== null && share.status !== "draft";
  const sendable = text.trim() !== "" && !text.trim().startsWith(PLACEHOLDER_PREFIX);

  const wrap = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(`うまくいきませんでした: ${errText(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const makeDraft = () =>
    wrap(async () => {
      const s = await api.createDraft(toUser || undefined);
      setShare(s);
      setText(s.final_text.startsWith(PLACEHOLDER_PREFIX) ? "" : s.final_text);
      setStatus("");
    });

  const saveEdit = () =>
    wrap(async () => {
      if (!share) return;
      setShare(await api.editShare(share.id, text));
      setStatus("✓ 下書きを保存しました");
    });

  const send = () =>
    wrap(async () => {
      if (!share) return;
      const s = await api.sendShare(share.id, text);
      setShare(s);
      setText(s.final_text);
      setStatus(`✓ ${s.to_name}へ届けました`);
    });

  return (
    <section>
      <h2>共有の確認</h2>
      <p className="lead">
        共有は任意です。AIが作った下書きを、あなたが読んで、直して、「送る」と決めた内容だけが家族に届きます。
      </p>
      {!share && family.length === 0 && (
        <div className="card">
          <p>まだ家族と連携していません。便りは、連携した家族にだけ送れます。</p>
          <button onClick={() => go("settings")}>設定・家族で連携する</button>
        </div>
      )}
      {!share && family.length > 0 && (
        <div className="card">
          {family.length > 1 && (
            <>
              <label className="q" htmlFor="to-user">送る相手</label>
              <select id="to-user" value={toUser} onChange={(e) => setToUser(e.target.value)}>
                <option value="">選んでください</option>
                {family.map((f) => (
                  <option key={f.id} value={f.id}>{f.display_name}</option>
                ))}
              </select>
            </>
          )}
          <button className="primary" onClick={makeDraft} disabled={busy || !toUser}>AIの下書きを作る</button>
          {error && <p className="error">{error}</p>}
        </div>
      )}
      {share && (
        <div className="card">
          <p className="muted">AIの下書き（そのまま送る必要はありません）:</p>
          <p className="draft">{share.draft_text}</p>
          <label className="q">{sent ? "送った内容" : "送る内容（自由に直せます）"}</label>
          <textarea
            rows={4}
            value={text}
            readOnly={sent}
            placeholder="家族に届けたいことを、ひとこと書いてください"
            onChange={(e) => setText(e.target.value)}
          />
          {!sent && (
            <div className="row">
              <button onClick={saveEdit} disabled={busy}>下書きを保存</button>
              <button className="primary" onClick={send} disabled={busy || !sendable}>
                {share.to_name}へ送る
              </button>
            </div>
          )}
          {sent && <p className="muted">送った便りは、あとから書きかえることはできません。</p>}
          {status && <p className="ok">{status}</p>}
          {error && <p className="error">{error}</p>}
        </div>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* 届いた便り                                                          */
/* ------------------------------------------------------------------ */
function Letters() {
  const [letters, setLetters] = useState<LettersData>({ received: [], sent: [] });
  const [stampOptions, setStampOptions] = useState<string[]>([]);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api.letters().then(setLetters).catch(() => setLetters({ received: [], sent: [] }));
  }, []);

  useEffect(() => {
    load();
    api.stamps().then(setStampOptions).catch(() => {});
  }, [load]);

  const stamp = async (shareId: number, s: string) => {
    setError("");
    try {
      await api.addStamp(shareId, s);
      load();
    } catch (e) {
      setError(`スタンプを送れませんでした: ${errText(e)}`);
    }
  };

  const sentDate = (l: Share) => (l.sent_at ? localDateStr(new Date(l.sent_at)) : "");

  return (
    <section>
      <h2>届いた便り</h2>
      {error && <p className="error">{error}</p>}
      {letters.received.length === 0 && (
        <p className="muted">まだ便りは届いていません。相手が「送る」と決めたものだけが、ここに届きます。</p>
      )}
      {letters.received.map((l) => (
        <div className="card" key={l.id}>
          <p className="date">
            {l.from_name}から ・ {sentDate(l)}
          </p>
          <p>{l.final_text}</p>
          <div className="row wrap">
            {stampOptions.map((s) => (
              <button key={s} onClick={() => stamp(l.id, s)}>{s}</button>
            ))}
          </div>
          {l.stamps && l.stamps.length > 0 && (
            <p className="muted">
              返したスタンプ：{l.stamps.map((s) => `「${s.stamp}」`).join(" ")}
            </p>
          )}
        </div>
      ))}

      <h3>あなたの便りへの返事</h3>
      {letters.sent.length === 0 && (
        <p className="muted">まだ便りを送っていません。送りたいときは「共有の確認」からどうぞ。</p>
      )}
      {letters.sent.map((l) => (
        <div className="card" key={l.id}>
          <p className="date">
            {l.to_name}へ ・ {sentDate(l)}
          </p>
          <p className="muted">{l.final_text}</p>
          {l.stamps && l.stamps.length > 0 ? (
            <p className="big">
              {l.stamps.map((s) => (
                <span key={s.id} className="stamp">
                  {s.from_name ?? l.to_name}から「{s.stamp}」
                </span>
              ))}
            </p>
          ) : (
            <p className="muted">返事はまだです。</p>
          )}
        </div>
      ))}
      <p className="muted">※ 家族のあいだで記録の回数を比べる表示は、このアプリにはありません。</p>
    </section>
  );
}

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

function DevView() {
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

/* ------------------------------------------------------------------ */
/* 設定・家族（家族コードの表示と連携）                                  */
/* ------------------------------------------------------------------ */
function Settings({ me, family, refreshMe }: Ctx) {
  const [code, setCode] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const wrap = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setStatus("");
    try {
      await fn();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const link = (e: FormEvent) => {
    e.preventDefault();
    wrap(async () => {
      const res = await api.linkFamily(code);
      setCode("");
      setStatus(
        res.already_linked
          ? `${res.linked.display_name}とは、もう連携しています`
          : `✓ ${res.linked.display_name}と連携しました`,
      );
      await refreshMe();
    });
  };

  const unlink = (f: FamilyMember) => {
    if (!window.confirm(`${f.display_name}との連携を解除しますか？ 解除すると、おたがいに便りを送れなくなります。`)) return;
    wrap(async () => {
      await api.unlinkFamily(f.id);
      setStatus(`${f.display_name}との連携を解除しました`);
      await refreshMe();
    });
  };

  return (
    <section>
      <h2>設定・家族</h2>
      <div className="card">
        <h3>あなたのアカウント</h3>
        <p>表示名：{me.display_name}</p>
        <p className="muted">メール：{me.email}</p>
      </div>
      <div className="card">
        <h3>あなたの家族コード</h3>
        <p className="big code">{me.family_code}</p>
        <p className="muted">
          連携したい家族にだけ伝えてください。このコードを入れた人と連携し、おたがいに便りを送れるようになります。
          連携しても、あなたの記録が自動で届くことはありません。届くのは、あなたが「送る」と決めた便りだけです。
        </p>
      </div>
      <form className="card" onSubmit={link}>
        <h3>家族のコードを入れて連携する</h3>
        <label className="q" htmlFor="family-code">家族コード</label>
        <input
          id="family-code"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          placeholder="例：ABCD-EFGH-JKMN"
          autoComplete="off"
          required
        />
        <button className="primary" type="submit" disabled={busy || !code.trim()}>連携する</button>
      </form>
      <div className="card">
        <h3>連携している家族</h3>
        {family.length === 0 ? (
          <p className="muted">まだいません。</p>
        ) : (
          <ul>
            {family.map((f) => (
              <li key={f.id}>
                {f.display_name}{" "}
                <button onClick={() => unlink(f)} disabled={busy}>連携を解除</button>
              </li>
            ))}
          </ul>
        )}
      </div>
      {status && <p className="ok">{status}</p>}
      {error && <p className="error">{error}</p>}
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* 免責表示                                                            */
/* ------------------------------------------------------------------ */
function Disclaimer() {
  return (
    <section>
      <h2>免責表示</h2>
      <div className="card">
        <ul>
          <li>このアプリは医療機器ではなく、診断や治療を行うものではありません。</li>
          <li>AIはあなたの言葉を観察として返すだけであり、判断や診断は行いません。病名の提示もしません。</li>
          <li>体調について気になることが続くときは、かかりつけの先生にも相談してみてくださいね。</li>
          <li>記録した音声・写真・文章は、あなたが共有を決めた場合を除き、家族を含め誰にも送られません。</li>
          <li>最初の7日間は「いつものあなた」を覚える期間のため、変化についてのコメントは表示されません。</li>
        </ul>
      </div>
    </section>
  );
}
