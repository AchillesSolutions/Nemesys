// Hears one thing the viewer says. Azure AI Speech recognises it (detecting whether it is English,
// Mandarin or Spanish) using a short-lived token from our server, so the key never reaches the
// browser. Without Azure, the browser's own recogniser is used where it exists.

import { sound } from "./sound";
import type { Language } from "./types";
import { voice } from "./voice";

const LOCALES: Record<Language, string> = { en: "en-US", zh: "zh-CN", es: "es-MX" };
const FROM_LOCALE: Record<string, Language> = { "en-US": "en", "zh-CN": "zh", "es-MX": "es" };

export interface Heard {
  text: string;
  language: Language;
}

/** True if this browser can listen at all (Azure needs a microphone; the fallback needs the Web Speech API). */
export function canListen(): boolean {
  return Boolean(navigator.mediaDevices?.getUserMedia) || "webkitSpeechRecognition" in window || "SpeechRecognition" in window;
}

/** Listen until the viewer pauses, then return what they said. Commentary pauses meanwhile. */
export async function listenOnce(hint: Language): Promise<Heard | null> {
  voice.stop();
  sound.duck(true);
  try {
    return (await listenAzure(hint)) ?? (await listenBrowser(hint));
  } finally {
    sound.duck(false);
  }
}

async function listenAzure(hint: Language): Promise<Heard | null> {
  const res = await fetch("/api/speech/token").catch(() => null);
  if (!res?.ok) return null;
  const { token, region } = (await res.json()) as { token: string; region: string };
  const sdk = await import("microsoft-cognitiveservices-speech-sdk"); // loaded only when first needed
  const config = sdk.SpeechConfig.fromAuthorizationToken(token, region);
  // The viewer's current language first, so it wins when the audio is ambiguous.
  const candidates = [LOCALES[hint], ...Object.values(LOCALES).filter((l) => l !== LOCALES[hint])];
  const recognizer = sdk.SpeechRecognizer.FromConfig(
    config, sdk.AutoDetectSourceLanguageConfig.fromLanguages(candidates), sdk.AudioConfig.fromDefaultMicrophoneInput());
  try {
    const result = await new Promise<import("microsoft-cognitiveservices-speech-sdk").SpeechRecognitionResult>(
      (resolve, reject) => recognizer.recognizeOnceAsync(resolve, reject));
    if (result.reason !== sdk.ResultReason.RecognizedSpeech || !result.text.trim()) return { text: "", language: hint };
    const detected = sdk.AutoDetectSourceLanguageResult.fromResult(result).language;
    return { text: result.text.trim(), language: FROM_LOCALE[detected] ?? hint };
  } finally {
    recognizer.close();
  }
}

interface BrowserRecognition {
  lang: string;
  interimResults: boolean;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onerror: (() => void) | null;
  onend: (() => void) | null;
  start(): void;
}

function listenBrowser(hint: Language): Promise<Heard | null> {
  const Ctor = (window as unknown as Record<string, new () => BrowserRecognition>).SpeechRecognition
    ?? (window as unknown as Record<string, new () => BrowserRecognition>).webkitSpeechRecognition;
  if (!Ctor) return Promise.resolve(null);
  return new Promise((resolve) => {
    const rec = new Ctor();
    rec.lang = LOCALES[hint];
    rec.interimResults = false;
    let text = "";
    rec.onresult = (e) => { text = e.results[0][0].transcript; };
    rec.onerror = () => resolve(null);
    rec.onend = () => resolve({ text: text.trim(), language: hint });
    rec.start();
  });
}
