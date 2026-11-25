"""デモデータ投入スクリプト（きょうのかがみ）。

企画のデモシナリオ:
  母: 1〜7日目は安定（感謝も気分もしっかり書く）
      8〜10日目は「元気」と言いつつ「眠い」が続き、回答が短くなる（9・10日目は感謝欄が空）
  娘: 同じ期間、毎日記録する（変化なし）
  息子: 同じ期間、毎日記録する（安定型。文量が安定し、要注意のことばも感謝の空欄もないのでルールは発動しない）

記録はデモアカウント（3人とも互いに連携済み）に入ります:
  母さん    haha@demo.local   / demo-mama-2026    （ID: mother）
  娘さん    musume@demo.local / demo-musume-2026  （ID: daughter）
  息子さん  musuko@demo.local / demo-musuko-2026  （ID: son）
※ デモ用の公開された認証情報です。本番のDBには投入しないでください。

最終日（既定では JST の今日）が DAYS 日目になるように、過去にさかのぼって投入します。
解析は決定的なローカルモックで行います（GEMINI_API_KEY は投入中だけ無効化）。
最終日の母の記録だけ投入しない `--leave-today` を付けると、デモで「今日の記録」をその場で
入力して8日目以降の観察が出る様子を見せられます。
アカウントは登録 API を通さず Store に直接作るので、REGISTRATION_ENABLED=false でも投入できます。

使い方（backend ディレクトリで）:
  .venv/bin/python scripts/seed_demo.py --reset            # data/app.db を作り直して投入
  .venv/bin/python scripts/seed_demo.py --db /tmp/demo.db   # 別のDBに投入
  .venv/bin/python scripts/seed_demo.py --reset --with-letter  # 便りも入れる（下記）

--with-letter の便り（どれも「その日のフィードバックの引用＋本人のコメント」の新形式）:
  母→娘（7日目）。娘からのスタンプ「うれしい」と返信が1件ずつ付く（母のベルにバッジが出る）
  息子→母（5日目）

MySQL（DB_BACKEND=mysql、接続は MYSQL_* 環境変数）でも Store 経由で同じように投入する。
MySQL の --reset は全テーブルを空にするので、--confirm-mysql-reset <MYSQL_DATABASE の値> が必要:
  DB_BACKEND=mysql MYSQL_HOST=127.0.0.1 MYSQL_USER=... MYSQL_PASSWORD=... MYSQL_DATABASE=kagami \\
    .venv/bin/python scripts/seed_demo.py --reset --confirm-mysql-reset kagami
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, time, timedelta

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MOTHER = [
    # (感謝, 気分・体調)   1〜7日目: 安定
    ("朝、庭のバラがきれいに咲いていて、しばらく眺めていられたこと", "よく眠れて、体も軽く元気です"),
    ("お隣さんが採れたてのきゅうりを分けてくれたこと", "気持ちよく晴れて、散歩もできて元気"),
    ("娘から写真付きのメッセージが届いて、孫の笑顔が見られたこと", "少し肩がこるけれど、気分はおだやか"),
    ("ラジオ体操の仲間と公園でおしゃべりできたこと", "朝から体が動いて、元気に過ごせました"),
    ("久しぶりに煮物を作ったら、思ったよりおいしくできたこと", "のんびりした一日で、気分は上々です"),
    ("図書館で読みたかった本をやっと借りられたこと", "夜もぐっすりで、すっきり元気です"),
    ("友人から電話があって、昔の話で笑い合えたこと", "笑ったおかげか、心も体も軽いです"),
    # 8〜10日目: 「元気」と言いつつ「眠い」が続き、短くなる
    ("天気がよかった", "元気。でも眠い"),
    ("", "元気だけど眠い"),
    ("", "眠い。元気"),
]

DAUGHTER = [
    ("同僚がコーヒーを差し入れてくれたこと", "仕事は忙しいけれど元気"),
    ("帰り道に夕焼けがきれいだったこと", "少し疲れ気味、でも気分はいい"),
    ("母から庭のバラの写真が届いたこと", "ほっとした一日"),
    ("子どもが保育園で描いた絵を見せてくれたこと", "にぎやかで楽しい"),
    ("週末に家族でゆっくり朝ごはんを食べられたこと", "のんびりできて元気"),
    ("プレゼンが無事に終わったこと", "肩の荷がおりてすっきり"),
    ("友だちとランチでたくさん話せたこと", "よく笑ってご機嫌"),
    ("子どもが自分で靴をはけたこと", "朝はバタバタ、でも元気"),
    ("母から電話があって声が聞けたこと", "ちょっと寝不足だけどまあまあ"),
    ("夫が夕飯を作ってくれたこと", "ゆっくりできて気分がいい"),
]

SON = [
    # 安定型：毎日同じくらいの文量。要注意のことば（rules.WATCH_WORDS）は使わず、感謝も毎日書く
    ("駅前のパン屋で焼きたてのクロワッサンが買えたこと", "朝から調子がよく、仕事もはかどった"),
    ("先輩が企画書にていねいな助言をくれたこと", "気持ちが前向きで、体の調子もいい"),
    ("ベランダのバジルに新しい葉が増えていたこと", "ゆったりした一日で、気分はおだやか"),
    ("昼休みに同期と近くの定食屋で話せたこと", "いつもどおり元気で、食欲もある"),
    ("母さんから庭のバラの写真が届いたこと", "懐かしい気持ちになって、心が温かい"),
    ("週末のフットサルでゴールを決められたこと", "よく動いて汗をかき、すっきりした"),
    ("近所の銭湯でゆっくり湯船につかれたこと", "体がほぐれて、気分も晴れやかです"),
    ("姉ちゃんが子どもの動画を送ってくれたこと", "思わず笑って、元気をもらえた一日"),
    ("取引先との打ち合わせがうまくまとまったこと", "仕事が順調で、気持ちに余裕がある"),
    ("帰り道に見た月がとてもきれいだったこと", "落ち着いた一日で、体の調子も上々"),
]

# --with-letter で入れる便り・反応（コメント・返信は本人が書いた体の文）
MOTHER_LETTER_DAY = 7
MOTHER_LETTER_COMMENT = "友だちと電話で昔話をして、たくさん笑ったよ。"
DAUGHTER_STAMP = "うれしい"
DAUGHTER_REPLY = "お母さんの笑い声が聞こえてきそう。週末に電話するね。"
SON_LETTER_DAY = 5
SON_LETTER_COMMENT = "母さんのバラの写真、デスクで見てるよ。"

PEOPLE = (("mother", MOTHER, 8), ("daughter", DAUGHTER, 21), ("son", SON, 22))


def _send_letter(app_main, store, from_user: str, to_user: str, start: date, day: int,
                 comment: str, hour: int) -> dict | None:
    """day 日目の記録のフィードバックを引用して、from_user → to_user の送信済み便りを1通作る。"""
    d = start + timedelta(days=day - 1)
    rec = store.find_record(from_user, d.isoformat())
    feedback = app_main._feedback_snapshot(rec) if rec else None
    if not feedback:
        print(f"便りを作れませんでした（{from_user} の {day}日目に引用できる一言がありません）", file=sys.stderr)
        return None
    sent_at = min(datetime.combine(d, time(hour, 0), tzinfo=app_main.JST), datetime.now(app_main.JST))
    letters = store.create_letters(from_user, rec["id"], [to_user], {
        "snapshot_json": json.dumps(feedback, ensure_ascii=False),
        "comment": comment,
        "photo_path": None,
        "sent_at": sent_at.isoformat(),
    })
    return letters[0]


def main() -> int:
    ap = argparse.ArgumentParser(description="きょうのかがみのデモデータを投入します")
    ap.add_argument("--db", help="SQLite のパス（既定: backend/data/app.db または APP_DB_PATH）")
    ap.add_argument("--reset", action="store_true", help="投入前に作り直す（SQLite はファイルを削除、MySQL は全テーブルを空にする）")
    ap.add_argument("--end-date", help="最終日（YYYY-MM-DD、既定は JST の今日）")
    ap.add_argument("--leave-today", action="store_true", help="最終日の母の記録は投入しない（デモで入力する用）")
    ap.add_argument("--with-letter", action="store_true", help="7日目に母→娘の送信済み便りを1通入れる")
    ap.add_argument("--confirm-mysql-reset", metavar="DATABASE",
                    help="DB_BACKEND=mysql で --reset するときに、消してよいデータベース名（MYSQL_DATABASE と同じ値）を渡す")
    args = ap.parse_args()

    os.environ.pop("GEMINI_API_KEY", None)  # 決定的なモックで投入する
    use_mysql = os.environ.get("DB_BACKEND", "").strip().lower() == "mysql"
    if use_mysql:
        if args.db:
            print("DB_BACKEND=mysql のときは --db を使えません（接続先は MYSQL_* 環境変数で指定）。", file=sys.stderr)
            return 2
        mysql_db = os.environ.get("MYSQL_DATABASE", "")
        if args.reset and args.confirm_mysql_reset != mysql_db:
            print("MySQL の --reset は全テーブルを空にします。確認のため "
                  f"--confirm-mysql-reset <データベース名> を付けてください（対象: {mysql_db or '未設定'}）。\n"
                  "デモアカウントは公開パスワードなので、本番のデータベースには投入しないでください。", file=sys.stderr)
            return 2
    else:
        db_path = os.path.abspath(
            args.db or os.environ.get("APP_DB_PATH") or os.path.join(BACKEND_DIR, "data", "app.db")
        )
        os.environ["APP_DB_PATH"] = db_path
        if args.reset and os.path.exists(db_path):
            os.remove(db_path)

    sys.path.insert(0, BACKEND_DIR)
    from app import auth  # noqa: E402
    from app import main as app_main  # noqa: E402  (DB の設定を決めてから import する)

    store = app_main.store
    target = store.describe() if use_mysql else db_path  # type: ignore[attr-defined]
    if use_mysql and args.reset:
        store.reset_all()  # type: ignore[attr-defined]
    if not args.reset and any(store.list_records(a["id"]) for a in auth.DEMO_ACCOUNTS):
        print(f"既に記録があります: {target}\n作り直す場合は --reset を付けてください。", file=sys.stderr)
        return 1
    auth.ensure_demo_accounts(store)  # 母さん・娘さん・息子さんを作って、全員を互いに連携する

    end = date.fromisoformat(args.end_date) if args.end_date else app_main.today_jst()
    n = len(MOTHER)
    start = end - timedelta(days=n - 1)

    for i in range(n):
        d = start + timedelta(days=i)
        for user_id, data, hour in PEOPLE:
            if user_id == "mother" and args.leave_today and i == n - 1:
                continue
            gratitude, mood = data[i]
            # 未来の時刻にしない（解析より新しい記録＝「解析し直し待ち」と扱われるため）
            created = min(datetime.combine(d, time(hour, 0), tzinfo=app_main.JST),
                          datetime.now(app_main.JST)).isoformat()
            rec, _ = store.upsert_record({
                "user_id": user_id, "date": d.isoformat(), "gratitude": gratitude, "mood": mood,
                "transcript": "", "audio_path": None, "photo_path": None, "created_at": created,
            })
            ana = app_main.run_analysis(rec)
            if user_id == "mother":
                extra = [o for o in ana["observations"] if "ここ" in o or "最近" in o]
                print(f"{d} 母 {i + 1:>2}日目  観察ルール: {len(extra)}件" + (f"  {extra}" if extra else ""))

    if args.with_letter:
        # 便りは「その日のフィードバックの引用（送信時点で固定）＋本人のコメント」（API の POST /api/shares と同じ形）
        letter = _send_letter(app_main, store, "mother", "daughter", start, MOTHER_LETTER_DAY,
                              MOTHER_LETTER_COMMENT, hour=20)
        if letter:
            # 娘からの反応（スタンプ＋返信）。母でログインするとベルのバッジと通知パネルに出る
            store.toggle_stamp(letter["id"], "daughter", DAUGHTER_STAMP)
            store.add_reply(letter["id"], "daughter", DAUGHTER_REPLY)
        _send_letter(app_main, store, "son", "mother", start, SON_LETTER_DAY, SON_LETTER_COMMENT, hour=22)

    print(f"\n投入しました: {target}（{start} 〜 {end}、{n}日分）")
    for acc in auth.DEMO_ACCOUNTS:
        print(f"  デモアカウント {acc['display_name']}: {acc['email']}（パスワードは README を参照）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
