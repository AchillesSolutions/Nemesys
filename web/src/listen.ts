// Always-on listening, TV style. Kick-Off hears the room but only acts when it is being talked to:
//   "Achilles, why was that offside?"   - the name, then the request, in one breath
//   "Achilles." ... "What's the score?" - the name, a chime, then the request
//   follow-ups                          - for a few seconds after each answer, no name is needed
//   holding Space                       - push-to-talk, like the mic button on a remote
//   hands-free (watching alone)         - everything said is for Kick-Off; the mic is always on
// The viewer is always in control: the moment they start talking Kick-Off lowers its voice, and it
// falls silent as soon as it is clear they are talking to it.
//
// Azure AI Speech transcribes with continuous language identification (English, Mandarin, Spanish),
// using a short-lived token from our server, never the key. Without Azure, the browser's own
// recogniser is used where it exists.

import { sound } from "./sound";
import type { Language } from "./types";
import { voice } from "./voice";
import { overlap, splitWake, tokens } from "./wake";

export type ListenState = "off" | "idle" | "awake" | "push" | "thinking" | "conversation";

const LOCALES: Record<Language, string> = { en: "en-US", zh: "zh-CN", es: "es-MX" };
const FROM_LOCALE: Record<string, Language> = { "en-US": "en", "zh-CN": "zh", "es-MX": "es" };
const AWAKE_MS = 7000;          // after "Achilles." alone, how long the next words count as the request
const FOLLOW_UP_MS = 10000;     // after an answer, how long follow-ups need no name
const PUSH_GRACE_MS = 1500;     // words recognised just after Space is released still count
const TOKEN_REFRESH_MS = 9 * 60 * 1000;

type Handlers = {
  command: (text: string, language: Language) => void;
  state: (state: ListenState, caption?: string) => void;
};

const wordCount = (text: string) => tokens(text).length;

class Listener {
  state: ListenState = "off";
  /** In "open" mode every utterance is a request (used while setting up); in "wake" mode only addressed ones. */
  mode: "wake" | "open" = "wake";
  /** Watching alone: the microphone is always on and everything said is a request. */
  handsFree = false;
  private handlers: Handlers = { command: () => {}, state: () => {} };
  private windowUntil = 0;
  private pushReleasedAt = 0;
  private followUpPending = false;
  private stopFn: (() => void) | null = null;
  private hint: Language = "en";

  constructor() {
    // An answer has finished being spoken: open the follow-up window.
    voice.onIdle(() => { if (this.followUpPending) this.openWindow(FOLLOW_UP_MS, "conversation"); });
  }

  on(handlers: Partial<Handlers>): void {
    this.handlers = { ...this.handlers, ...handlers };
  }

  async start(hint: Language): Promise<boolean> {
    this.hint = hint;
    if (this.stopFn) return true;
    this.stopFn = (await this.startAzure()) ?? this.startBrowser();
    this.setState(this.stopFn ? "idle" : "off");
    return Boolean(this.stopFn);
  }

  stop(): void {
    this.stopFn?.();
    this.stopFn = null;
    this.setState("off");
  }

  setHint(lang: Language): void {
    this.hint = lang;
  }

  /** Space bar down / up: everything said while held is a request. */
  push(down: boolean): void {
    if (this.state === "off") return;
    if (down) {
      voice.stop();
      sound.duck(true);
      this.setState("push");
    } else if (this.state === "push") {
      this.pushReleasedAt = performance.now();
      this.setState("idle");
      setTimeout(() => sound.duck(false), PUSH_GRACE_MS);
    }
  }

  /**
   * The request has been answered (or failed). With `followUp`, the next few seconds after the answer
   * need no name; without it (nothing was said back, or setup has just finished), it is the name again.
   */
  done(followUp = true): void {
    if (this.state !== "thinking") return;
    this.setState("idle");
    if (!followUp) return;
    this.followUpPending = true;
    setTimeout(() => { if (this.followUpPending && !voice.speaking) this.openWindow(FOLLOW_UP_MS, "conversation"); }, 400);
  }

  // -- deciding what was meant for us -------------------------------------------

  /** Is whatever the viewer says next meant for Kick-Off, without needing its name? */
  private addressed(): boolean {
    const now = performance.now();
    return this.mode === "open" || this.handsFree || this.state === "push" || now - this.pushReleasedAt < PUSH_GRACE_MS
      || ((this.state === "awake" || this.state === "conversation") && now < this.windowUntil);
  }

  private isEcho(text: string): boolean {
    return (voice.speaking && wordCount(text) >= 2 && overlap(text, voice.current) > 0.5)
      || (voice.lastSpoken !== "" && wordCount(text) >= 3 && overlap(text, voice.lastSpoken) > 0.6);
  }

