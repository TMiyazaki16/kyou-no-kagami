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
  (a)(e) は、本人の言葉をそのまま「」で引用した部分を除いて検査する。
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
    "あなたは本人の自己認知を助ける『鏡』です。観察だけを返し、判断・診断はしません。"
    "病名や医療的な助言（受診・薬・病院などへの言及を含む）は一切出しません。"
    "本人が書いた（話した）言葉を「」で具体的にそのまま引用して、"
    "その人自身に気づきを返す短い『今日の一言』を作ってください。"
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

# アップロード時に許可している音声 MIME（main.py と同じ集合）
AUDIO_MIMES = {"audio/webm", "audio/mp4", "audio/ogg"}


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
) -> dict:
    """Returns {todays_word, kind, notice, observations, engine, fallback_reason,
    transcript, request, response}."""
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
            "has_photo": bool(record.get("photo_path")),
        },
        "rules": SYSTEM_RULES,
    }
    change = rules.evaluate(history or [record])
    request["change_rules"] = {"fired": change["fired"], "care_context": change["care_context"]}

    result: dict | None = None
    if api_key:
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
                g = _analyze_gemini(api_key, request, record, audio, model, deadline, attempts)
                break
            except Exception as exc:  # 次のモデルへ。全滅したらモック
                last_error = exc
        if g is None:  # fall back to mock, but say so in the dev view
            result = _analyze_mock(record, baseline_day, previous)
            result["engine"] = "mock (gemini-error fallback)"
            result["fallback_reason"] = "gemini_error"
            result["response"]["gemini_error"] = _redact(str(last_error), api_key)
            result["response"]["gemini_attempts"] = attempts
        else:
            # 「仕組みを見る」が表示するモデル名は、実際に応答したモデルにする
            request["model"] = g["response"]["model"]
            g["response"]["gemini_attempts"] = attempts
            # ガードは、どのモデルが応答しても同じものを通す
            result = _apply_guards(g, record, baseline_day, previous)
    else:
        result = _analyze_mock(record, baseline_day, previous)
        result["fallback_reason"] = "no_api_key"

    # (c) ベースライン期間中は observations をサーバー定型文に差し替え（どのエンジンでも）
    if in_baseline:
        result["observations"] = baseline_observations(
            {**record, "transcript": result.get("transcript") or record.get("transcript", "")},
            baseline_day,
        )
    else:
        result["observations"] = list(result["observations"]) + change["observations"]
        # (d) 健康への配慮が必要な文脈では、定型文だけをサーバー側で付ける
        if change["care_context"] and FIXED_CARE_PHRASE not in result["observations"]:
            result["observations"].append(FIXED_CARE_PHRASE)

    result["request"] = request
    result["response"]["final"] = {
        "kind": result["kind"],
        "todays_word": result["todays_word"],
        "notice": result["notice"],
        "observations": result["observations"],
    }
    return result


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


def _answer_texts(record: dict, transcript: str = "") -> list[str]:
    return [t for t in (record.get("gratitude", ""), record.get("mood", ""),
                        transcript or record.get("transcript", "")) if (t or "").strip()]


def _apply_guards(g: dict, record: dict, baseline_day: int, previous: dict | None) -> dict:
    """(a)(b): Gemini 出力の検査。不合格ならモックに差し替える。"""
    transcript = (g.get("transcript") or "").strip()
    rec_for_mock = {**record, "transcript": transcript or record.get("transcript", "")}
    answers = _answer_texts(record, transcript)
    word = str(g.get("todays_word") or "").strip()
    raw_obs = g.get("observations") or []
    if isinstance(raw_obs, str):  # 文字列で返ってきたとき、1文字ずつに分解しない
        raw_obs = [raw_obs]
    observations = [str(o) for o in raw_obs]

    guard: dict = {"ng_words": [], "quote": None, "baseline_change": [], "passed": False}
    reason = ""
    if not answers:
        # 文字も文字起こしも無い（声が聞き取れなかった等）→ 汎用文を出さずに案内
        reason = "no_text"
    else:
        # 本人の言葉をそのまま引用した部分は除いて検査する（本人が書いた「薬」「病院」で弾かない）
        own = [strip_user_quotes(t, answers) for t in (word, *observations)]
        guard["ng_words"] = find_ng_words(*own)
        if guard["ng_words"]:
            reason = "guard_ng_word"
        else:
            guard["quote"] = has_quote(answers, word)
            if not guard["quote"]:
                reason = "guard_no_quote"
            elif baseline_day <= BASELINE_DAYS:
                guard["baseline_change"] = [w for w in BASELINE_CHANGE_WORDS if w in own[0]]
                if guard["baseline_change"]:
                    reason = "guard_baseline_change"

    if not reason:
        guard["passed"] = True
        g["response"]["guard"] = guard
        return {
            "todays_word": word, "kind": "word", "notice": "",
            "observations": observations, "engine": "gemini", "fallback_reason": "",
            "transcript": transcript, "response": g["response"],
        }

    result = _analyze_mock(rec_for_mock, baseline_day, previous)
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

