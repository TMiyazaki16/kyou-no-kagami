"""変化への気づきルール（pandas なし・Python のみ）。

出力は「判定」ではなく、本人に返す「観察文」のリスト。
- 最初の BASELINE_DAYS 日（記録した日数）は『いつものあなた』を覚える期間なので、
  どのルールも発動しない。
- 病名・医療的助言は一切書かない。健康への配慮が必要と思われる文脈では
  `care_context=True` を返し、定型文の付与はサーバー側（analysis.py）が行う。
"""
from __future__ import annotations

BASELINE_DAYS = 7

# ルールA: 回答の総文字数がベースライン平均の半分以下の日が、何日続いたら観察するか
SHORT_RATIO = 0.5
SHORT_MIN_STREAK = 2
# ルールB: 直近7日（ベースライン後）で感謝欄が空の日が何日あれば観察するか
EMPTY_GRATITUDE_MIN = 2
# ルールC: 特定のことばが何日続いたら観察するか（ベースライン中に2日以上出ていた語は「いつものこと」として除外）
WATCH_WORDS = ["眠い", "ねむい", "だるい", "疲れ", "つかれ", "しんどい", "眠れ"]
WORD_MIN_STREAK = 2
WORD_BASELINE_MAX = 1


def _text(rec: dict) -> str:
    return "".join((rec.get(k) or "").strip() for k in ("gratitude", "mood", "transcript"))


def _length(rec: dict) -> int:
    return len("".join(_text(rec).split()))


def _gratitude_empty(rec: dict) -> bool:
    return not (rec.get("gratitude") or "").strip() and not (rec.get("transcript") or "").strip()


def _one_per_day(history: list[dict]) -> list[dict]:
    """日付昇順・1日1件（同日に複数あれば最新）に正規化。"""
    by_date: dict[str, dict] = {}
    for r in sorted(history, key=lambda r: (r["date"], r.get("id") or 0)):
        by_date[r["date"]] = r
    return [by_date[d] for d in sorted(by_date)]


def _trailing(days: list[dict], pred) -> int:
    n = 0
    for r in reversed(days):
        if not pred(r):
            break
        n += 1
    return n


def evaluate(history: list[dict]) -> dict:
    """history: 対象日までの本人の記録（対象日の記録を含む）。

    returns {"observations": [...], "fired": [rule ids], "care_context": bool, "day": n}
    """
    days = _one_per_day(history)
    day_n = len(days)
    out = {"observations": [], "fired": [], "care_context": False, "day": day_n}
    if day_n <= BASELINE_DAYS:
        return out

    baseline, after = days[:BASELINE_DAYS], days[BASELINE_DAYS:]
    today = days[-1]

    # --- ルールA: 回答がいつもより短い日が続いている -----------------------
    base_avg = sum(_length(r) for r in baseline) / len(baseline)
    if base_avg > 0:
        limit = base_avg * SHORT_RATIO
        streak = _trailing(after, lambda r: _length(r) <= limit)
        if streak >= SHORT_MIN_STREAK:
            out["fired"].append("short_answers")
            out["observations"].append(
                f"ここ{streak}日、回答がいつもより短めです"
                f"（はじめの{BASELINE_DAYS}日間は平均 約{round(base_avg)}文字、今日は{_length(today)}文字）。"
            )

    # --- ルールB: 感謝欄が空の日 ------------------------------------------
    recent = after[-7:]
    empty_days = sum(1 for r in recent if _gratitude_empty(r))
    if empty_days >= EMPTY_GRATITUDE_MIN:
        out["fired"].append("empty_gratitude")
        out["observations"].append(
            f"最近{len(recent)}日のうち{empty_days}日は、感謝の欄が空でした。"
            "書けない日があっても大丈夫です。そのまま置いておきます。"
        )

    # --- ルールC: 特定のことばが続けて出てくる -----------------------------
    seen_groups: set[str] = set()
    for word in WATCH_WORDS:
        group = word[:1]  # 眠い/眠れ, 疲れ/つかれ 等の重複観察を避ける簡易グルーピング
        if group in seen_groups:
            continue
        baseline_hits = sum(1 for r in baseline if word in _text(r))
        if baseline_hits > WORD_BASELINE_MAX:
            continue
        streak = _trailing(after, lambda r, w=word: w in _text(r))
        if streak >= WORD_MIN_STREAK:
            seen_groups.add(group)
            window = after[-streak:]
            with_genki = all("元気" in _text(r) for r in window)
            prefix = "『元気』ということばと一緒に、" if with_genki else ""
            out["fired"].append(f"word:{word}")
            out["observations"].append(
                f"{prefix}『{word}』ということばが、ここ{streak}日続けて出てきています。"
            )
            out["care_context"] = True

    if len(out["fired"]) >= 2:
        out["care_context"] = True
    return out
