import { useCallback, useEffect, useRef, useState } from "react";
import { api, FamilyMember, Me, session, UNAUTHORIZED_EVENT } from "./api";
import { Ctx, DiaryFocus, Screen } from "./navigation";
import { BottomTabs } from "./components/BottomTabs";
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
import { DiaryBell } from "./components/DiaryBell";
import { NotificationPanel } from "./components/NotificationPanel";
import { countNewReactions, DiaryNotice, listDiaryNotices, markDiarySeen } from "./lib/diarySeen";

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [family, setFamily] = useState<FamilyMember[]>([]);
  const [checking, setChecking] = useState(session.get() !== null);
  const [screen, setScreen] = useState<Screen>("home");
  // 通知バッジ：自分の投稿に付いた新しい反応の数（既読はこの端末の localStorage だけ）
  const [newReactions, setNewReactions] = useState(0);
  const clearBadge = useCallback(() => setNewReactions(0), []);
  // 通知パネル（ベルから開く）。notices が null の間は読み込み中
  const [panelOpen, setPanelOpen] = useState(false);
  const [notices, setNotices] = useState<DiaryNotice[] | null>(null);
  // 家族の日記で強調する便り（通知の行から開いた便り・送った直後の便り）と、上部に一度だけ出すお知らせ。
  // Letters が受け取ったらすぐ消すので、日記を離れて戻っても出ない（永続化しない）
  const [diaryFocus, setDiaryFocus] = useState<DiaryFocus | null>(null);
  const clearFocus = useCallback(() => setDiaryFocus(null), []);
  const bellWrap = useRef<HTMLSpanElement>(null);
  const closePanel = useCallback(() => {
    setPanelOpen(false);
    bellWrap.current?.querySelector("button")?.focus(); // 閉じたらベルへフォーカスを戻す
  }, []);

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
    setNewReactions(0);
    setPanelOpen(false);
  };

  // 画面を切り替えたとき・1分ごとにバッジを数え直す（「家族の日記」を開いている間は Letters が既読にする）
  const meId = me?.id;
  useEffect(() => {
    if (!meId || screen === "letters") return;
    const refresh = () =>
      api
        .letters()
        .then((d) => setNewReactions(countNewReactions(d.sent, meId)))
        .catch(() => {});
    refresh();
    const timer = window.setInterval(refresh, 60_000);
    return () => window.clearInterval(timer);
  }, [meId, screen]);

  // 深い藍の細いヘッダー（画面の幅いっぱい。中身は本文の列にそろえる）
  const appbar = (right?: JSX.Element) => (
    <header className="appbar">
      <div className="appbar-in">
        <h1 className="brand">きょうのかがみ</h1>
        {right}
      </div>
    </header>
  );

  if (checking)
    return (
      <div className="app">
        {appbar()}
        <main className="main"><p>読み込み中…</p></main>
      </div>
    );

  if (!me) {
    return (
      <div className="app">
        {appbar()}
        <AuthScreen onAuthed={onAuthed} />
      </div>
    );
  }

  const go = (s: Screen) => {
    setScreen(s);
    window.scrollTo(0, 0); // 画面を切り替えたら先頭から読めるようにする
  };
  const openDiary = (focus: DiaryFocus) => {
    setDiaryFocus(focus);
    go("letters");
  };
  const ctx: Ctx = { me, family, go, openDiary, refreshMe };

  // ベル：パネルを開いた時点で、いま見えている反応を既読にしてバッジを消す（この端末の localStorage だけ）
  const openPanel = () => {
    setPanelOpen(true);
    setNotices(null);
    const id = me.id;
    api
      .letters()
      .then((d) => {
        setNotices(listDiaryNotices(d.sent, id)); // 「新着」の印は既読にする前の状態で決める
        markDiarySeen(d.sent, id);
        setNewReactions(0);
      })
      .catch(() => setNotices([]));
  };
  const pickNotice = (letterId: number) => {
    setPanelOpen(false);
    openDiary({ ids: [letterId] });
  };

  return (
    <div className="app app-authed">
      {appbar(
        <>
          <span ref={bellWrap} className="bell-wrap">
            <DiaryBell count={newReactions} open={panelOpen} onToggle={() => (panelOpen ? closePanel() : openPanel())} />
          </span>
          <span className="who">{me.display_name}</span>
          <button onClick={logout}>ログアウト</button>
        </>,
      )}

      {/* H-5: アカウントが変わったら画面の状態を作り直す */}
      <main className="main" key={me.id}>
        {screen === "home" && <Home {...ctx} />}
        {screen === "record" && <RecordScreen {...ctx} />}
        {screen === "today" && <TodayWord {...ctx} />}
        {screen === "list" && <RecordList />}
        {screen === "reflect" && <Reflect />}
        {screen === "share" && <ShareConfirm {...ctx} />}
        {screen === "letters" && (
          <Letters {...ctx} onSeen={clearBadge} focus={diaryFocus} onFocused={clearFocus} />
        )}
        {screen === "dev" && <DevView />}
        {screen === "settings" && <Settings {...ctx} />}
        {screen === "disclaimer" && <Disclaimer />}
      </main>

      {panelOpen && <NotificationPanel notices={notices} onClose={closePanel} onPick={pickNotice} />}

      {/* 主要画面は下部タブ、残りは「その他」シート（画面の数・名前は NAV のまま） */}
      <BottomTabs screen={screen} go={go} />
    </div>
  );
}
