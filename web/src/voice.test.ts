import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Line } from "./types";

// A fake audio element that records how many lines are playing at once.
class FakeAudio {
  static playing = 0;
  static maxTogether = 0;
  static all: FakeAudio[] = [];
  onended: (() => void) | null = null;
  onerror: (() => void) | null = null;
  volume = 1;
  private live = false;
  constructor(public src: string) { FakeAudio.all.push(this); }
  play() {
    this.live = true;
    FakeAudio.playing++;
    FakeAudio.maxTogether = Math.max(FakeAudio.maxTogether, FakeAudio.playing);
    return Promise.resolve();
  }
  pause() { this.end(); }
  end() { if (this.live) { this.live = false; FakeAudio.playing--; this.onended?.(); } }
}

const line = (text: string, headline = false): Line =>
  ({ text, tone: "neutral", card_title: "", sources: [], proposal_ids: [], concepts: [], headline });

beforeEach(() => {
  FakeAudio.playing = 0;
  FakeAudio.maxTogether = 0;
  FakeAudio.all = [];
  vi.stubGlobal("Audio", FakeAudio);
  Object.assign(URL, { createObjectURL: () => "blob:x", revokeObjectURL: () => {} });
  // Speech audio arrives after a short delay; the health check says Azure is available.
  vi.stubGlobal("fetch", (url: string) => url.endsWith("/api/health")
    ? Promise.resolve({ json: () => Promise.resolve({ speech: true }) })
    : new Promise((r) => setTimeout(() => r({ ok: true, blob: () => Promise.resolve(new Blob()) }), 20)));
  vi.resetModules();
});

const settle = (ms = 60) => new Promise((r) => setTimeout(r, ms));

describe("voice", () => {
  it("never plays two lines at once, even when they arrive while audio is loading", async () => {
    const { voice } = await import("./voice");
    await voice.init();
    voice.enable();
    voice.say([line("Let's kick off!")], "en");
    voice.say([line("We're under way!")], "en"); // arrives while the first is still downloading
    await settle();
    expect(FakeAudio.playing).toBe(1);
    FakeAudio.all[0].end();
    await settle();
    expect(FakeAudio.all).toHaveLength(2);
    expect(FakeAudio.maxTogether).toBe(1);
  });

  it("lets a headline cut in, without overlapping", async () => {
    const { voice } = await import("./voice");
    await voice.init();
    voice.enable();
    voice.say([line("A long explanation of corners.")], "en");
    await settle();
    voice.say([line("Goal! We've scored!", true)], "en");
    await settle();
    expect(voice.current).toBe("Goal! We've scored!");
    expect(FakeAudio.maxTogether).toBe(1);
  });
});
