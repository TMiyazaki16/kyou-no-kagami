import { useState } from "react";
import { api, Share } from "../api";
import { errText } from "../lib/format";
import type { Ctx } from "../navigation";

const PLACEHOLDER_PREFIX = "（AI下書きはまだありません";
/* ------------------------------------------------------------------ */
/* 共有の確認                                                          */
/* ------------------------------------------------------------------ */
export function ShareConfirm({ family, go }: Ctx) {
  const [share, setShare] = useState<Share | null>(null);
  const [toUser, setToUser] = useState<string>(family.length === 1 ? family[0].id : "");
  const [text, setText] = useState("");
  // 「写真も一緒に届ける」。既定はオフ（記録に写真があっても、本人がオンにしたときだけ届く）
  const [includePhoto, setIncludePhoto] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const sent = share !== null && share.status !== "draft";
  const sendable = text.trim() !== "" && !text.trim().startsWith(PLACEHOLDER_PREFIX);

  const wrap = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(`うまくいきませんでした: ${errText(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const makeDraft = () =>
    wrap(async () => {
      const s = await api.createDraft(toUser || undefined);
      setShare(s);
      setText(s.final_text.startsWith(PLACEHOLDER_PREFIX) ? "" : s.final_text);
      setIncludePhoto(false);
      setStatus("");
    });

  const saveEdit = () =>
    wrap(async () => {
      if (!share) return;
      setShare(await api.editShare(share.id, text));
      setStatus("✓ 下書きを保存しました");
    });

  const send = () =>
    wrap(async () => {
      if (!share) return;
      const s = await api.sendShare(share.id, text, includePhoto && !!share.record_photo_url);
      setShare(s);
      setText(s.final_text);
      setStatus(s.photo_url ? `✓ ${s.to_name}へ、写真と一緒に届けました` : `✓ ${s.to_name}へ届けました`);
    });

  return (
    <section>
      <h2>共有の確認</h2>
      <p className="lead">
        共有は任意です。AIが作った下書きを、あなたが読んで、直して、「送る」と決めた内容だけが家族に届きます。
      </p>
      {!share && family.length === 0 && (
        <div className="card">
          <p>まだ家族と連携していません。便りは、連携した家族にだけ送れます。</p>
          <button onClick={() => go("settings")}>設定・家族で連携する</button>
        </div>
      )}
      {!share && family.length > 0 && (
        <div className="card">
          {family.length > 1 && (
            <>
              <label className="q" htmlFor="to-user">送る相手</label>
              <select id="to-user" value={toUser} onChange={(e) => setToUser(e.target.value)}>
                <option value="">選んでください</option>
                {family.map((f) => (
                  <option key={f.id} value={f.id}>{f.display_name}</option>
                ))}
              </select>
            </>
          )}
          <button className="primary" onClick={makeDraft} disabled={busy || !toUser}>AIの下書きを作る</button>
          {error && <p className="error">{error}</p>}
        </div>
      )}
      {share && (
        <div className="card">
          <p className="muted">AIの下書き（そのまま送る必要はありません）:</p>
          <p className="draft">{share.draft_text}</p>
          <label className="q">{sent ? "送った内容" : "送る内容（自由に直せます）"}</label>
          <textarea
            rows={4}
            value={text}
            readOnly={sent}
            placeholder="家族に届けたいことを、ひとこと書いてください"
            onChange={(e) => setText(e.target.value)}
          />
          {!sent && share.record_photo_url && (
            <div className="photo-toggle">
              <label className="toggle-label">
                <input
                  type="checkbox"
                  checked={includePhoto}
                  onChange={(e) => setIncludePhoto(e.target.checked)}
                />
                <span>写真も一緒に届ける</span>
              </label>
              <p className="muted">
                {includePhoto
                  ? "この写真も便りと一緒に届きます。"
                  : "記録の写真は届きません（文面だけが届きます）。"}
              </p>
              {includePhoto && <img className="preview share-photo" src={share.record_photo_url} alt="一緒に届ける写真" />}
            </div>
          )}
          {sent && share.photo_url && (
            <img className="preview share-photo" src={share.photo_url} alt="便りと一緒に届けた写真" />
          )}
          {!sent && (
            <div className="row">
              <button onClick={saveEdit} disabled={busy}>下書きを保存</button>
              <button className="primary" onClick={send} disabled={busy || !sendable}>
                {share.to_name}へ送る
              </button>
            </div>
          )}
          {sent && <p className="muted">送った便りは、あとから書きかえることはできません。</p>}
          {status && <p className="ok">{status}</p>}
          {error && <p className="error">{error}</p>}
        </div>
      )}
    </section>
  );
}
