// Match atmosphere, synthesised in the browser with the Web Audio API: a referee's pea whistle and
// a crowd built from filtered noise. Nothing is recorded or sampled, so there are no rights issues.

import type { MatchEvent } from "./types";

type WhistlePattern = number[]; // blast lengths in seconds

const WHISTLES: Record<string, WhistlePattern> = {
  kick_off: [0.85],
  foul: [0.28],
  offside: [0.28],
  penalty: [0.9],
  half_time: [0.45, 0.9],
  full_time: [0.45, 0.45, 1.1],
  goal_disallowed: [0.3, 0.3],
};

const CROWD_BASE = 0.05;

export class MatchSound {
  private ctx: AudioContext | null = null;
  private master: GainNode | null = null;
  private crowd: GainNode | null = null;
  private ducker: GainNode | null = null;
  private crowdTone: BiquadFilterNode | null = null;
  private running = false;
  enabled = false;

  /** Must be called from a click (browsers only allow audio after a user gesture). */
  async enable(): Promise<void> {
    if (!this.ctx) this.build();
    await this.ctx!.resume();
    this.enabled = true;
    this.master!.gain.setTargetAtTime(1, this.ctx!.currentTime, 0.1);
  }

  disable(): void {
    this.enabled = false;
    if (this.ctx && this.master) this.master.gain.setTargetAtTime(0, this.ctx.currentTime, 0.1);
  }

  /** React to a match event. ``speed`` mutes effects when playback is fast-forwarding. */
  onEvent(ev: MatchEvent, speed: number): void {
    if (!this.enabled || !this.ctx) return;
    if (ev.type !== "full_time" && ev.type !== "half_time") this.startCrowd(); // also if sound was switched on mid-match
    if (speed > 4) return;
    const pattern = WHISTLES[ev.type];
    if (pattern) this.whistle(pattern);
    switch (ev.type) {
      case "goal": this.swell(0.42, 5, 1800); break;
      case "penalty": case "red_card": this.swell(0.22, 3, 1200); break;
      case "shot": if (ev.outcome !== "goal") this.swell(0.16, 1.6, 1100); break; // the "ooh"
      case "corner": case "var_decision": this.swell(0.1, 2, 900); break;
      case "kick_off": this.setCrowd(CROWD_BASE, 2); break; // back up after half-time
      case "half_time": this.setCrowd(CROWD_BASE * 0.5); break;
      case "full_time": this.swell(0.3, 4, 1400); setTimeout(() => this.setCrowd(0, 3), 4500); break;
    }
  }

  private build(): void {
    const ctx = new AudioContext();
    this.ctx = ctx;
    this.master = ctx.createGain();
    this.master.gain.value = 0;
    this.master.connect(ctx.destination);

    // Crowd: two seconds of pink-ish noise, looped, shaped into a distant murmur.
    const buffer = ctx.createBuffer(1, ctx.sampleRate * 2, ctx.sampleRate);
    const data = buffer.getChannelData(0);
    let b0 = 0, b1 = 0, b2 = 0;
    for (let i = 0; i < data.length; i++) {
      const white = Math.random() * 2 - 1;
      b0 = 0.99765 * b0 + white * 0.099;
      b1 = 0.963 * b1 + white * 0.2965;
      b2 = 0.57 * b2 + white * 1.0526;
      data[i] = (b0 + b1 + b2 + white * 0.1848) * 0.2;
    }
    const noise = ctx.createBufferSource();
    noise.buffer = buffer;
    noise.loop = true;
    this.crowdTone = ctx.createBiquadFilter();
    this.crowdTone.type = "bandpass";
    this.crowdTone.frequency.value = 700;
    this.crowdTone.Q.value = 0.6;
    this.crowd = ctx.createGain();
    this.crowd.gain.value = 0;
    // A slow wobble so the murmur breathes instead of hissing evenly.
    const lfo = ctx.createOscillator();
    const lfoDepth = ctx.createGain();
    lfo.frequency.value = 0.13;
    lfoDepth.gain.value = 0.012;
    lfo.connect(lfoDepth).connect(this.crowd.gain);
    this.ducker = ctx.createGain();
    noise.connect(this.crowdTone).connect(this.crowd).connect(this.ducker).connect(this.master);
    noise.start();
    lfo.start();
  }

  /** Lower the crowd while commentary is speaking, and bring it back afterwards. */
  duck(on: boolean): void {
    if (!this.ctx || !this.ducker) return;
    this.ducker.gain.setTargetAtTime(on ? 0.35 : 1, this.ctx.currentTime, on ? 0.08 : 0.4);
  }

  private startCrowd(): void {
    if (this.running) return;
    this.running = true;
    this.setCrowd(CROWD_BASE, 2);
  }

  private setCrowd(level: number, seconds = 1): void {
    if (!this.ctx || !this.crowd) return;
    this.crowd.gain.setTargetAtTime(level, this.ctx.currentTime, seconds / 3);
  }

  /** The crowd rises (and brightens) for a moment, then settles back. */
  private swell(level: number, seconds: number, brightness: number): void {
    if (!this.ctx || !this.crowd || !this.crowdTone) return;
    const t = this.ctx.currentTime;
    this.crowd.gain.cancelScheduledValues(t);
    this.crowd.gain.setTargetAtTime(level, t, 0.15);
    this.crowd.gain.setTargetAtTime(this.running ? CROWD_BASE : 0, t + seconds * 0.6, seconds / 3);
    this.crowdTone.frequency.cancelScheduledValues(t);
    this.crowdTone.frequency.setTargetAtTime(brightness, t, 0.1);
    this.crowdTone.frequency.setTargetAtTime(700, t + seconds * 0.6, seconds / 3);
  }

  /** A pea whistle: a high tone with the fast warble the rattling pea gives it, plus breath noise. */
  private whistle(pattern: WhistlePattern): void {
    const ctx = this.ctx!, out = this.master!;
    let t = ctx.currentTime + 0.05;
    for (const length of pattern) {
      const tone = ctx.createOscillator();
      tone.type = "triangle";
      tone.frequency.value = 2950;
      const warble = ctx.createOscillator();
      const warbleDepth = ctx.createGain();
      warble.frequency.value = 26;
      warbleDepth.gain.value = 170;
      warble.connect(warbleDepth).connect(tone.frequency);

      const env = ctx.createGain();
      env.gain.setValueAtTime(0, t);
      env.gain.linearRampToValueAtTime(0.16, t + 0.02);
      env.gain.setValueAtTime(0.16, t + length - 0.05);
      env.gain.linearRampToValueAtTime(0, t + length);
      tone.connect(env).connect(out);

      tone.start(t);
      warble.start(t);
      tone.stop(t + length + 0.05);
      warble.stop(t + length + 0.05);
      t += length + 0.12;
    }
  }
}

export const sound = new MatchSound();
