"""Daily analysis: turns one day's memo into 「今日の一言」.

Two engines:
- "gemini": used when env GEMINI_API_KEY is set. Calls the Gemini API.
            音声があれば inline_data で渡し、文字起こしと一言の生成を1回の呼び出しで行う。
            主モデル（GEMINI_MODEL）→ 代替モデル（GEMINI_FALLBACK_MODEL）の順に呼び、
            503・429・5xx・タイムアウト等の一次的エラーは各モデルで短い指数バックオフで再試行する。
            全滅したらモックに落とす（fallback_reason = "gemini_error"）。
- "mock":   deterministic local fallback. Still references the SPECIFIC words
            of that day's memo (the gratitude content and the mood phrase),
            never generic encouragement.

AI principles (from the final proposal):
- The AI observes; it never judges or diagnoses. No disease names, no medical
  advice beyond the fixed generic phrase below (attached by the server only).
- Days 1-7 are a baseline-learning period: no change callouts at all.

Gemini の出力はそのまま使わず、後段ガード（`guard_gemini`）で検査する:
  (a) NG語（病名・医療助言）→ 破棄してモックへ
  (b) 引用チェック（本人の言葉の一部が含まれているか）→ 不採用ならモックへ
  (c) ベースライン期間中の observations はサーバー側の定型観察文に差し替え
  (d) 健康への配慮が必要な文脈では、FIXED_CARE_PHRASE をサーバー側で付与
  (e) ベースライン期間中に今日の一言が変化に触れていたら（「いつもより」「最近」等）不採用→モック
  (f) 疲れ・体調についての断定調（「疲れているようです」等。「かもしれ」や問いかけの形でない文）→ 不採用→モック
  (g) 外見へのコメント（「痩せた」「老けた」「顔色」等。写真があってもなくても）→ 不採用→モック
  (a)(e)(g) は、本人の言葉をそのまま「」で引用した部分を除いて検査する。(f) は直近の記録の言葉の引用も除く。

直近の流れ（#9）：ベースライン後は、過去 RECENT_CONTEXT_DAYS 日の記録の要約（recent_days）と、
変化検知ルールの発火結果から作った傾向の手がかり（trends）をプロンプトに添える。
傾向がある日だけ、今日の映し返しに添えて「少し疲れているのかもしれませんね」程度のやわらかさで触れる。

写真（#11）：記録に写真があれば inline_data で一緒に渡す（主モデル・代替モデルとも）。拾うのは本人の「様子」
（表情の明るさ・姿勢・部屋の明るさ等）だけで、言葉・声と並ぶ材料の一つとして添える。外見へのコメントは (g) で止める。
写真を受け付けない恒常的エラー（400 等）のときは、写真を外して同じモデルで続行する（写真は任意なので音声より先に外す）。
モックは写真の中身を見ないので、写真が届いたことにだけ触れる。

観察候補（#23）：観察の「候補出し」は Gemini、「採用」はサーバー。Gemini は observation_candidates
（各 {text, quote, date}、最大 MAX_CANDIDATES 件）を提案し、`evaluate_candidates` が候補ごとに検査する：
引用の実在（quote が date の日の記録に逐語で含まれる）、NG語、外見、断定調、ベースライン中の変化への言及、
ルール観察との重複。落ちた候補だけを捨て、今日の一言や他の候補には影響しない。採用した候補はルール観察の
後ろに並べる。care_context（定型文の付与）はルール由来だけで決める。モックは候補を出さない。

回数ガード（#34）：呼び出し元（main.run_analysis）が `quota` を渡すと、実 Gemini を呼ぶ直前に1回だけ呼ぶ。
枠が取れなければ（dict が返れば）Gemini を呼ばずにモックに落とす（fallback_reason = "quota_user" / "quota_global"）。
フォールバック連鎖・再試行は、1回の解析でまとめて1回と数える（枠の確保はモデルのループの外で1回）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from datetime import date
from typing import Callable

from . import rules

BASELINE_DAYS = rules.BASELINE_DAYS
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
DEFAULT_GEMINI_FALLBACK_MODEL = "gemini-3.1-flash-lite"

# 一次的エラー（503・429・5xx・タイムアウト等）の再試行。主モデル → 代替モデル → モックの順に落とす。
# 間隔は短く保つ：503 の多くは数秒で回復する一方、nginx の proxy_read_timeout（120s）以内に
# 解析全体を終える必要があるため。
GEMINI_MAX_RETRIES = 2              # 各モデルの再試行回数（初回を含めて最大3回）
GEMINI_BACKOFF_SEC = (1.0, 2.0)     # 再試行前の待ち（指数バックオフ：1s → 2s）。1モデルあたり最大 3s
GEMINI_CALL_TIMEOUT_SEC = 25.0      # 1回の呼び出しのタイムアウト（Lite 系は音声込みでも数秒で返る）
GEMINI_TOTAL_BUDGET_SEC = 100.0     # Gemini 全体（全モデル・全再試行）の上限。残りをモデル数で等分する
_MIN_ATTEMPT_SEC = 3.0              # 残り時間がこれ未満なら、そのモデルではもう呼ばない
_TRANSIENT_HTTP = {408, 429}        # 5xx に加えて一次的とみなす HTTP ステータス

# テストで差し替える（実時間スリープしない）
_sleep = time.sleep
_monotonic = time.monotonic

# The only health-related wording the AI may ever produce (fixed phrase).
FIXED_CARE_PHRASE = "気になることが続くときは、かかりつけの先生にも相談してみてくださいね。"

# 音声のみで文字がない（かつ文字起こしできなかった）ときに、今日の一言の代わりに返す案内文
VOICE_NOTICE = "声の記録を受け取りました。文字でも一言残すと、今日の一言にあなたの言葉が反映されます"

SYSTEM_RULES = (
    "あなたは本人の自己認知を助ける『鏡』です。カウンセラーのように、やわらかく受けとめる視座で"
    "本人の言葉を映し返します。観察と映し返しだけを返し、判断・診断・評価はしません。"
    "病名や医療的な助言（受診・薬・病院などへの言及を含む）は一切出しません。\n"
    "『今日の一言』は日記の要約ではありません。本人が自分では気づいていないかもしれない面を、"
    "そっと映し返す短い一言です。次のように作ってください。\n"
    "1. 本人が書いた（話した）言葉を「」で具体的にそのまま引用する。\n"
    "2. その言葉の裏にある気持ちやニュアンスを拾って映し返す。"
    "（例：「元気だけど眠い」なら、元気さと眠さが同居していること自体に目を向ける。"
    "「そこそこ」「なんとか」のような言い回しや「！」にも、そのときの気持ちが表れる）\n"
    "3. 本人が気づいていなさそうな点に、やわらかく光を当てたり言い換えたりする（リフレーミング）。"
    "決めつけず「〜かもしれませんね」「〜のようですね」のように書く。\n"
    "4. 締めは、答えを求めない短い問いかけにしてもよい（毎回でなくてよい）。\n"
    "【文体】目の前の本人に直接語りかける、温かく親しみのある言葉で書きます。敬語（です・ます）は保ち、"
    "「〜ですね」「〜でしたね」「〜かもしれませんね」のように本人に話しかける形にします"
    "（例：「元気そうですね！」「やわらかい光の中で過ごせたんですね」）。"
    "三人称の観察レポート調（「〜が向けられています」「〜が見られます」「〜がうかがえます」"
    "「〜という様子です」等）や、本人を「この方」「本人」「利用者」と呼ぶ書き方はしません。"
    "呼びかけが要るときは「あなた」とします。"
    "この文体は、今日の一言・観察（observations）・観察の候補・写真や声の様子に触れる文のすべてに使います。"
    "「元気そうですね」のような様子の印象は、本人の言葉や写真から自然に言えるときだけにし、"
    "下の「書かないこと」（疲れ・体調の断定など）はそのまま守ります。\n"
    "書かないこと：アドバイスや指示（「〜しましょう」「〜してみては」等）、"
    "決まり文句の励まし（「がんばりましたね」等）、気持ちの断定、良し悪しの評価、"
    "体調の断定（「疲れているようです」「疲れていますね」等）やその原因の推測。\n"
    "長さは2〜3文、全体で120字程度まで。\n"
    "【役割分担】あなたの役割は次の2つだけです。"
    "①今日の一言（本人の言葉の映し返し）を文章にすること。"
    "②観察の候補（observation_candidates）を提案すること。候補には、根拠にした本人の言葉を"
    "逐語で（一字一句そのまま）quote に、その言葉があった記録の日付を date に必ず添えます。\n"
    "あなたの役割ではないこと：観察を最終的に決めること（候補を採用するかどうかは、アプリが根拠の言葉を"
    "確かめて決めます）、判定・診断・助言、受診や医療への言及。\n"
)

# 観察候補（#23）。Gemini は候補を出すだけで、採用はサーバー（evaluate_candidates）が決める
MAX_CANDIDATES = 3
CANDIDATE_RULES = (
    "観察の候補（observation_candidates）について：本人が自分では気づいていないかもしれない、"
    "言葉の選び方・言葉の中で同居しているもの・数日の流れなどの気づきを、最大3件まで提案してください。"
    "見つからなければ空配列にします（無理に作らない）。\n"
    "- text：本人に返す1文。本人の言葉を「」で引用して根拠を示し、言い切らずに"
    "「〜かもしれませんね」「〜でしょうか」の推し量りまでにします。"
    "本人に直接語りかける です・ます の文にし、「〜が見られます」のようなレポート調にはしません。\n"
    "- quote：根拠にした本人の言葉を、記録からそのまま抜き出したもの（言い換え・要約をしない）。\n"
    "- date：その言葉があった記録の日付（YYYY-MM-DD）。\n"
    "候補でも、病名・医療的な表現・受診の勧め・助言・指示・外見（体型・顔色など）へのコメント・"
    "体調や疲れの断定（「疲れているようです」等）は書きません。\n"
)

# (a) NG語。誤検出しやすい短いひらがな（「がん」=がんばる、「うつ」=うつくしい 等）は入れない。
NG_WORDS = [
    # 病名・診断系
    "うつ病", "鬱", "認知症", "痴呆", "不眠症", "睡眠障害", "糖尿", "高血圧", "低血圧", "脳梗塞",
    "心筋梗塞", "癌", "がん検診", "腫瘍", "病気", "疾患", "症状", "障害", "更年期", "自律神経",
    "貧血", "熱中症", "脱水", "感染", "診断", "病名", "発症", "認知機能",
    # 医療助言系
    "病院", "医者", "医師", "受診", "通院", "診察", "治療", "薬", "服用", "処方", "サプリ", "検査",
    "かかりつけ", "専門家に相談", "専門医", "相談してください",
]

# (b) 引用チェックで「引用」とみなさない、ありふれた部分文字列
QUOTE_MIN_LEN = 2
_QUOTE_STOP = {
    "今日", "きょう", "気分", "体調", "感謝", "ありがとう", "あり", "がと", "した", "です", "ます",
    "ました", "こと", "もの", "ので", "から", "けど", "でも", "とても", "少し", "すこし", "一日",
}
_HIRAGANA_ONLY = re.compile(r"^[぀-ゟ]+$")
_STRIP = re.compile(r"[\s、。，．,.!！?？「」『』（）()・…〜~ー\-]+")

# 直近の流れ（#9）：ベースライン後に、過去何日分の記録をプロンプトの文脈に入れるか（暦日）
RECENT_CONTEXT_DAYS = 7

# 傾向にふれるときの指示（直近の記録・傾向の手がかりを渡す日だけプロンプトに足す）
TREND_RULES = (
    "直近の記録は、今日を数日の流れの中で見るための参考です。"
    "数日にわたる傾向（同じ言葉が続いている、言葉が短くなっている等）がはっきり読み取れるときだけ、"
    "今日の言葉の映し返しに添えて、やわらかく一言だけ触れてください"
    "（例：「ここ数日、『眠い』が続いていますね」「少し疲れているのかもしれませんね」）。"
    "傾向は言い切らず、「〜かもしれませんね」の推し量りまでにします。"
    "「疲れているようです」「疲れていますね」のような言い切り、原因の推測、評価、指示は書きません。"
    "傾向がはっきりしない日は過去に触れず、今日の言葉の映し返しだけにしてください。"
    "今日の言葉の引用は必ず入れます（過去の言葉だけを引用しない）。"
)

# (f) 疲れ・体調についての断定調。AI 自身の地の文で、これらの語を含む文は
# 「かもしれ」等の推し量りか問いかけの形でなければ不採用にする。
STATE_CUES = (
    "疲れ", "つかれ", "疲労", "だるさ", "しんどさ", "眠そう", "ねむそう", "寝不足", "睡眠不足",
    "眠れていな", "眠りが浅", "弱って", "落ち込んで", "無理をして", "無理して", "頑張りすぎ",
    "がんばりすぎ", "体調", "調子が悪", "元気がな", "元気がありませ",
)
_HEDGE_MARKS = ("かもしれ", "でしょうか", "？", "?")
_SENTENCE = re.compile(r"[^。！!？?\n]+[。！!？?]*")

# アップロード時に許可している音声・写真の MIME（main.py / uploads.py と同じ集合）
AUDIO_MIMES = {"audio/webm", "audio/mp4", "audio/ogg"}
PHOTO_MIMES = {"image/jpeg", "image/png", "image/webp"}

# 写真の扱い（写真を渡すときだけプロンプトに足す）
PHOTO_RULES = (
    "添付の写真は、本人が今日の記録に任意で添えた1枚です。写真から拾ってよいのは、そのときの『様子』だけです"
    "（例：表情の明るさ、姿勢、部屋や光の明るさ、写っているもの）。"
    "外見（体型・痩せた／太った・老けた・顔色・肌・髪・しわ等）には一切触れません。"
    "写っている人が誰かを推測したり、写真から体調や気持ちを判断したりもしません"
    "（「元気そうですね」のような、やわらかい様子の印象までにします）。"
    "写真だけで結論を出さず、本人の言葉（と声）の映し返しを主にして、写真の様子は添える程度にしてください"
    "（例：「やわらかい光が差し込むお部屋で過ごせたんですね」）。"
    "写真の様子も本人に語りかける形で書きます。"
    "「写真：すっきりと明るい室内で、まっすぐな視線が向けられています。」のような三人称のレポート調にせず、"
    "「写真：明るいお部屋で、まっすぐこちらを見てくれていますね。」のように書きます。"
    "言葉と写真の様子が違って見えても、どちらが本当かを決めず、両方をそのまま並べるだけにします。"
    "観察（observations）で写真に触れるときは「写真：」で始まる1項目までにしてください。\n"
)

# (g) 外見へのコメント。AI 自身の地の文（本人の言葉の引用を除く）にあれば不採用にする。
# 「くま」「しわ」「クマ」（ぬいぐるみ等、写真に写りうる物）のような誤検出しやすい語は、複合の形だけを入れる。
APPEARANCE_WORDS = (
    "痩せ", "やせて", "やせた", "やせ細", "ほっそり", "太っ", "太り", "ふっくら", "肥満", "体型", "体重",
    "老け", "ふけて", "ふけた", "老い", "老化", "年をとっ", "歳をとっ", "年老", "若々し", "若く見え",
    "顔色", "血色", "青白", "土気色", "やつれ", "げっそり", "頬がこけ", "こけて", "むくみ", "むくん",
    "肌荒れ", "肌つや", "肌の色", "肌の調子", "シワ", "しわが", "しわの", "白髪", "目の下", "クマが", "クマができ",
    "見た目", "外見", "容姿", "顔つき",
)

# モック・ベースラインの定型観察文で、写真が届いていることにだけ触れる（中身は見ていない）
PHOTO_RECEIVED_OBSERVATION = "写真：1枚、添えてくれましたね"


def _short(text: str, limit: int = 40) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def gemini_model() -> str:
    return os.environ.get("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL


def gemini_fallback_model() -> str:
    return os.environ.get("GEMINI_FALLBACK_MODEL", "").strip() or DEFAULT_GEMINI_FALLBACK_MODEL


def gemini_models() -> list[str]:
    """呼び出し順（主モデル → 代替モデル）。同じ名前なら1回だけ。"""
    primary, fallback = gemini_model(), gemini_fallback_model()
    return [primary] if fallback == primary else [primary, fallback]


# --------------------------------------------------------------------------
# Guards
# --------------------------------------------------------------------------

def find_ng_words(*texts: str) -> list[str]:
    joined = "\n".join(t or "" for t in texts)
    return [w for w in NG_WORDS if w in joined]


# (e) ベースライン期間中に「今日の一言」で変化に触れていないか（引用部分は除いて検査）
BASELINE_CHANGE_WORDS = ["いつもより", "前回", "前より", "最近", "変化", "ここ数日", "この数日"]

_QUOTED = re.compile(r"[「『]([^」』]*)[」』]")


def strip_user_quotes(text: str, answer_texts: list[str]) -> str:
    """本人の言葉をそのまま引用した「…」『…』部分を取り除く。

    本人が「病院の帰りに」と書いたのを AI がそのまま引用しただけで NG 語扱いにしないため。
    AI 自身が書いた部分（引用の外側、または本人の言葉ではない引用）は残して検査する。
    """
    answers = [_STRIP.sub("", a or "") for a in answer_texts]

    def repl(m: re.Match) -> str:
        inner = _STRIP.sub("", m.group(1))
        return "" if inner and any(inner in a for a in answers) else m.group(0)

    return _QUOTED.sub(repl, text or "")


def has_quote(answer_texts: list[str], word: str) -> str | None:
    """回答から抽出した2文字以上の部分文字列が word に含まれていれば、その部分文字列を返す。

    ありふれた語（_QUOTE_STOP）と、2文字のひらがなだけの部分文字列は引用とみなさない。
    最長一致から探すので、返り値は「一番長く引用できている部分」。
    """
    target = _STRIP.sub("", word or "")
    if not target:
        return None
    best: str | None = None
    for ans in answer_texts:
        a = _STRIP.sub("", ans or "")
        n = len(a)
        for length in range(n, QUOTE_MIN_LEN - 1, -1):
            if best and length <= len(best):
                break
            for i in range(0, n - length + 1):
                s = a[i:i + length]
                if s in _QUOTE_STOP:
                    continue
                if length == 2 and _HIRAGANA_ONLY.match(s):
                    continue
                if s in target:
                    best = s
                    break
            if best and len(best) == length:
                break
    return best


def find_assertive_state(*texts: str) -> list[str]:
    """(f) 疲れ・体調を言い切っている文を返す（引用部分は呼び出し側で除いておく）。"""
    hits: list[str] = []
    for t in texts:
        for sent in _SENTENCE.findall(t or ""):
            if any(c in sent for c in STATE_CUES) and not any(h in sent for h in _HEDGE_MARKS):
                hits.append(sent.strip())
    return hits


def find_appearance_words(*texts: str) -> list[str]:
    """(g) 外見へのコメント語を返す（引用部分は呼び出し側で除いておく）。"""
    joined = "\n".join(t or "" for t in texts)
    return [w for w in APPEARANCE_WORDS if w in joined]


def baseline_observations(record: dict, baseline_day: int) -> list[str]:
    """(c) ベースライン期間のサーバー側定型観察文。"""
    g = _short(record.get("gratitude", ""))
    m = _short(record.get("mood", ""), 30)
    obs = [
        f"感謝のことば：『{g}』" if g else "感謝のことば：今日は文字での入力はありませんでした",
        f"気分・体調のことば：『{m}』" if m else "気分・体調のことば：今日は文字での入力はありませんでした",
    ]
    if (record.get("transcript") or "").strip():
        obs.append(f"声で話したこと：『{_short(record['transcript'], 40)}』")
    if record.get("photo_path"):
        obs.append(PHOTO_RECEIVED_OBSERVATION)
    obs.append(
        f"いまは『いつものあなた』を覚えている期間です（{baseline_day}日目／{BASELINE_DAYS}日間）。"
        "変化についてのコメントはまだしません。"
    )
    return obs


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def analyze(
    record: dict,
    baseline_day: int,
    previous: dict | None,
    history: list[dict] | None = None,
    audio: tuple[bytes, str] | None = None,
    photo: tuple[bytes, str] | None = None,
    quota: Callable[[], dict | None] | None = None,
) -> dict:
    """Returns {todays_word, kind, notice, observations, engine, fallback_reason,
    transcript, request, response}.

    audio / photo は (中身, MIME)。中身はリクエストのログ（request）には残さない。
    quota は実 Gemini を呼ぶ直前に1回だけ呼ぶ（枠が取れたら None、超えていたら {"scope": "user" | "global", ...}）。"""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    in_baseline = baseline_day <= BASELINE_DAYS
    request = {
        "user_id": record["user_id"],
        "date": record["date"],
        "baseline_day": baseline_day,
        "baseline_period": in_baseline,
        "inputs": {
            "gratitude": record.get("gratitude", ""),
            "mood": record.get("mood", ""),
            "transcript": record.get("transcript", ""),
            "has_audio": bool(audio),
            "audio_mime": audio[1] if audio else None,
            "has_photo": bool(photo or record.get("photo_path")),
            "photo_mime": photo[1] if photo else None,
        },
        "rules": SYSTEM_RULES,
    }
    change = rules.evaluate(history or [record])
    request["change_rules"] = {"fired": change["fired"], "care_context": change["care_context"]}
    # 直近の流れ（#9）。ベースライン中は変化に触れないので、文脈も傾向も渡さない
    request["recent_days"] = [] if in_baseline else recent_days(record, history or [record])
    trends = [] if in_baseline else recent_trends(history or [record], change)
    request["trends"] = trends

    result: dict | None = None
    candidates: list[str] = []  # 採用した観察候補（Gemini の一言が採用されたときだけ）
    # 回数ガード（#34）。数えるのは実 Gemini を呼ぶ直前の1回だけ（連鎖・再試行はまとめて1回）
    quota_denied = quota() if api_key and quota else None
    if quota_denied:
        result = _analyze_mock(record, baseline_day, previous, trends)
        result["engine"] = "mock (quota fallback)"
        result["fallback_reason"] = f"quota_{quota_denied['scope']}"
        result["response"]["quota"] = quota_denied
    elif api_key:
        models = gemini_models()
        request["model_chain"] = models
        request["model"] = models[0]
        attempts: list[dict] = []
        g, last_error = None, None
        start = _monotonic()
        for i, model in enumerate(models):
            # 残り時間をまだ試していないモデルで等分する（主モデルが粘っても代替に時間を残す）
            remaining = GEMINI_TOTAL_BUDGET_SEC - (_monotonic() - start)
            deadline = _monotonic() + remaining / (len(models) - i)
            try:
                g = _analyze_gemini(api_key, request, record, audio, model, deadline, attempts, photo)
                break
            except Exception as exc:  # 次のモデルへ。全滅したらモック
                last_error = exc
        if g is None:  # fall back to mock, but say so in the dev view
            result = _analyze_mock(record, baseline_day, previous, trends)
            result["engine"] = "mock (gemini-error fallback)"
            result["fallback_reason"] = "gemini_error"
            result["response"]["gemini_error"] = _redact(str(last_error), api_key)
            result["response"]["gemini_attempts"] = attempts
        else:
            # 「仕組みを見る」が表示するモデル名は、実際に応答したモデルにする
            request["model"] = g["response"]["model"]
            g["response"]["gemini_attempts"] = attempts
            # ガードは、どのモデルが応答しても同じものを通す
            result = _apply_guards(g, record, baseline_day, previous, trends, request["recent_days"])
            # 観察候補（#23）。今日の一言のガードとは別の、候補だけに効く層。一言がモックに
            # 差し替わったとき（モックは候補を出さない）は、検査結果だけ残して採用しない
            adoptable = result["engine"] == "gemini" and result["kind"] == "word"
            items = evaluate_candidates(
                g.get("observation_candidates"), record=record, transcript=result.get("transcript") or "",
                history=history or [record], recent=request["recent_days"], baseline_day=baseline_day,
                change=change, adoptable=adoptable,
                not_adoptable_reason="engine_fallback" if result["engine"] != "gemini" else "no_text")
            result["response"].setdefault("guard", {})["candidates"] = items
            candidates = [it["text"] for it in items if it["adopted"]]
    else:
        result = _analyze_mock(record, baseline_day, previous, trends)
        result["fallback_reason"] = "no_api_key"

    # 当日のルールを文字起こし込みで再評価する（#24）。文字起こしは Gemini の結果でしか得られないので、
    # プロンプトに渡した change_rules / trends は呼び出し前の評価のまま。当日の観察と (d) の判定にだけ効かせる。
    # 記録に文字起こしが既にあるとき（main.py は上書きしない）は、最初の評価に含まれているので何もしない
    new_transcript = (result.get("transcript") or "").strip()
    if new_transcript and not (record.get("transcript") or "").strip():
        before = change
        change = rules.evaluate(_with_transcript(history or [record], record, new_transcript))
        result["response"]["rules_reevaluation"] = {
            "applied": True,
            "reason": "transcript",
            "before": {"fired": before["fired"], "care_context": before["care_context"]},
            "after": {"fired": change["fired"], "care_context": change["care_context"]},
        }

    # (c) ベースライン期間中は observations をサーバー定型文に差し替え（どのエンジンでも）
    if in_baseline:
        result["observations"] = baseline_observations(
            {**record, "transcript": result.get("transcript") or record.get("transcript", "")},
            baseline_day,
        )
    else:
        # ルール観察 → AI の観察候補の順。候補は定型文の付与（care_context）には関わらない
        result["observations"] = list(result["observations"]) + change["observations"] + candidates
        # (d) 健康への配慮が必要な文脈では、定型文だけをサーバー側で付ける
        if change["care_context"] and FIXED_CARE_PHRASE not in result["observations"]:
            result["observations"].append(FIXED_CARE_PHRASE)

    if request["inputs"]["has_photo"]:
        result["response"]["photo"] = photo_status(result, bool(photo))
    result["request"] = request
    result["response"]["final"] = {
        "kind": result["kind"],
        "todays_word": result["todays_word"],
        "notice": result["notice"],
        "observations": result["observations"],
    }
    return result


def _with_transcript(history: list[dict], record: dict, transcript: str) -> list[dict]:
    """履歴のうち対象日の記録に、Gemini の文字起こしを入れたもの（main.py が保存する値と同じ）。"""
    return [{**r, "transcript": transcript} if r["date"] == record["date"] else r for r in history]


def photo_status(result: dict, readable: bool) -> dict:
    """「仕組みを見る」向け：写真が Gemini に渡ったか、最終の出力に反映されうるか。

    - sent_to_gemini：写真を含めた呼び出しが成功した（試行ログの input に「+photo」）
    - used_in_output：その Gemini の出力をガード通過で採用した（モックに落ちたら False）
    - dropped_error：写真を受け付けない恒常的エラーで、写真を外して続行したときのエラー
    """
    resp = result["response"]
    gem = resp if resp.get("engine") == "gemini" else (resp.get("gemini") or {})
    sent = bool(gem.get("photo_included"))
    attempts = resp.get("gemini_attempts") or gem.get("gemini_attempts") or []
    return {
        "attached": True,
        "readable": readable,
        "sent_to_gemini": sent,
        "used_in_output": sent and result["engine"] == "gemini",
        "tried_with_photo": any("+photo" in a.get("input", "") for a in attempts),
        "dropped_error": gem.get("photo_error"),
    }


def recent_days(record: dict, history: list[dict]) -> list[dict]:
    """対象日の前 RECENT_CONTEXT_DAYS 日（暦日）の記録の要約。古い順・1日1件。

    記録の項目（感謝・気分・声の文字起こし）だけを短く切って渡す。過去の「今日の一言」（AI の出力）は
    入れない：AI が自分の言い回しをなぞらず、本人の言葉を映すため。
    """
    try:
        today = date.fromisoformat(record["date"])
    except (TypeError, ValueError):
        return []
    lo = date.fromordinal(today.toordinal() - RECENT_CONTEXT_DAYS).isoformat()
    by_date: dict[str, dict] = {}
    for r in sorted(history, key=lambda r: (r["date"], r.get("id") or 0)):
        if lo <= r["date"] < record["date"]:
            by_date[r["date"]] = r
    out = []
    for d in sorted(by_date):
        r = by_date[d]
        row = {"date": d, "gratitude": _short(r.get("gratitude", "")), "mood": _short(r.get("mood", ""), 30)}
        if (r.get("transcript") or "").strip():
            row["transcript"] = _short(r["transcript"], 40)
        out.append(row)
    return out


def recent_trends(history: list[dict], change: dict) -> list[dict]:
    """変化検知ルールの発火結果から、傾向の手がかりを日数つきで作る（rules.py の判定をそのまま使う）。

    - word:<語>（同じことばが続く）→ {"kind": "word", "word": 語, "days": n}
    - short_answers（回答が短めの日が続く）→ {"kind": "short_answers", "days": n}
    感謝欄が空の日（empty_gratitude）は「続く変化」ではないので入れない。
    日数の数え方はルールと同じ（ベースライン後の記録の、末尾からの連続数）。
    """
    days = rules._one_per_day(history)
    after = days[rules.BASELINE_DAYS:]
    out: list[dict] = []
    for fired in change.get("fired", []):
        if fired.startswith("word:"):
            w = fired[len("word:"):]
            n = rules._trailing(after, lambda r, w=w: w in rules._text(r))
            out.append({"kind": "word", "word": w, "days": n})
        elif fired == "short_answers":
            baseline = days[:rules.BASELINE_DAYS]
            limit = sum(rules._length(r) for r in baseline) / len(baseline) * rules.SHORT_RATIO
            n = rules._trailing(after, lambda r: rules._length(r) <= limit)
            out.append({"kind": "short_answers", "days": n})
    return out


# --------------------------------------------------------------------------
# 観察候補（#23）：Gemini が提案し、サーバーが候補ごとに採否を決める
# --------------------------------------------------------------------------

_RULE_QUOTED = re.compile(r"『([^』]*)』")
_MD_DATE = re.compile(r"^(\d{1,2})[/月-](\d{1,2})日?$")


def _candidate_date(raw: str, window: dict[str, dict]) -> str | None:
    """候補の date を、プロンプトで見せた記録の日付（YYYY-MM-DD）に合わせる。
    プロンプトの直近の記録は「MM/DD」で見せているので、月日だけの書き方も受け付ける。"""
    raw = (raw or "").strip()
    if raw in window:
        return raw
    m = _MD_DATE.match(raw)
    if m:
        md = f"-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
        hits = [d for d in window if d.endswith(md)]
        if len(hits) == 1:
            return hits[0]
    return None


def evaluate_candidates(raw, *, record: dict, transcript: str, history: list[dict], recent: list[dict],
                        baseline_day: int, change: dict, adoptable: bool = True,
                        not_adoptable_reason: str = "engine_fallback") -> list[dict]:
    """Gemini の観察候補を1件ずつ検査し、{text, quote, date, adopted, reason} のリストを返す。

    - 引用の実在：quote（空白・句読点を除いて比べる）が、date の日の記録の感謝・気分・声のどれかに含まれる。
      date はプロンプトで見せた日（今日と直近の記録）だけを受け付ける
    - NG語・外見・断定調：本人の言葉（今日と直近の記録）を「」で引用した部分を除いて検査する
    - ベースライン中：変化に触れる候補は guard_baseline_change。触れていなくても、観察文は定型文に
      差し替える期間なので採用しない（baseline_period）
    - 重複：ルールC が発火した監視語（同じグループの語を含む）に触れる候補、ルール観察が『』で引いた言葉と
      同じ quote の候補は duplicate_rule。先に採用した候補と同じ文は duplicate_candidate
    - adoptable=False（今日の一言がモックに差し替わった等）のときは、検査結果を残したうえで採用しない
    """
    if isinstance(raw, dict):
        raw = [raw]
    elif not isinstance(raw, list):
        raw = [raw] if raw else []
    in_baseline = baseline_day <= BASELINE_DAYS

    # 根拠にしてよい記録：今日（Gemini の文字起こしを含む）と、プロンプトで見せた直近の記録の日
    window: dict[str, dict] = {}
    shown = {r["date"] for r in recent or []}
    for r in rules._one_per_day(history or []):
        if r["date"] in shown:
            window[r["date"]] = r
    window[record["date"]] = {**record, "transcript": transcript or record.get("transcript", "")}

    def fields(r: dict) -> list[str]:
        return [t for t in (r.get("gratitude"), r.get("mood"), r.get("transcript")) if (t or "").strip()]

    quoted_ok = [t for r in window.values() for t in fields(r)]

    fired_words = [f[len("word:"):] for f in change.get("fired", []) if f.startswith("word:")]
    dup_words = sorted({w for f in fired_words for w in rules.WATCH_WORDS if w[:1] == f[:1]} | set(fired_words))
    rule_quotes = {_STRIP.sub("", q) for o in change.get("observations", []) for q in _RULE_QUOTED.findall(o)}
    rule_quotes.discard("")

    items: list[dict] = []
    adopted_texts: set[str] = set()
    for i, c in enumerate(raw):
        if not isinstance(c, dict):
            items.append({"text": str(c)[:200], "quote": "", "date": "", "adopted": False, "reason": "invalid"})
            continue
        text = str(c.get("text") or "").strip()
        quote = str(c.get("quote") or "").strip()
        raw_date = str(c.get("date") or "").strip()
        item = {"text": text, "quote": quote, "date": raw_date, "adopted": False, "reason": ""}
        items.append(item)
        if not text or not quote:
            item["reason"] = "invalid"
            continue
        if i >= MAX_CANDIDATES:
            item["reason"] = "over_limit"
            continue
        d = _candidate_date(raw_date, window)
        q = _STRIP.sub("", quote)
        if d:
            item["date"] = d
        if not d or len(q) < QUOTE_MIN_LEN or not any(q in _STRIP.sub("", t) for t in fields(window[d])):
            item["reason"] = "quote_not_found"
            continue
        own = strip_user_quotes(text, quoted_ok)
        if find_ng_words(own):
            item["reason"], item["hits"] = "guard_ng_word", find_ng_words(own)
        elif find_appearance_words(own):
            item["reason"], item["hits"] = "guard_appearance", find_appearance_words(own)
        elif find_assertive_state(own):
            item["reason"], item["hits"] = "guard_assertive", find_assertive_state(own)
        elif in_baseline and [w for w in BASELINE_CHANGE_WORDS if w in own]:
            item["reason"] = "guard_baseline_change"
            item["hits"] = [w for w in BASELINE_CHANGE_WORDS if w in own]
        elif in_baseline:
            item["reason"] = "baseline_period"
        elif [w for w in dup_words if w in text or w in quote] or q in rule_quotes:
            item["reason"] = "duplicate_rule"
            item["hits"] = [w for w in dup_words if w in text or w in quote] or [quote]
        elif _STRIP.sub("", text) in adopted_texts:
            item["reason"] = "duplicate_candidate"
        elif not adoptable:
            item["reason"] = not_adoptable_reason
        else:
            item["adopted"] = True
            adopted_texts.add(_STRIP.sub("", text))
    return items


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


def _answer_texts(record: dict, transcript: str = "") -> list[str]:
    return [t for t in (record.get("gratitude", ""), record.get("mood", ""),
                        transcript or record.get("transcript", "")) if (t or "").strip()]


def _apply_guards(g: dict, record: dict, baseline_day: int, previous: dict | None,
                  trends: list[dict] | None = None, recent: list[dict] | None = None) -> dict:
    """(a)(b): Gemini 出力の検査。不合格ならモックに差し替える。"""
    transcript = (g.get("transcript") or "").strip()
    rec_for_mock = {**record, "transcript": transcript or record.get("transcript", "")}
    answers = _answer_texts(record, transcript)
    word = str(g.get("todays_word") or "").strip()
    raw_obs = g.get("observations") or []
    if isinstance(raw_obs, str):  # 文字列で返ってきたとき、1文字ずつに分解しない
        raw_obs = [raw_obs]
    observations = [str(o) for o in raw_obs]

    guard: dict = {"ng_words": [], "appearance": [], "quote": None, "baseline_change": [], "assertive": [],
                   "passed": False}
    reason = ""
    if not answers:
        # 文字も文字起こしも無い（声が聞き取れなかった等）→ 汎用文を出さずに案内
        reason = "no_text"
    else:
        # 本人の言葉をそのまま引用した部分は除いて検査する（本人が書いた「薬」「病院」で弾かない）
        own = [strip_user_quotes(t, answers) for t in (word, *observations)]
        guard["ng_words"] = find_ng_words(*own)
        guard["appearance"] = find_appearance_words(*own)
        if guard["ng_words"]:
            reason = "guard_ng_word"
        elif guard["appearance"]:
            reason = "guard_appearance"  # (g) 写真の有無によらず、外見には触れない
        else:
            guard["quote"] = has_quote(answers, word)
            if not guard["quote"]:
                reason = "guard_no_quote"
            elif baseline_day <= BASELINE_DAYS:
                guard["baseline_change"] = [w for w in BASELINE_CHANGE_WORDS if w in own[0]]
                if guard["baseline_change"]:
                    reason = "guard_baseline_change"
            if not reason:
                # (f) 直近の記録の言葉を「」で引用した部分も除いて検査する（「疲れた」が続く、の引用で弾かない）
                quoted_ok = answers + [t for r in (recent or [])
                                       for t in (r.get("gratitude"), r.get("mood"), r.get("transcript")) if t]
                targets = [word] + (observations if baseline_day > BASELINE_DAYS else [])  # ベースライン中の観察文は差し替えるので見ない
                guard["assertive"] = find_assertive_state(*(strip_user_quotes(t, quoted_ok) for t in targets))
                if guard["assertive"]:
                    reason = "guard_assertive"

    if not reason:
        guard["passed"] = True
        g["response"]["guard"] = guard
        return {
            "todays_word": word, "kind": "word", "notice": "",
            "observations": observations, "engine": "gemini", "fallback_reason": "",
            "transcript": transcript, "response": g["response"],
        }

    result = _analyze_mock(rec_for_mock, baseline_day, previous, trends)
    if reason != "no_text":
        result["engine"] = "mock (guard fallback)"
        result["fallback_reason"] = reason
    else:
        result["engine"] = "gemini"  # Gemini は呼べたが、反映できる言葉が無かった
        result["fallback_reason"] = ""
    result["transcript"] = transcript
    result["response"]["guard"] = guard
    result["response"]["gemini"] = g["response"]
    return result


# --------------------------------------------------------------------------
# Local mock engine (deterministic)
# --------------------------------------------------------------------------

# モックの「今日の一言」：日記の要約ではなく、本人の言葉を映し返す決定的なテンプレート。
# 気分のことばの手ざわり（「〜けど」の同居・沈み気味・明るい・「そこそこ」等の言い回し）を
# 語の一致だけで拾い、引用＋やわらかい着目（＋ときに答えを求めない問いかけ）で返す。
# 判断・診断・助言・原因の推測・過去との比較は書かない（ベースライン中でもそのまま使えるように）。
# 検出用の語は出力には出さない。出力に出るのは本人の言葉（「」の中）とテンプレートの地の文だけ。
_CONTRAST = ("だけど", "だけれど", "けれども", "けれど", "けど", "でも", "のに", "が、")
_LOW_CUES = (
    "疲れ", "つかれ", "だるい", "しんどい", "つらい", "辛い", "落ち込", "沈ん", "イライラ", "いらいら",
    "不安", "眠い", "ねむい", "眠た", "重い", "いまいち", "イマイチ", "元気がない", "元気ない", "さみしい",
    "寂しい", "もやもや", "モヤモヤ", "ため息", "へとへと", "ぐったり", "痛い", "悲しい", "かなしい",
    "しょんぼり", "憂う", "だめ", "ダメ", "ぼんやり", "ゆううつ",
)
_UP_CUES = (
    "元気", "楽しい", "たのしい", "うれしい", "嬉しい", "いい", "良い", "よい", "よかった", "すっきり",
    "スッキリ", "爽やか", "さわやか", "穏やか", "おだやか", "最高", "幸せ", "しあわせ", "ほっと", "軽",
    "上々", "好調", "わくわく", "ワクワク", "のんびり",
)
_HEDGES = ("そこそこ", "まあまあ", "まずまず", "わりと", "それなりに", "ぼちぼち")
_NO_GRATITUDE = {"なし", "特になし", "とくになし", "特にない", "ない", "無し", "特に無し"}
_PART_STRIP = "、。，．,.!！?？ 　"


def _first_cue(text: str, cues: tuple[str, ...]) -> tuple[int, str] | None:
    """text の中で最初に現れる手がかり語の (位置, 語)。"""
    hits = [(text.find(c), c) for c in cues if c in text]
    return min(hits) if hits else None


def _contrast_pair(mood: str) -> tuple[str, str] | None:
    """「元気だけど眠い」→（元気, 眠い）。接続の語が無くても、明るい語と沈み気味の語が両方あれば対にする。"""
    hits = [(mood.find(c), -len(c), c) for c in _CONTRAST if c in mood]
    if hits:
        pos, _, c = min(hits)
        a, b = mood[:pos].strip(_PART_STRIP), mood[pos + len(c):].strip(_PART_STRIP)
        if a and b:
            return a, b
    low = _first_cue(mood, _LOW_CUES)
    up = _first_cue(mood, _UP_CUES)
    if low and up and "元気がない" not in mood and "元気ない" not in mood:
        (_, x), (_, y) = sorted([low, up])
        return x, y
    return None


def _mirror_word(gratitude: str, mood: str, spoken: str, turn: int, trend: str = "") -> str:
    """trend があれば（直近の傾向の一文）、締めの文の代わりにそれを置く（長さを保つため）。"""
    g = "" if gratitude.strip(_PART_STRIP) in _NO_GRATITUDE else gratitude
    m = mood

    def pick(*variants: str) -> str:  # 同じ型の日が続いても、隣り合う日で締めの文が変わるように
        return trend or variants[turn % len(variants)]

    if m:
        pair = _contrast_pair(m)
        if pair:
            a, b = pair
            head = f"「{g}」に目を留めた今日。" if g else ""
            tail = pick(
                "どちらかに決めてしまわずに両方を書けたのは、今の自分をていねいに見ているからかもしれません。",
                f"今日は「{a}」と「{b}」の、どちらが少し大きかったでしょう。",
                "どちらも今日の本当の感じとして、そのまま並べておいていいのかもしれません。",
            )
            return f"{head}「{a}」と「{b}」が、ひとつの言葉の中に同居していますね。{tail}"

        if _first_cue(m, _LOW_CUES):
            if g:
                tail = pick(
                    "重さを感じながらも、ありがたいものを見つけられている。その両方が、今日のあなたです。",
                    "その気持ちとありがたさは、同じ一日の中に並んでいられるのかもしれませんね。",
                    "気持ちが重い日に目に留まるものには、その人らしさが表れるのかもしれません。",
                )
                return f"気分は「{m}」。そんな日にも「{g}」に目を向けられたんですね。{tail}"
            tail = pick(
                "無理に前向きに言い換えずに書けたこと自体が、自分の声をちゃんと聞けているしるしかもしれません。",
                "その言葉の奥には、どんな気持ちがあったでしょう。言葉にならなくても、ここに置いておけます。",
                "こういう日の言葉も、あなたが自分を知るための、大事な手がかりのひとつです。",
            )
            return f"「{m}」と、そのままの言葉で残してくれました。{tail}"

        if _first_cue(m, _UP_CUES):
            hedge = _first_cue(m, _HEDGES)
            if hedge:
                tail = trend or (f"「{hedge[1]}」という言い方に、無理に盛らない、"
                                 "今のあなたのちょうどいい手ざわりが映っているようですね。")
            elif g:
                tail = pick(
                    "明るい気持ちのとき、自分が何に心を動かされるのか。今日の言葉がそっと教えてくれているようですね。",
                    "その明るさと、ありがたいと感じたことは、どこかでつながっていたのかもしれませんね。",
                    "その心地よさは、今日のどんな瞬間にいちばん感じられたでしょう。",
                )
            else:
                tail = pick(
                    "その明るさは、今日のどんな場面から来ていたのでしょう。",
                    "言葉の弾み方に、今日のあなたの調子がそのまま映っているようですね。",
                )
            if g:
                return f"「{g}」と「{m}」。感謝と気分が、同じ明るい方を向いている一日でしたね。{tail}"
            return f"「{m}」と書いてくれた今日。{tail}"

        # 手がかり語のない気分のことば
        if g:
            tail = pick(
                f"「{m}」という言葉を選んだとき、心のどこが動いていたでしょう。",
                "たくさんの出来事の中からそれを選んだところに、"
                "今のあなたが大事にしているものが映っているのかもしれません。",
                f"「{m}」のひと言の中に、言葉にしきれなかった気持ちも混ざっているかもしれませんね。",
            )
            return f"「{g}」に目が向いた今日。気分は「{m}」と言葉にしてくれました。{tail}"
        tail = pick(
            "その言葉を選んだとき、心のどこが動いていたでしょう。",
            "短いひと言にも、そのときの気持ちの手ざわりが残っていますね。",
        )
        return f"今日の気分は「{m}」。{tail}"

    if g:
        return f"今日は「{g}」に目が向いたんですね。" + (trend or (
            "たくさんの出来事の中からそれを選んだところに、今のあなたが大事にしているものが映っているようですね。"))
    return f"声で「{spoken}」と話してくれました。" + (trend or (
        "声に出したその言葉を、もう一度自分で聞いてみると、何か見えてくるかもしれません。"))


# 傾向にふれる一文（モック）。言い切らず、推し量りは「〜かもしれませんね」まで。
_TREND_SOFT = ("少し疲れているのかもしれませんね。", "このところ、少しお疲れなのかもしれませんね。")


def _trend_sentence(trends: list[dict] | None, turn: int) -> str:
    """rules の発火結果から作った傾向（recent_trends）を、やわらかい一文にする。無ければ空文字。"""
    if not trends:
        return ""
    word = next((t for t in trends if t["kind"] == "word"), None)
    short = next((t for t in trends if t["kind"] == "short_answers"), None)
    soft = _TREND_SOFT[turn % len(_TREND_SOFT)]
    if word:
        n = word["days"]
        # 「眠れ」のような語の切れ端は「」に入れず言い換える
        what = "眠りについての言葉" if word["word"] == "眠れ" else f"「{word['word']}」という言葉"
        rest = "が続き、言葉も短めになっていますね。" if short else "が続いていますね。"
        return f"ここ{n}日、{what}{rest}{soft}"
    return (f"ここ{short['days']}日、短めの言葉の日が続いていますね。"
            "短いひと言にも、そのときの気持ちがそのまま映っています。")





def _analyze_mock(record: dict, baseline_day: int, previous: dict | None,
                  trends: list[dict] | None = None) -> dict:
    gratitude = _short(record.get("gratitude", ""))
    mood = _short(record.get("mood", ""), 30)
    spoken = _short(record.get("transcript", ""), 40)
    try:  # 日付の通し番号。連続した日は締めの文が順に入れ替わる（「これまでの一言」が同じ文の繰り返しにならない）
        turn = date.fromisoformat(record["date"]).toordinal()
    except (TypeError, ValueError):
        turn = int(hashlib.md5(f"{record['user_id']}|{record['date']}".encode()).hexdigest(), 16)

    kind, notice, todays_word = "word", "", None
    if gratitude or mood or spoken:
        # 傾向はベースライン後だけ（analyze がベースライン中は空にして渡す。念のためここでも見る）
        trend = _trend_sentence(trends, turn) if baseline_day > BASELINE_DAYS else ""
        todays_word = _mirror_word(gratitude, mood, spoken, turn, trend)
    else:
        # 汎用文は出さない。案内文を、一言とは区別できる形で返す。
        kind, notice = "voice_notice", VOICE_NOTICE

    observations: list[str] = []
    if previous and previous.get("mood") and mood:
        # After the baseline period: a pure observation of wording, no judgment.
        observations.append(
            f"前回の気分のことばは『{_short(previous['mood'], 30)}』、今日は『{mood}』でした。"
            "ことばの変化を、ここにそのまま置いておきますね。"
        )
    if gratitude:
        observations.insert(0, f"感謝のことば：『{gratitude}』")
    if mood:
        observations.insert(1 if gratitude else 0, f"気分・体調のことば：『{mood}』")

    if record.get("photo_path"):
        # 写真の中身は見ていない（解析したふりをしない）。届いたことにだけ触れる
        observations.append(PHOTO_RECEIVED_OBSERVATION)

    return {
        "todays_word": todays_word,
        "kind": kind,
        "notice": notice,
        "observations": observations,
        "engine": "mock",
        "fallback_reason": "",
        "transcript": "",
        "response": {
            "engine": "mock",
            "note": "ローカルモックで生成しました。本人のメモの言葉（感謝・気分・声の文字起こし）を"
                    "そのまま引用しています。",
            "todays_word": todays_word,
            "notice": notice,
            **({"photo_note": "写真は届いていますが、ローカルモックは写真の中身を見ていません。"}
               if record.get("photo_path") else {}),
        },
    }


# --------------------------------------------------------------------------
# Gemini engine
# --------------------------------------------------------------------------

def _gemini_prompt(request: dict, with_audio: bool, with_photo: bool = False) -> str:
    inputs = request["inputs"]
    baseline_note = (
        f"現在はベースライン学習期間（{request['baseline_day']}日目）です。変化への言及はしないでください。"
        "観察の候補（observation_candidates）は空配列にしてください。"
        if request["baseline_period"]
        else "変化に触れる場合も、判断ではなく観察としてだけ書いてください。"
    )
    audio_note = (
        "添付の音声は、本人がこの2つの質問に声で答えたものです。まず音声を日本語でそのまま文字起こしして"
        " transcript に入れてください（聞き取れなければ空文字）。今日の一言では、文字のメモか文字起こしの"
        "言葉を「」でそのまま引用してください。声の様子に触れるときも、本人に語りかける形にします"
        "（例：「声が弾んでいて、楽しそうですね」）。\n"
        if with_audio else ""
    )
    return (
        f"{SYSTEM_RULES}\n{CANDIDATE_RULES}{baseline_note}\n{audio_note}{PHOTO_RULES if with_photo else ''}{_recent_block(request)}"
        f"質問: ①今日、感謝できることは？ ②今日の気分・体調を一言で言うと？\n"
        f"今日のメモ{'（' + request['date'] + '）' if request.get('date') else ''}:\n- 感謝できること: {inputs['gratitude']}\n- 気分・体調: {inputs['mood']}\n"
        "体調についての助言・受診の勧め・病名は書かないでください（必要な定型文はアプリ側で付けます）。\n"
        "出力はJSONのみ: {\"transcript\": \"音声の文字起こし（音声が無ければ空文字）\", "
        "\"todays_word\": \"本人の言葉を「」で具体的に引用し、その裏の気持ちを映し返す2〜3文の一言\", "
        "\"observations\": [\"観察の箇条書き（本人に語りかける です・ます の1文ずつ）\"], "
        "\"observation_candidates\": [{\"text\": \"本人に返す観察の候補（1文）\", "
        "\"quote\": \"根拠にした本人の言葉（記録から逐語で）\", \"date\": \"その言葉があった記録の日付 YYYY-MM-DD\"}]}"
    )


def _recent_block(request: dict) -> str:
    """直近の記録と傾向の手がかり（#9）。ベースライン中・直近の記録が無い日は空文字（従来どおり）。"""
    rows = request.get("recent_days") or []
    trends = request.get("trends") or []
    if request.get("baseline_period") or not (rows or trends):
        return ""
    lines = []
    for r in rows:
        d = r["date"][5:].replace("-", "/")
        line = f"- {d}: 感謝「{r['gratitude'] or '（なし）'}」／気分「{r['mood'] or '（なし）'}」"
        if r.get("transcript"):
            line += f"／声「{r['transcript']}」"
        lines.append(line)
    hints = []
    for t in trends:
        if t["kind"] == "word":
            hints.append(f"- 「{t['word']}」ということばが、ここ{t['days']}日続いている")
        elif t["kind"] == "short_answers":
            hints.append(f"- 回答がはじめの{BASELINE_DAYS}日間より短めの日が、ここ{t['days']}日続いている")
    block = f"{TREND_RULES}\n"
    if lines:
        block += f"直近の記録（過去{RECENT_CONTEXT_DAYS}日、古い順。今日は含まない）:\n" + "\n".join(lines) + "\n"
        block += ("観察の候補の根拠に直近の記録の言葉を使うときは、date にその日付を"
                  f"YYYY-MM-DD で書いてください（年は {rows[0]['date'][:4]} 年）。\n")
    block += ("アプリが言葉の一致だけで数えた傾向の手がかり:\n" + "\n".join(hints) + "\n" if hints
              else "アプリが数えた傾向の手がかり: なし（はっきりした傾向がなければ、過去には触れない）\n")
    return block


