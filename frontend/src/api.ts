/** ログイン中の自分（パスワード・トークンは含まない） */
export interface Me {
  id: string;
  email: string;
  display_name: string;
  family_code: string;
}

export interface FamilyMember {
  id: string;
  display_name: string;
}

export interface MeResponse {
  user: Me;
  family: FamilyMember[];
}

/** /api/health（未ログインでも読める） */
export interface Health {
  status: string;
  today: string;
  /** false なら新規登録を受け付けていない（REGISTRATION_ENABLED） */
  registration_enabled: boolean;
}

export interface AuthResponse {
  token: string;
  user: Me;
}

export type WordKind = "word" | "voice_notice";

export interface RecordItem {
  id: number;
  user_id: string;
  date: string;
  gratitude: string;
  mood: string;
  transcript: string;
  audio_url?: string | null;
  photo_url?: string | null;
  todays_word?: string | null;
  word_kind?: WordKind | null;
  created_at: string;
}

export interface Analysis {
  id: number;
  record_id: number;
  user_id: string;
  date: string;
  /** kind が "voice_notice" のときは null（案内文は notice に入る） */
  todays_word: string | null;
  kind: WordKind;
  notice: string;
  /** "" | "no_api_key" | "gemini_error" | "quota_user" | "quota_global" | "guard_ng_word" | "guard_appearance" | "guard_no_quote" | "guard_baseline_change" | "guard_assertive" */
  fallback_reason: string;
  observations: string[];
  engine: string;
  baseline_day: number;
  request: Record<string, unknown>;
  response: Record<string, unknown>;
  created_at: string;
}

export type TodayWordResponse =
  | (Analysis & { has_record: true })
  | { todays_word: null; kind: null; date: string; has_record: boolean; record_id: number | null };

/** 便りに入る、その日の AI フィードバックの引用（送信時点のスナップショット。だれも編集できない） */
export interface Feedback {
  date: string;
  todays_word: string;
  observations: string[];
}

/** 便り（送信済み）。中身は feedback（引用）＋ comment（本人のコメント） */
export interface Share {
  id: number;
  from_user: string;
  to_user: string;
  record_id: number | null;
  /** 引用のスナップショット。旧版（#35 より前）の便りは null で、本文は final_text */
  feedback: Feedback | null;
  /** 本人のコメント（空のこともある） */
  comment: string;
  /** 旧版の便りの本文（新しい便りではコメントと同じ） */
  final_text: string;
  status: string;
  created_at: string;
  sent_at: string | null;
  from_name: string;
  to_name: string;
  /** 本人が「写真も一緒に届ける」をオンにして送った便りだけ。それ以外は null */
  photo_url?: string | null;
  /** リアクション（旧スタンプを含む）。人×種類ごとに1つ */
  stamps?: Stamp[];
  /** テキスト返信（古い順） */
  replies?: Reply[];
}

/** 共有画面を開いた家族（連携中の相手）。already_sent はこの日の便りを送信済みか（取り消した便りは数えない） */
export interface ShareRecipient extends FamilyMember {
  already_sent: boolean;
}

/** 共有画面：選んだ日のフィードバックの引用と、届けられる家族 */
export interface SharePreview {
  record_id: number;
  date: string;
  /** null のときは送れない（理由は unavailable） */
  feedback: Feedback | null;
  unavailable: string;
  /** 記録に写真があるときだけ（「写真も一緒に届ける」の表示用） */
  record_photo_url: string | null;
  recipients: ShareRecipient[];
}

/** コメントの上限（サーバーの COMMENT_MAX_CHARS と同じ。文字は code point で数える） */
export const COMMENT_MAX_CHARS = 200;

export interface Reply {
  id: number;
  share_id: number;
  from_user: string;
  from_name?: string;
  body: string;
  created_at: string;
}

/** 返信の上限（サーバーの REPLY_MAX_CHARS と同じ。文字は code point で数える） */
export const REPLY_MAX_CHARS = 200;

export interface Stamp {
  id: number;
  share_id: number;
  from_user: string;
  from_name?: string;
  stamp: string;
  created_at: string;
}

export interface Letters {
  received: Share[];
  sent: Share[];
}

export interface Weekly {
  user_id: string;
  week_start: string;
  week_end: string;
  days_recorded: number;
  total_records: number;
  message: string;
}

export interface DevLatest {
  analysis: Analysis | null;
  gemini_configured: boolean;
  gemini_model: string;
}

