/* ------------------------------------------------------------------ */
/* 今日の一言の「」部分（本人の言葉）だけを太字にする                      */
/* 文面は変えない（区切って <strong> で包むだけ。連結すると元の文字列と同じ） */
/* ------------------------------------------------------------------ */
export function QuotedText({ text }: { text: string }) {
  const parts = text.split(/(「[^「」]*」)/);
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith("「") && p.endsWith("」") ? (
          <strong key={i} className="quote">{p}</strong>
        ) : (
          p
        ),
      )}
    </>
  );
}
