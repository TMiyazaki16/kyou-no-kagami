import { FormEvent, useEffect, useState } from "react";
import { api, Me } from "../api";
import { errText } from "../lib/format";
import { WaterBand } from "../components/WaterBand";

/* ------------------------------------------------------------------ */
/* ログイン / 登録（未ログイン時）                                       */
/* ------------------------------------------------------------------ */
export function AuthScreen({ onAuthed }: { onAuthed: (token: string, user: Me) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // 本番（審査用）は新規登録を止めている。フォームは見せたまま、登録ボタンだけ押せなくする
  const [registrationOpen, setRegistrationOpen] = useState(true);

  useEffect(() => {
    api
      .health()
      .then((h) => setRegistrationOpen(h.registration_enabled !== false))
      .catch(() => undefined); // 読めないときは従来どおり（登録 API 側でも止めている）
  }, []);
  const registerClosed = mode === "register" && !registrationOpen;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    if (registerClosed) return;
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
        <WaterBand title={mode === "login" ? "ログイン" : "アカウントをつくる"} />
        <p className="lead">毎日のわたしを映す鏡。届けたい日は、家族にも。</p>
        <p className="lead">
          記録はまず、あなた自身のためのものです。家族に届くのは、あなたが「送る」と決めたものだけです。
        </p>
        <div className="seg">
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
          {registerClosed && <p className="notice">現在、新規登録は受け付けていません</p>}
          {error && <p className="error">{error}</p>}
          <button className="primary big-btn" type="submit" disabled={busy || registerClosed}>
            {busy ? "確認しています…" : mode === "login" ? "ログイン" : "登録してはじめる"}
          </button>
        </form>
      </section>
    </main>
  );
}
