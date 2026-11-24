import { ReactNode, useEffect, useId, useRef } from "react";

interface Props {
  open: boolean;
  title: string;
  children: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * 取り消し・破棄の前に出す確認（共有の確認・家族の日記）。
 * ネイティブの <dialog> を showModal で開く（フォーカスの閉じ込め・Esc で閉じる・背景の操作不可はブラウザに任せる）。
 * 最初のフォーカスは「やめる」に置き、うっかり Enter で消さないようにする。赤・警告色は使わない。
 */
export function ConfirmDialog({ open, title, children, confirmLabel, cancelLabel = "やめる", busy, onConfirm, onCancel }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      d.showModal();
      cancelRef.current?.focus();
    } else if (!open && d.open) {
      d.close();
    }
  }, [open]);

  return (
    <dialog
      ref={ref}
      className="confirm"
      aria-labelledby={titleId}
      onCancel={(e) => {
        e.preventDefault(); // Esc：閉じるかどうかは呼び出し側の状態で決める
        if (!busy) onCancel();
      }}
    >
      <h3 id={titleId}>{title}</h3>
      <div className="confirm-body">{children}</div>
      <div className="row">
        <button ref={cancelRef} className="big-btn" onClick={onCancel} disabled={busy}>
          {cancelLabel}
        </button>
        <button className="primary big-btn" onClick={onConfirm} disabled={busy}>
          {confirmLabel}
        </button>
      </div>
    </dialog>
  );
}