  /** Words are arriving. React at once: lower Kick-Off's voice, or stop it if we're being talked to. */
  private partial(text: string): void {
    if (this.isEcho(text)) return;
    const woke = splitWake(text).woke;
    if (this.addressed() || woke) {
      voice.stop();
      sound.duck(true);
      if (this.state === "conversation") this.windowUntil = performance.now() + FOLLOW_UP_MS;
      this.handlers.state(this.state, text);
    } else {
      // Someone is talking, but maybe not to us: make room, and decide when they finish.
      voice.duck(true);
      sound.duck(true);
    }
  }

  private final(text: string, language: Language): void {
    text = text.trim();
    if (!text || this.isEcho(text)) {
      voice.duck(false);
      return;
    }
    const { woke, command } = splitWake(text);
    if (this.addressed()) return this.request(woke ? command || text : text, language);
    if (!woke) {
      voice.duck(false); // chatter in the room, not for us
      sound.duck(false);
      return;
    }
    if (command.length > 1) return this.request(command, language);
    // Just the name: chime, and listen for the request.
    voice.stop();
    sound.chime();
    this.openWindow(AWAKE_MS, "awake");
  }

  private openWindow(ms: number, state: "awake" | "conversation"): void {
    this.followUpPending = false;
    this.windowUntil = performance.now() + ms;
    this.setState(state);
    setTimeout(() => {
      if (this.state === state && performance.now() >= this.windowUntil) {
        sound.duck(false);
        this.setState("idle");
      }
    }, ms + 50);
  }

  private request(text: string, language: Language): void {
    voice.duck(false);
    sound.duck(false);
    this.followUpPending = false;
    this.setState("thinking", text);
    this.handlers.command(text, language);
  }

  private setState(state: ListenState, caption?: string): void {
    this.state = state;
    this.handlers.state(state, caption);
  }

  // -- engines -------------------------------------------------------------------

  private async startAzure(): Promise<(() => void) | null> {
    const fetchToken = async () => {
      const res = await fetch("/api/speech/token").catch(() => null);
      return res?.ok ? (await res.json()) as { token: string; region: string } : null;
    };
    const first = await fetchToken();
    if (!first) return null;
    const sdk = await import("microsoft-cognitiveservices-speech-sdk"); // loaded only when needed
    const config = sdk.SpeechConfig.fromEndpoint(new URL(`wss://${first.region}.stt.speech.microsoft.com/speech/universal/v2`));
    config.authorizationToken = first.token;
    config.setProperty(sdk.PropertyId.SpeechServiceConnection_LanguageIdMode, "Continuous");
    const languages = [LOCALES[this.hint], ...Object.values(LOCALES).filter((l) => l !== LOCALES[this.hint])];
    const recognizer = sdk.SpeechRecognizer.FromConfig(
      config, sdk.AutoDetectSourceLanguageConfig.fromLanguages(languages), sdk.AudioConfig.fromDefaultMicrophoneInput());
    const phrases = sdk.PhraseListGrammar.fromRecognizer(recognizer);
    ["Achilles", "Aquiles", "阿喀琉斯", "Kilburn", "Harbour City", "offside", "VAR", "it's just me"]
      .forEach((p) => phrases.addPhrase(p));

    recognizer.recognizing = (_, e) => this.partial(e.result.text);
    recognizer.recognized = (_, e) => {
      if (e.result.reason !== sdk.ResultReason.RecognizedSpeech) return;
      const detected = sdk.AutoDetectSourceLanguageResult.fromResult(e.result).language;
      this.final(e.result.text, FROM_LOCALE[detected] ?? this.hint);
    };
    recognizer.canceled = () => { /* network blips: the SDK reconnects; a hard failure shows as "off" */ };
    try {
      await new Promise<void>((resolve, reject) => recognizer.startContinuousRecognitionAsync(resolve, reject));
    } catch {
      recognizer.close();
      return null;
    }
    const refresh = setInterval(async () => {
      const next = await fetchToken();
      if (next) recognizer.authorizationToken = next.token;
    }, TOKEN_REFRESH_MS);
    return () => {
      clearInterval(refresh);
      recognizer.stopContinuousRecognitionAsync(() => recognizer.close(), () => recognizer.close());
    };
  }

  private startBrowser(): (() => void) | null {
    type Rec = {
      lang: string; continuous: boolean; interimResults: boolean; start(): void; stop(): void;
      onresult: ((e: { resultIndex: number; results: ArrayLike<{ isFinal: boolean } & ArrayLike<{ transcript: string }>> }) => void) | null;
      onend: (() => void) | null;
    };
    const w = window as unknown as Record<string, new () => Rec>;
    const Ctor = w.SpeechRecognition ?? w.webkitSpeechRecognition;
    if (!Ctor) return null;
    const rec = new Ctor();
    let running = true;
    rec.lang = LOCALES[this.hint];
    rec.continuous = true;
    rec.interimResults = true;
    rec.onresult = (e) => {
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) this.final(r[0].transcript, this.hint); else this.partial(r[0].transcript);
      }
    };
    rec.onend = () => { if (running) rec.start(); }; // the browser stops after silences: keep going
    rec.start();
    return () => { running = false; rec.stop(); };
  }
}

export const listener = new Listener();