export class ApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(`${status} ${detail}`);
  }
}

/* ---- セッション：トークンは localStorage に置き、Authorization ヘッダーで送る ---- */
const TOKEN_KEY = "fudan_token";
export const UNAUTHORIZED_EVENT = "fudan:unauthorized";

export const session = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (token: string) => localStorage.setItem(TOKEN_KEY, token),
  clear: () => localStorage.removeItem(TOKEN_KEY),
};

function authFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = session.get();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(url, { ...init, headers }).then((res) => {
    // ログイン失敗の 401 はその画面で扱う。それ以外の 401 はセッション切れとしてログイン画面へ
    if (res.status === 401 && token && !url.startsWith("/api/auth/login")) {
      session.clear();
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    }
    return res;
  });
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text();
    let detail = text;
    try {
      const parsed = JSON.parse(text);
      if (typeof parsed?.detail === "string") detail = parsed.detail;
    } catch {
      /* keep raw text */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

const get = <T>(url: string) => authFetch(url).then((r) => json<T>(r));

const send = <T>(url: string, body: unknown, method = "POST") =>
  authFetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then((r) => json<T>(r));

const del = <T>(url: string) => authFetch(url, { method: "DELETE" }).then((r) => json<T>(r));

export const api = {
  health: () => get<Health>("/api/health"),
  register: (displayName: string, email: string, password: string) =>
    send<AuthResponse>("/api/auth/register", { display_name: displayName, email, password }),
  login: (email: string, password: string) => send<AuthResponse>("/api/auth/login", { email, password }),
  logout: () => send<{ ok: boolean }>("/api/auth/logout", {}),
  me: () => get<MeResponse>("/api/me"),
  linkFamily: (code: string) =>
    send<{ linked: FamilyMember; already_linked: boolean; family: FamilyMember[] }>("/api/family/link", { code }),
  unlinkFamily: (otherId: string) => del<{ family: FamilyMember[] }>(`/api/family/links/${encodeURIComponent(otherId)}`),

  stamps: () => get<string[]>("/api/stamps"),
  records: () => get<RecordItem[]>("/api/records"),
  createRecord: (form: FormData) =>
    authFetch("/api/records", { method: "POST", body: form }).then((r) => json<RecordItem>(r)),
  analyze: (recordId: number) =>
    authFetch(`/api/records/${recordId}/analyze`, { method: "POST" }).then((r) => json<Analysis>(r)),
  todayWord: () => get<TodayWordResponse>("/api/today-word"),
  weekly: () => get<Weekly>("/api/weekly"),
  devLatest: () => get<DevLatest>("/api/dev/latest-analysis"),
  /** 共有画面：その日（自分の記録）のフィードバックの引用と、宛先ごとの送信済みかどうか */
  sharePreview: (recordId: number) => get<SharePreview>(`/api/shares/preview?record_id=${recordId}`),
  /** 選んだ家族それぞれに1通ずつ届ける（同じ日・同じ相手は1回だけ。送信済みがいれば 409 でだれにも送らない） */
  sendLetters: (recordId: number, toUsers: string[], comment: string, includePhoto = false) =>
    send<{ letters: Share[] }>("/api/shares", {
      record_id: recordId,
      to_users: toUsers,
      comment,
      include_photo: includePhoto,
    }),
  letters: () => get<Letters>("/api/letters"),
  /** 旧版のスタンプ返信（付けるだけ）。画面はリアクション（toggleReaction）を使う */
  addStamp: (shareId: number, stamp: string) => send<Stamp>(`/api/letters/${shareId}/stamps`, { stamp }),
  /** stamps はリアクションの種類（「いいね」＋旧スタンプ） */
  toggleReaction: (shareId: number, reaction: string) =>
    send<{ reaction: string; on: boolean; letter: Share }>(`/api/letters/${shareId}/reactions`, { reaction }),
  addReply: (shareId: number, text: string) =>
    send<{ reply: Reply; letter: Share }>(`/api/letters/${shareId}/replies`, { text }),
  /** 送信の取り消し（送った本人だけ）。家族の画面からも消え、リアクション・返信も一緒に消える */
  retractLetter: (shareId: number) => del<{ ok: boolean; deleted: number }>(`/api/letters/${shareId}`),
  /** 自分の返信の削除 */
  deleteReply: (shareId: number, replyId: number) =>
    del<{ ok: boolean; deleted: number; letter: Share }>(`/api/letters/${shareId}/replies/${replyId}`),
};
