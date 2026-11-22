/** M-5: 録音形式。サーバーの許可リスト（audio/webm, audio/mp4, audio/ogg）に合わせる */
export const AUDIO_EXT: Record<string, string> = { "audio/ogg": "ogg", "audio/webm": "webm", "audio/mp4": "m4a" };
export const pickRecorderMime = (): string | undefined => {
  if (typeof MediaRecorder === "undefined" || !MediaRecorder.isTypeSupported) return undefined;
  return ["audio/ogg;codecs=opus", "audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find((t) =>
    MediaRecorder.isTypeSupported(t),
  );
};
export const baseMime = (t: string) => t.split(";")[0].trim().toLowerCase();
