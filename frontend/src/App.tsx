import { useCallback, useEffect, useState } from "react";
import { api, FamilyMember, Me, session, UNAUTHORIZED_EVENT } from "./api";
import { Ctx, NAV, Screen } from "./navigation";
import { AuthScreen } from "./screens/AuthScreen";
import { Home } from "./screens/Home";
import { RecordScreen } from "./screens/RecordScreen";
import { TodayWord } from "./screens/TodayWord";
import { RecordList } from "./screens/RecordList";
import { Reflect } from "./screens/Reflect";
import { ShareConfirm } from "./screens/ShareConfirm";
import { Letters } from "./screens/Letters";
import { DevView } from "./screens/DevView";
import { Settings } from "./screens/Settings";
import { Disclaimer } from "./screens/Disclaimer";

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
