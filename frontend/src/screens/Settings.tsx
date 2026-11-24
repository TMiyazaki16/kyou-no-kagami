import { FormEvent, useState } from "react";
import { api, FamilyMember } from "../api";
import { errText } from "../lib/format";
import type { Ctx } from "../navigation";

/* ------------------------------------------------------------------ */
/* 設定・家族（家族コードの表示と連携）                                  */
/* ------------------------------------------------------------------ */
export function Settings({ me, family, refreshMe }: Ctx) {
  const [code, setCode] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const wrap = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setStatus("");
    try {
      await fn();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const link = (e: FormEvent) => {
    e.preventDefault();
    wrap(async () => {
      const res = await api.linkFamily(code);
      setCode("");
      setStatus(
        res.already_linked
          ? `${res.linked.display_name}とは、もう連携しています`
          : `✓ ${res.linked.display_name}と連携しました`,
      );
      await refreshMe();
    });
  };

  const unlink = (f: FamilyMember) => {
    if (!window.confirm(`${f.display_name}との連携を解除しますか？ 解除すると、おたがいに便りを送れなくなります。`)) return;
    wrap(async () => {
      await api.unlinkFamily(f.id);
      setStatus(`${f.display_name}との連携を解除しました`);
      await refreshMe();
    });
  };

  return (
    <section>
      <h2>設定・家族</h2>
      <div className="card">
        <h3>あなたのアカウント</h3>
        <p>表示名：{me.display_name}</p>
        <p className="muted">メール：{me.email}</p>
      </div>
      <div className="card">
        <h3>あなたの家族コード</h3>
        <p className="big code">{me.family_code}</p>
        <p className="muted">
          連携したい家族にだけ伝えてください。このコードを入れた人と連携し、おたがいに便りを送れるようになります。
          連携しても、あなたの記録が自動で届くことはありません。届くのは、あなたが「送る」と決めた便りだけです。
        </p>
      </div>
      <form className="card" onSubmit={link}>
        <h3>家族のコードを入れて連携する</h3>
        <label className="q" htmlFor="family-code">家族コード</label>
        <input
          id="family-code"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          placeholder="例：ABCD-EFGH-JKMN"
          autoComplete="off"
          required
        />
        <button className="primary" type="submit" disabled={busy || !code.trim()}>連携する</button>
      </form>
      <div className="card">
        <h3>連携している家族</h3>
        {family.length === 0 ? (
          <p className="muted">まだいません。</p>
        ) : (
          <ul className="member-list">
            {family.map((f) => (
              <li key={f.id}>
                <span>{f.display_name}</span>
                <button onClick={() => unlink(f)} disabled={busy}>連携を解除</button>
              </li>
            ))}
          </ul>
        )}
      </div>
      {status && <p className="ok">{status}</p>}
      {error && <p className="error">{error}</p>}
    </section>
  );
}