class GeminiHTTPError(RuntimeError):
    """Gemini API の HTTP エラー。status で一次的か恒常的かを分ける。"""

    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status


def _is_transient(exc: BaseException) -> bool:
    """再試行して意味がある一次的エラーか（503・429・5xx・タイムアウト・接続断）。"""
    if isinstance(exc, GeminiHTTPError):
        return exc.status in _TRANSIENT_HTTP or exc.status >= 500
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in _TRANSIENT_HTTP or exc.code >= 500
    if isinstance(exc, (TimeoutError, socket.timeout, ConnectionError)):
        return True
    if isinstance(exc, urllib.error.URLError):  # DNS 失敗・接続拒否・読み取りタイムアウト等
        return True
    return False


def _call_gemini(api_key: str, model: str, parts: list[dict], timeout: float = GEMINI_CALL_TIMEOUT_SEC) -> dict:
    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        # M-3: キーは URL ではなくヘッダーで渡す
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise GeminiHTTPError(exc.code, detail) from None


def _call_with_retry(api_key: str, model: str, parts: list[dict], deadline: float,
                     attempts: list[dict], label: str) -> dict:
    """1モデルへの呼び出し。一次的エラーだけ指数バックオフで再試行する（最大 GEMINI_MAX_RETRIES 回）。
    恒常的エラー（400・404 等）と、再試行を使い切った／時間切れの一次的エラーはそのまま投げる。"""
    for n in range(GEMINI_MAX_RETRIES + 1):
        remaining = deadline - _monotonic()
        try:
            if remaining < _MIN_ATTEMPT_SEC:
                raise TimeoutError(f"time budget exhausted before attempt {n + 1}")
            raw = _call_gemini(api_key, model, parts, timeout=min(GEMINI_CALL_TIMEOUT_SEC, remaining))
        except Exception as exc:
            transient = _is_transient(exc)
            entry = {"model": model, "input": label, "attempt": n + 1, "ok": False,
                     "transient": transient, "error": _redact(str(exc), api_key)[:300]}
            attempts.append(entry)
            if not transient or n >= GEMINI_MAX_RETRIES:
                raise
            wait = GEMINI_BACKOFF_SEC[min(n, len(GEMINI_BACKOFF_SEC) - 1)]
            if deadline - _monotonic() - wait < _MIN_ATTEMPT_SEC:
                raise  # 待っても次を呼ぶ時間が残らない
            entry["retry_after_sec"] = wait
            _sleep(wait)
        else:
            attempts.append({"model": model, "input": label, "attempt": n + 1, "ok": True})
            return raw
    raise AssertionError("unreachable")