def _analyze_mock(record: dict, baseline_day: int, previous: dict | None) -> dict:
    gratitude = _short(record.get("gratitude", ""))
    mood = _short(record.get("mood", ""), 30)
    spoken = _short(record.get("transcript", ""), 40)
    seed = int(hashlib.md5(f"{record['user_id']}|{record['date']}".encode()).hexdigest(), 16)

    kind, notice, todays_word = "word", "", None
    if gratitude and mood:
        templates = [
            "「{g}」にありがとうを感じられた今日。気分は「{m}」とのことでした。"
            "その気持ちを自分の言葉で残せたこと自体が、今日のあなたの記録です。",
            "今日の感謝は「{g}」。そして気分は「{m}」。"
            "うれしいことも、そうでない気持ちも、どちらも今日のあなたの一部ですね。",
            "「{g}」があった一日。気分は「{m}」と教えてくれました。"
            "言葉にしてみると、昨日より少しだけ自分のことが見えてくるかもしれません。",
        ]
        todays_word = templates[seed % len(templates)].format(g=gratitude, m=mood)
    elif gratitude:
        todays_word = f"今日の感謝は「{gratitude}」。その一言を残せたことが、今日のあなたの記録です。"
    elif mood:
        todays_word = f"今日の気分は「{mood}」と教えてくれました。そのままの言葉を、ここに置いておきます。"
    elif spoken:
        todays_word = f"声で「{spoken}」と話してくれました。その言葉を、今日の記録として置いておきます。"
    else:
        # 汎用文は出さない。案内文を、一言とは区別できる形で返す。
        kind, notice = "voice_notice", VOICE_NOTICE

    observations: list[str] = []
    if previous and previous.get("mood") and mood:
        # After the baseline period: a pure observation of wording, no judgment.
        observations.append(
            f"前回の気分のことばは『{_short(previous['mood'], 30)}』、今日は『{mood}』でした。"
            "ことばの変化を、そのまま置いておきます。"
        )
    if gratitude:
        observations.insert(0, f"感謝のことば：『{gratitude}』")
    if mood:
        observations.insert(1 if gratitude else 0, f"気分・体調のことば：『{mood}』")

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
        },
    }


# --------------------------------------------------------------------------
# Gemini engine
# --------------------------------------------------------------------------

def _gemini_prompt(request: dict, with_audio: bool) -> str:
    inputs = request["inputs"]
    baseline_note = (
        f"現在はベースライン学習期間（{request['baseline_day']}日目）です。変化への言及はしないでください。"
        if request["baseline_period"]
        else "変化に触れる場合も、判断ではなく観察としてだけ書いてください。"
    )
    audio_note = (
        "添付の音声は、本人がこの2つの質問に声で答えたものです。まず音声を日本語でそのまま文字起こしして"
        " transcript に入れてください（聞き取れなければ空文字）。今日の一言では、文字のメモか文字起こしの"
        "言葉を「」でそのまま引用してください。\n"
        if with_audio else ""
    )
    return (
        f"{SYSTEM_RULES}\n{baseline_note}\n{audio_note}"
        f"質問: ①今日、感謝できることは？ ②今日の気分・体調を一言で言うと？\n"
        f"今日のメモ:\n- 感謝できること: {inputs['gratitude']}\n- 気分・体調: {inputs['mood']}\n"
        "体調についての助言・受診の勧め・病名は書かないでください（必要な定型文はアプリ側で付けます）。\n"
        "出力はJSONのみ: {\"transcript\": \"音声の文字起こし（音声が無ければ空文字）\", "
        "\"todays_word\": \"本人の言葉を「」で具体的に引用した短い一言\", "
        "\"observations\": [\"観察の箇条書き\"]}"
    )


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
                    attempts: list[dict] | None = None) -> dict:
    model = model or request.get("model") or gemini_model()
    deadline = deadline if deadline is not None else _monotonic() + GEMINI_TOTAL_BUDGET_SEC
    attempts = attempts if attempts is not None else []
    audio_ok = bool(audio) and audio[1] in AUDIO_MIMES  # type: ignore[index]
    audio_error = None
    raw = None
    if audio_ok:
        data, mime = audio  # type: ignore[misc]
        parts = [
            {"text": _gemini_prompt(request, True)},
            {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}},
        ]
        try:
            raw = _call_with_retry(api_key, model, parts, deadline, attempts, "text+audio")
        except Exception as exc:
            # 一次的エラー（混雑）を使い切ったときは、音声のまま次のモデルへ
            if _is_transient(exc):
                raise
            # 音声形式を受け付けない等。文字があれば文字だけで再試行する
            audio_error = _redact(str(exc), api_key)
            if not (record.get("gratitude") or record.get("mood")):
                raise
    if raw is None:
        raw = _call_with_retry(api_key, model, [{"text": _gemini_prompt(request, False)}],
                               deadline, attempts, "text")
    text = raw["candidates"][0]["content"]["parts"][0]["text"]
    parsed = json.loads(text)
    response = {"engine": "gemini", "model": model, "raw": raw, "parsed": parsed}
    if audio_error:
        response["audio_error"] = audio_error
    return {
        "todays_word": parsed.get("todays_word", ""),
        "observations": parsed.get("observations", []) or [],
        "transcript": (parsed.get("transcript") or "") if audio_ok and not audio_error else "",
        "response": response,
    }
