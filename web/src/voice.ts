// Speaks the commentary. Uses Azure AI Speech through the server when it is configured (natural
// voices with speaking styles per tone), otherwise the browser's built-in voices.
//
// Live commentary must never fall behind the match, so the player keeps a short queue: a headline
// interrupts whatever is playing, and lines that have waited too long are dropped unspoken.
// The viewer always wins: the listener can lower this voice the instant they start talking,
// and silence it as soon as it is clear they are talking to Kick-Off.

import { sound } from "./sound";
import type { Language, Line } from "./types";

const STALE_MS = 12000;
const DUCKED_VOLUME = 0.25;
const LOCALES: Record<Language, string[]> = { en: ["en-GB", "en"], zh: ["zh-CN", "zh"], es: ["es-MX", "es-US", "es"] };
// Browser voices have no speaking styles; rate and pitch carry a little of the tone instead.
// The base rate is brisk, like a commentator rather than an audiobook.
const BROWSER_TONE: Record<string, { rate: number; pitch: number }> = {
  excited: { rate: 1.24, pitch: 1.12 }, hopeful: { rate: 1.18, pitch: 1.05 }, tense: { rate: 1.15, pitch: 0.96 },
  disappointed: { rate: 1.06, pitch: 0.9 }, calm: { rate: 1.1, pitch: 1.0 }, neutral: { rate: 1.15, pitch: 1.0 },
};

interface Item {
  line: Line;
  lang: Language;
  queuedAt: number;
  audio?: Promise<Blob | null>; // fetched ahead of time, so the next line starts without delay
}

interface Playing {
  text: string;
  stop: () => void;
  duck: (on: boolean) => void;
}

class Voice {
  enabled = false;
  /** The line most recently spoken, so the listener can ignore Kick-Off hearing itself. */
  lastSpoken = "";
  private azure = false;
  private waiting: (() => void)[] = [];
  private idleListeners = new Set<() => void>();
  private queue: Item[] = [];
  private playing: Playing | null = null;
  private ducked = false;

  /** Ask the server whether Azure AI Speech is available. */
  async init(): Promise<void> {
    try {
      this.azure = Boolean((await (await fetch("/api/health")).json()).speech);
    } catch {
      this.azure = false;
    }
  }

  get engine(): "azure" | "browser" {
    return this.azure ? "azure" : "browser";
  }

  enable(): void {
    this.enabled = true;
    if (!this.azure) speechSynthesis.getVoices(); // starts loading the voice list
  }

  disable(): void {
    this.enabled = false;
    this.stop();
  }

  get speaking(): boolean {
    return this.playing !== null;
  }

  /** What is being said right now ("" when silent). */
  get current(): string {
    return this.playing?.text ?? "";
  }

  /** Queue lines for speaking; a headline cuts in ahead of everything else. */
  say(lines: Line[], lang: Language): void {
    if (!this.enabled) return;
    for (const line of lines) {
      const item: Item = { line, lang, queuedAt: performance.now() };
      if (this.azure) item.audio = this.fetchAudio(line, lang);
      if (line.headline) {
        this.queue = [item, ...this.queue.filter((q) => q.line.headline)];
        this.playing?.stop();
      } else {
        this.queue.push(item);
      }
    }
    if (!this.playing) void this.next();
  }

  /** Speak and resolve once everything queued has been said (or straight away if voice is off). */
  sayAndWait(lines: Line[], lang: Language): Promise<void> {
    if (!this.enabled) return Promise.resolve();
    const done = new Promise<void>((resolve) => this.waiting.push(resolve));
    this.say(lines, lang);
    return done;
  }

  /** Silence everything, now. */
  stop(): void {
    this.queue = [];
    this.playing?.stop();
  }

  /** Lower (or restore) the voice while someone in the room is talking. */
  duck(on: boolean): void {
    this.ducked = on;
    this.playing?.duck(on);
  }

  /** Called every time Kick-Off falls silent. Returns an unsubscribe function. */
  onIdle(listener: () => void): () => void {
    this.idleListeners.add(listener);
    return () => this.idleListeners.delete(listener);
  }

  private async next(): Promise<void> {
    const now = performance.now();
    this.queue = this.queue.filter((q) => q.line.headline || now - q.queuedAt < STALE_MS);
    const item = this.queue.shift();
    if (!item) {
      this.playing = null;
      sound.duck(false);
      this.waiting.splice(0).forEach((resolve) => resolve());
      this.idleListeners.forEach((listener) => listener());
      return;
    }
    sound.duck(true);
    this.lastSpoken = item.line.text;
    let handle: Playing | null = null;
    const done = () => {
      if (this.playing === handle) void this.next();
    };
    handle = this.azure ? await this.playAzure(item, done) : this.playBrowser(item, done);
    this.playing = handle;
    if (this.ducked) handle.duck(true);
  }

  private fetchAudio(line: Line, lang: Language): Promise<Blob | null> {
    return fetch("/api/speech", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: line.text, language: lang, tone: line.tone }),
    }).then((r) => (r.ok ? r.blob() : null)).catch(() => null);
  }

  private async playAzure(item: Item, done: () => void): Promise<Playing> {
    const blob = await item.audio;
    if (!blob) return this.playBrowser(item, done); // Azure hiccup: fall back rather than go silent
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      URL.revokeObjectURL(url);
      done();
    };
    audio.onended = finish;
    audio.onerror = finish;
    audio.play().catch(finish);
    return {
      text: item.line.text,
      stop: () => { audio.pause(); finish(); },
      duck: (on) => { audio.volume = on ? DUCKED_VOLUME : 1; },
    };
  }

  private playBrowser(item: Item, done: () => void): Playing {
    const u = new SpeechSynthesisUtterance(item.line.text);
    const voice = pickBrowserVoice(item.lang);
    if (voice) u.voice = voice;
    u.lang = voice?.lang ?? LOCALES[item.lang][0];
    const tone = BROWSER_TONE[item.line.tone] ?? BROWSER_TONE.neutral;
    u.rate = tone.rate;
    u.pitch = tone.pitch;
    u.onend = done;
    u.onerror = done;
    speechSynthesis.speak(u);
    return {
      text: item.line.text,
      stop: () => { u.onend = null; u.onerror = null; speechSynthesis.cancel(); done(); },
      // Browser speech cannot change volume mid-sentence; pausing is the closest thing.
      duck: (on) => { if (on) speechSynthesis.pause(); else speechSynthesis.resume(); },
    };
  }
}

/** The best installed voice for a language: Edge's "Natural" (neural) voices first. */
function pickBrowserVoice(lang: Language): SpeechSynthesisVoice | undefined {
  const voices = speechSynthesis.getVoices();
  for (const locale of LOCALES[lang]) {
    const matching = voices.filter((v) => v.lang.toLowerCase().startsWith(locale.toLowerCase()));
    const natural = matching.find((v) => /natural|online/i.test(v.name));
    if (natural ?? matching[0]) return natural ?? matching[0];
  }
  return undefined;
}

export const voice = new Voice();