def _analyze_gemini(api_key: str, request: dict, record: dict, audio: tuple[bytes, str] | None,
                    model: str | None = None, deadline: float | None = None,
                    attempts: list[dict] | None = None,
                    photo: tuple[bytes, str] | None = None) -> dict:
    model = model or request.get("model") or gemini_model()
    deadline = deadline if deadline is not None else _monotonic() + GEMINI_TOTAL_BUDGET_SEC
    attempts = attempts if attempts is not None else []
    use_audio = bool(audio) and audio[1] in AUDIO_MIMES  # type: ignore[index]
    use_photo = bool(photo) and photo[1] in PHOTO_MIMES  # type: ignore[index]
    audio_error = photo_error = None
    while True:
        parts = [{"text": _gemini_prompt(request, use_audio, use_photo)}]
        for media, on in ((audio, use_audio), (photo, use_photo)):
            if on:
                data, mime = media  # type: ignore[misc]
                parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}})
        label = "text" + ("+audio" if use_audio else "") + ("+photo" if use_photo else "")
        try:
            raw = _call_with_retry(api_key, model, parts, deadline, attempts, label)
            break
        except Exception as exc:
            # 一次的エラー（混雑）を使い切ったときは、音声・写真のまま次のモデルへ
            if _is_transient(exc) or not (use_audio or use_photo):
                raise
            # 形式を受け付けない等の恒常的エラー。写真（任意）を先に外し、次に音声を外して同じモデルで再試行する
            if use_photo:
                use_photo, photo_error = False, _redact(str(exc), api_key)
            else:
                use_audio, audio_error = False, _redact(str(exc), api_key)
                if not (record.get("gratitude") or record.get("mood")):
                    raise
    text = raw["candidates"][0]["content"]["parts"][0]["text"]
    parsed = json.loads(text)
    response = {"engine": "gemini", "model": model, "raw": raw, "parsed": parsed}
    if audio_error:
        response["audio_error"] = audio_error
    if photo:
        response["photo_included"] = use_photo
        if photo_error:
            response["photo_error"] = photo_error
    return {
        "todays_word": parsed.get("todays_word", ""),
        "observations": parsed.get("observations", []) or [],
        "observation_candidates": parsed.get("observation_candidates") or [],
        "transcript": (parsed.get("transcript") or "") if use_audio else "",
        "response": response,
    }
