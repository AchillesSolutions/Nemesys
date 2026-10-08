// Speaks the commentary. Uses Azure AI Speech through the server when it is configured (natural
// voices with speaking styles per tone), otherwise the browser's built-in voices.
//
// Live commentary must never fall behind the match, so the player keeps a short queue: a headline
// interrupts whatever is playing, and lines that have waited too long are dropped unspoken.

import { sound } from "./sound";
import type { Language, Line } from "./types";

const STALE_MS = 12000;
const LOCALES: Record<Language, string[]> = { en: ["en-GB", "en"], zh: ["zh-CN", "zh"], es: ["es-MX", "es-US", "es"] };
// Browser voices have no speaking styles; rate and pitch carry a little of the tone instead.
const BROWSER_TONE: Record<string, { rate: number; pitch: number }> = {
  excited: { rate: 1.08, pitch: 1.12 }, hopeful: { rate: 1.03, pitch: 1.05 }, tense: { rate: 1.0, pitch: 0.96 },
  disappointed: { rate: 0.93, pitch: 0.9 }, calm: { rate: 0.96, pitch: 1.0 }, neutral: { rate: 1.0, pitch: 1.0 },
};

interface Item {
  line: Line;
  lang: Language;
  queuedAt: number;
  audio?: Promise<Blob | null>; // fetched ahead of time, so the next line starts without delay
}

class Voice {
  enabled = false;
  private azure = false;
  private queue: Item[] = [];
  private playing: { stop: () => void } | null = null;

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

  stop(): void {
    this.queue = [];
    this.playing?.stop();
  }

  private async next(): Promise<void> {
    const now = performance.now();
    this.queue = this.queue.filter((q) => q.line.headline || now - q.queuedAt < STALE_MS);
    const item = this.queue.shift();
    if (!item) {
      this.playing = null;
      sound.duck(false);
      return;
    }
    sound.duck(true);
    const done = () => {
      if (this.playing === handle) void this.next();
    };
    const handle = this.azure ? await this.playAzure(item, done) : this.playBrowser(item, done);
    this.playing = handle;
  }

  private fetchAudio(line: Line, lang: Language): Promise<Blob | null> {
    return fetch("/api/speech", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: line.text, language: lang, tone: line.tone }),
    }).then((r) => (r.ok ? r.blob() : null)).catch(() => null);
  }

  private async playAzure(item: Item, done: () => void): Promise<{ stop: () => void }> {
    const blob = await item.audio;
    if (!blob) return this.playBrowser(item, done); // Azure hiccup: fall back rather than go silent
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    const finish = () => { URL.revokeObjectURL(url); done(); };
    audio.onended = finish;
    audio.onerror = finish;
    audio.play().catch(finish);
    return { stop: () => { audio.pause(); finish(); } };
  }

  private playBrowser(item: Item, done: () => void): { stop: () => void } {
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
    return { stop: () => { u.onend = null; speechSynthesis.cancel(); done(); } };
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
