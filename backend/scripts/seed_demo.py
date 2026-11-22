"""デモデータ投入スクリプト（きょうのかがみ）。

企画のデモシナリオ:
  母: 1〜7日目は安定（感謝も気分もしっかり書く）
      8〜10日目は「元気」と言いつつ「眠い」が続き、回答が短くなる（9・10日目は感謝欄が空）
  娘: 同じ期間、毎日記録する

記録はデモアカウント（連携済み）に入ります:
  母さん  haha@demo.local   / demo-mama-2026    （ID: mother）
  娘さん  musume@demo.local / demo-musume-2026  （ID: daughter）
※ デモ用の公開された認証情報です。本番のDBには投入しないでください。

最終日（既定では JST の今日）が DAYS 日目になるように、過去にさかのぼって投入します。
解析は決定的なローカルモックで行います（GEMINI_API_KEY は投入中だけ無効化）。
最終日の母の記録だけ投入しない `--leave-today` を付けると、デモで「今日の記録」をその場で
入力して8日目以降の観察が出る様子を見せられます。

使い方（backend ディレクトリで）:
  .venv/bin/python scripts/seed_demo.py --reset            # data/app.db を作り直して投入
  .venv/bin/python scripts/seed_demo.py --db /tmp/demo.db   # 別のDBに投入
  .venv/bin/python scripts/seed_demo.py --reset --with-letter  # 母→娘の送信済み便りも1通入れる
"""
from __future__ import annotations

import argparse
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


def main() -> int:
    ap = argparse.ArgumentParser(description="きょうのかがみのデモデータを投入します")
    ap.add_argument("--db", help="SQLite のパス（既定: backend/data/app.db または APP_DB_PATH）")
    ap.add_argument("--reset", action="store_true", help="投入前に SQLite ファイルを削除して作り直す")
    ap.add_argument("--end-date", help="最終日（YYYY-MM-DD、既定は JST の今日）")
    ap.add_argument("--leave-today", action="store_true", help="最終日の母の記録は投入しない（デモで入力する用）")
    ap.add_argument("--with-letter", action="store_true", help="7日目に母→娘の送信済み便りを1通入れる")
    args = ap.parse_args()

    db_path = os.path.abspath(
        args.db or os.environ.get("APP_DB_PATH") or os.path.join(BACKEND_DIR, "data", "app.db")
    )
    os.environ["APP_DB_PATH"] = db_path
    os.environ.pop("GEMINI_API_KEY", None)  # 決定的なモックで投入する
    if args.reset and os.path.exists(db_path):
        os.remove(db_path)

    sys.path.insert(0, BACKEND_DIR)
    from app import auth  # noqa: E402
    from app import main as app_main  # noqa: E402  (APP_DB_PATH を決めてから import する)

    store = app_main.store
    if not args.reset and any(store.list_records(u) for u in ("mother", "daughter")):
        print(f"既に記録があります: {db_path}\n作り直す場合は --reset を付けてください。", file=sys.stderr)
        return 1
    auth.ensure_demo_accounts(store)  # 母さん・娘さんを作って連携済みにする

    end = date.fromisoformat(args.end_date) if args.end_date else app_main.today_jst()
    n = len(MOTHER)
    start = end - timedelta(days=n - 1)

    for i in range(n):
        d = start + timedelta(days=i)
        for user_id, data, hour in (("mother", MOTHER, 8), ("daughter", DAUGHTER, 21)):
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
        rec7 = store.find_record("mother", (start + timedelta(days=6)).isoformat())
        share = store.create_share({
            "from_user": "mother", "to_user": "daughter", "record_id": rec7["id"] if rec7 else None,
            "draft_text": "今日は友だちと電話で昔話をして、たくさん笑いました。",
        })
        store.update_share(share["id"], {
            "status": "sent",
            "sent_at": datetime.combine(start + timedelta(days=6), time(20, 0),
                                        tzinfo=app_main.JST).isoformat(),
        })

    print(f"\n投入しました: {db_path}（{start} 〜 {end}、{n}日分）")
    for acc in auth.DEMO_ACCOUNTS:
        print(f"  デモアカウント {acc['display_name']}: {acc['email']}（パスワードは README を参照）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
