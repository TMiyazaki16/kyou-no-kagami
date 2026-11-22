import { useEffect, useRef, useState } from "react";
import { MORE_LABEL, NAV, Screen, TAB_IDS } from "../navigation";

/* ------------------------------------------------------------------ */
/* 下部タブ（案B）と「その他」シート                                     */
/* 文言は NAV のものをそのまま使う。アイコンは装飾（aria-hidden）           */
/* ------------------------------------------------------------------ */

const ICON_PATHS: Partial<Record<Screen, JSX.Element>> = {
  home: <path d="M4 11.5 12 5l8 6.5V19a1 1 0 0 1-1 1h-4.5v-5h-5v5H5a1 1 0 0 1-1-1z" />,
  record: (
    <>
      <path d="M5 19.5h3.2L18.6 9.1a2 2 0 0 0 0-2.8l-.9-.9a2 2 0 0 0-2.8 0L4.5 15.8V19.5z" />
      <path d="M13.5 6.8l3.7 3.7" />
    </>
  ),
  reflect: (
    <>
      <ellipse cx="12" cy="10" rx="6" ry="7" />
      <path d="M12 17v4M9 21h6" />
      <path d="M9.5 7.5c.6-.9 1.5-1.4 2.5-1.5" />
    </>
  ),
  letters: (
    <>
      <rect x="3.5" y="6" width="17" height="12.5" rx="2" />
      <path d="m4.5 7.5 7.5 6 7.5-6" />
    </>
  ),
};

const MoreIcon = () => (
  <>
    <circle cx="6" cy="12" r="1.6" />
    <circle cx="12" cy="12" r="1.6" />
    <circle cx="18" cy="12" r="1.6" />
  </>
);

function Icon({ children, filled }: { children: JSX.Element; filled?: boolean }) {
  return (
    <svg
      className="tab-icon"
      viewBox="0 0 24 24"
      aria-hidden="true"
      fill={filled ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth="1.9"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {children}
    </svg>
  );
}

export function BottomTabs({ screen, go }: { screen: Screen; go: (s: Screen) => void }) {
  const [open, setOpen] = useState(false);
  const moreBtnRef = useRef<HTMLButtonElement>(null);
  const sheetRef = useRef<HTMLDivElement>(null);

  const tabs = NAV.filter((n) => TAB_IDS.includes(n.id));
  const more = NAV.filter((n) => !TAB_IDS.includes(n.id));
  const inMore = !TAB_IDS.includes(screen);

  const close = (restoreFocus = true) => {
    setOpen(false);
    if (restoreFocus) moreBtnRef.current?.focus();
  };

  // シートを開いたら最初の項目へフォーカス。Esc で閉じる。Tab はシートの中を回す
  useEffect(() => {
    if (!open) return;
    const items = () => Array.from(sheetRef.current?.querySelectorAll<HTMLButtonElement>("button") ?? []);
    items()[0]?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        close();
      } else if (e.key === "Tab") {
        const list = items();
        if (list.length === 0) return;
        const first = list[0];
        const last = list[list.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    document.body.classList.add("sheet-open");
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.classList.remove("sheet-open");
    };
  }, [open]);

  const choose = (s: Screen) => {
    close(false);
    go(s);
  };

  return (
    <>
      {open && (
        <div className="sheet-backdrop" onClick={() => close()}>
          <div
            className="sheet"
            id="more-sheet"
            role="dialog"
            aria-modal="true"
            aria-label={MORE_LABEL}
            ref={sheetRef}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="sheet-handle" aria-hidden="true" />
            <ul className="sheet-list">
              {more.map((n) => (
                <li key={n.id}>
                  <button
                    className={screen === n.id ? "active" : ""}
                    aria-current={screen === n.id ? "page" : undefined}
                    onClick={() => choose(n.id)}
                  >
                    {n.label}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      <nav className="tabbar">
        {tabs.map((n) => {
          const active = screen === n.id && !open;
          return (
            <button
              key={n.id}
              className={active ? "tab active" : "tab"}
              aria-current={active ? "page" : undefined}
              onClick={() => {
                setOpen(false);
                go(n.id);
              }}
            >
              <Icon>{ICON_PATHS[n.id]!}</Icon>
              <span className="tab-label">{n.label}</span>
            </button>
          );
        })}
        <button
          ref={moreBtnRef}
          className={inMore || open ? "tab active" : "tab"}
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-controls="more-sheet"
          onClick={() => (open ? close() : setOpen(true))}
        >
          <Icon filled>
            <MoreIcon />
          </Icon>
          <span className="tab-label">{MORE_LABEL}</span>
        </button>
      </nav>
    </>
  );
}
