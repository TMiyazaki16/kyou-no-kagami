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
  /** "" | "no_api_key" | "gemini_error" | "guard_ng_word" | "guard_no_quote" | "guard_baseline_change" | "guard_assertive" */
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

export interface Share {
  id: number;
  from_user: string;
  to_user: string;
  record_id: number | null;
  draft_text: string;
  final_text: string;
  status: string;
  created_at: string;
  sent_at: string | null;
  from_name: string;
  to_name: string;
  stamps?: Stamp[];
}

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

export const api = {
  register: (displayName: string, email: string, password: string) =>
    send<AuthResponse>("/api/auth/register", { display_name: displayName, email, password }),
  login: (email: string, password: string) => send<AuthResponse>("/api/auth/login", { email, password }),
  logout: () => send<{ ok: boolean }>("/api/auth/logout", {}),
  me: () => get<MeResponse>("/api/me"),
  linkFamily: (code: string) =>
    send<{ linked: FamilyMember; already_linked: boolean; family: FamilyMember[] }>("/api/family/link", { code }),
  unlinkFamily: (otherId: string) =>
    authFetch(`/api/family/links/${encodeURIComponent(otherId)}`, { method: "DELETE" }).then((r) =>
      json<{ family: FamilyMember[] }>(r),
    ),

  stamps: () => get<string[]>("/api/stamps"),
  records: () => get<RecordItem[]>("/api/records"),
  createRecord: (form: FormData) =>
    authFetch("/api/records", { method: "POST", body: form }).then((r) => json<RecordItem>(r)),
  analyze: (recordId: number) =>
    authFetch(`/api/records/${recordId}/analyze`, { method: "POST" }).then((r) => json<Analysis>(r)),
  todayWord: () => get<TodayWordResponse>("/api/today-word"),
  weekly: () => get<Weekly>("/api/weekly"),
  devLatest: () => get<DevLatest>("/api/dev/latest-analysis"),
  createDraft: (toUser?: string, recordId?: number) =>
    send<Share>("/api/shares/draft", { to_user: toUser ?? null, record_id: recordId ?? null }),
  editShare: (shareId: number, finalText: string) =>
    send<Share>(`/api/shares/${shareId}`, { final_text: finalText }, "PATCH"),
  sendShare: (shareId: number, finalText: string) =>
    send<Share>(`/api/shares/${shareId}/send`, { final_text: finalText }),
  letters: () => get<Letters>("/api/letters"),
  addStamp: (shareId: number, stamp: string) => send<Stamp>(`/api/letters/${shareId}/stamps`, { stamp }),
};
