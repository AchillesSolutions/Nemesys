/**
 * The crowd in the rival's ear, synthesised in the browser with the Web Audio API (nothing recorded or sampled,
 * so it's all our own work). Crowds are voices, not noise: a cheer is a few dozen people shouting "aah" at
 * slightly different pitches, a boo is the same on a low "ooo", an "ooh" rises and falls. Each voice is a
 * buzzy oscillator with its own wobble, shaped into a vowel by formant filters; whistles, clapping, a babbling
 * murmur underneath and a stadium's reverb do the rest. The home end is loud and close; the away end is quieter
 * and further off. It sits under the conversation and dips whenever the rival talks.
 */

type Vowel = "ah" | "oo" | "oh" | "aw" | "eh";

// Formants (Hz) and their relative strengths for each vowel, roughly as an adult male voice says them.
const VOWELS: Record<Vowel, [number, number][]> = {
  ah: [[800, 1], [1200, 0.55], [2600, 0.25]],
  oo: [[320, 1], [800, 0.35], [2300, 0.1]],
  oh: [[500, 1], [880, 0.45], [2500, 0.12]],
  aw: [[620, 1], [1000, 0.5], [2550, 0.15]],
  eh: [[530, 1], [1850, 0.5], [2500, 0.25]],
};

interface Shout {
  vowel: Vowel;
  voices: number;
  pitch: [number, number]; // Hz: where the crowd's pitch starts and where it ends up
  attack: number; hold: number; release: number; // seconds
  level: number;
  far?: boolean; // the away end: quieter, duller, further off
}

const rand = (a: number, b: number) => a + Math.random() * (b - a);

export class Crowd {
  private ctx: AudioContext | null = null;
  private out: GainNode | null = null; // everything, before the stadium's reverb
  private ducker: GainNode | null = null;
  private murmur: GainNode | null = null;
  private babble: BiquadFilterNode[] = [];
  private noise: AudioBuffer | null = null;
  private babbling = 0;
  enabled = false;

  /** Must be called from a click: browsers only allow sound after one. */
  async enable(): Promise<void> {
    if (!this.ctx) this.build();
    await this.ctx!.resume();
    this.enabled = true;
  }

  private build(): void {
    const ctx = new AudioContext();
    this.ctx = ctx;
    const master = ctx.createGain();
    master.gain.value = 0.55;
    const squash = ctx.createDynamicsCompressor(); // so a goal is loud without ever being harsh
    squash.threshold.value = -18;
    squash.ratio.value = 4;
    squash.connect(master).connect(ctx.destination);
    this.ducker = ctx.createGain();
    this.ducker.connect(squash);

    // The stadium: a long, diffuse tail made from decaying noise, mixed in under the dry sound.
    const room = ctx.createConvolver();
    room.buffer = this.impulse(2.6);
    const wet = ctx.createGain();
    wet.gain.value = 0.45;
    this.out = ctx.createGain();
    this.out.connect(this.ducker);
    this.out.connect(room).connect(wet).connect(this.ducker);

    // Two seconds of noise, reused for breath, babble and claps.
    const buffer = ctx.createBuffer(1, ctx.sampleRate * 2, ctx.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < data.length; i++) data[i] = Math.random() * 2 - 1;
    this.noise = buffer;

    // The murmur: thousands of conversations, as noise through formants that hop from syllable to syllable.
    this.murmur = ctx.createGain();
    this.murmur.gain.value = 0;
    const rumble = ctx.createBiquadFilter();
    rumble.type = "lowpass";
    rumble.frequency.value = 380;
    const bed = this.loop();
    bed.connect(rumble).connect(this.murmur);
    for (let i = 0; i < 5; i++) {
      const voice = ctx.createBiquadFilter();
      voice.type = "bandpass";
      voice.Q.value = 5;
      voice.frequency.value = rand(300, 1800);
      const g = ctx.createGain();
      g.gain.value = 2.4;
      this.loop().connect(voice).connect(g).connect(this.murmur);
      this.babble.push(voice);
    }
    this.murmur.connect(this.out);
  }

  /** A stadium's reverb tail: stereo noise, decaying. */
  private impulse(seconds: number): AudioBuffer {
    const ctx = this.ctx!;
    const len = Math.floor(ctx.sampleRate * seconds);
    const ir = ctx.createBuffer(2, len, ctx.sampleRate);
    for (let ch = 0; ch < 2; ch++) {
      const d = ir.getChannelData(ch);
      for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, 3.2) * (i < 400 ? i / 400 : 1);
    }
    return ir;
  }

  private loop(): AudioBufferSourceNode {
    const src = this.ctx!.createBufferSource();
    src.buffer = this.noise;
    src.loop = true;
    src.loopStart = rand(0, 1);
    src.start(0, rand(0, 2));
    return src;
  }

  /** The babble's syllables: every so often each strand of murmur moves to a new vowel. */
  private chatter(): void {
    if (!this.ctx || this.babbling) return;
    this.babbling = window.setInterval(() => {
      const t = this.ctx!.currentTime;
      for (const f of this.babble) {
        if (Math.random() < 0.6) f.frequency.setTargetAtTime(rand(280, 2000), t + rand(0, 0.2), 0.05);
      }
    }, 140);
  }

  // -- the crowd's moods -----------------------------------------------------------------------------

  /** The murmur of a full stadium, from kick-off; it dips at half time and dies away at the end. */
  murmurAt(level: number, seconds = 2): void {
    if (!this.enabled || !this.ctx || !this.murmur) return;
    this.chatter();
    this.murmur.gain.setTargetAtTime(level, this.ctx.currentTime, seconds / 3);
  }

  /** Lower the crowd while the rival talks; back up after. */
  duck(on: boolean): void {
    if (!this.ctx || !this.ducker) return;
    this.ducker.gain.setTargetAtTime(on ? 0.35 : 1, this.ctx.currentTime, on ? 0.08 : 0.6);
  }

  /** Many voices on one vowel: the heart of every cheer, boo, groan and "ooh". */
  private shout(s: Shout): void {
    const ctx = this.ctx!, t0 = ctx.currentTime + 0.02;
    const end = s.attack + s.hold + s.release;
    const env = ctx.createGain();
    env.gain.setValueAtTime(0, t0);
    env.gain.linearRampToValueAtTime(s.level, t0 + s.attack);
    env.gain.setValueAtTime(s.level, t0 + s.attack + s.hold);
    env.gain.exponentialRampToValueAtTime(0.0001, t0 + end);
    const formants = ctx.createGain();
    for (const [freq, gain] of VOWELS[s.vowel]) {
      const f = ctx.createBiquadFilter();
      f.type = "bandpass";
      f.frequency.value = freq * rand(0.95, 1.05);
      f.Q.value = freq < 1000 ? 6 : 9;
      const g = ctx.createGain();
      g.gain.value = gain * 2.2;
      formants.connect(f).connect(g).connect(env);
    }
    let tail: AudioNode = env;
    if (s.far) { // the away end, across the pitch
      const dull = ctx.createBiquadFilter();
      dull.type = "lowpass";
      dull.frequency.value = 1300;
      env.connect(dull);
      tail = dull;
    }
    tail.connect(this.out!);

    const per = 1 / Math.sqrt(s.voices);
    for (let i = 0; i < s.voices; i++) {
      const start = t0 + rand(0, s.attack * 0.6); // not everyone starts at once
      const stop = t0 + end * rand(0.75, 1);
      const osc = ctx.createOscillator();
      osc.type = "sawtooth";
      const spread = rand(0.82, 1.22);
      osc.frequency.setValueAtTime(s.pitch[0] * spread, start);
      osc.frequency.exponentialRampToValueAtTime(s.pitch[1] * spread, start + Math.max(0.3, s.attack + s.hold * 0.5));
      const wobble = ctx.createOscillator();
      wobble.frequency.value = rand(4.5, 7);
      const depth = ctx.createGain();
      depth.gain.value = s.pitch[0] * spread * rand(0.015, 0.035);
      wobble.connect(depth).connect(osc.frequency);
      const g = ctx.createGain();
      g.gain.value = per * rand(0.5, 1);
      osc.connect(g).connect(formants);
      osc.start(start); wobble.start(start);
      osc.stop(stop); wobble.stop(stop);
    }
    // Breath: the hiss of a crowd shouting, through the same vowel.
    const breath = this.loop();
    const bg = ctx.createGain();
    bg.gain.value = 0.18;
    breath.connect(bg).connect(formants);
    breath.stop(t0 + end);
  }

  /** Whistles: some long and shrill, some the two-note wolf whistle. */
  private whistles(count: number, within: number, level = 0.06): void {
    const ctx = this.ctx!;
    for (let i = 0; i < count; i++) {
      const t = ctx.currentTime + rand(0.1, within);
      const osc = ctx.createOscillator();
      osc.type = "sine";
      const g = ctx.createGain();
      const len = rand(0.35, 1.1);
      const base = rand(1900, 3000);
      if (Math.random() < 0.4) { // wolf whistle: up, then down
        osc.frequency.setValueAtTime(base * 0.6, t);
        osc.frequency.exponentialRampToValueAtTime(base, t + len * 0.35);
        osc.frequency.setValueAtTime(base * 0.85, t + len * 0.5);
        osc.frequency.exponentialRampToValueAtTime(base * 0.55, t + len);
      } else {
        osc.frequency.setValueAtTime(base, t);
        osc.frequency.linearRampToValueAtTime(base * rand(0.97, 1.03), t + len);
      }
      g.gain.setValueAtTime(0, t);
      g.gain.linearRampToValueAtTime(level * rand(0.5, 1), t + 0.04);
      g.gain.setValueAtTime(level * 0.8, t + len - 0.06);
      g.gain.linearRampToValueAtTime(0, t + len);
      osc.connect(g).connect(this.out!);
      osc.start(t);
      osc.stop(t + len + 0.05);
    }
  }

  /** Applause: lots of short, bright noise bursts, thinning out. */
  private clapping(seconds: number, perSecond: number, level = 0.24): void {
    const ctx = this.ctx!;
    const bright = ctx.createBiquadFilter();
    bright.type = "bandpass";
    bright.frequency.value = 1700;
    bright.Q.value = 0.8;
    bright.connect(this.out!);
    const total = Math.floor(seconds * perSecond);
    for (let i = 0; i < total; i++) {
      const at = Math.pow(Math.random(), 1.6) * seconds; // denser at the start
      const t = ctx.currentTime + 0.05 + at;
      const src = ctx.createBufferSource();
      src.buffer = this.noise;
      const g = ctx.createGain();
      g.gain.setValueAtTime(level * rand(0.4, 1) * (1 - at / seconds * 0.7), t);
      g.gain.exponentialRampToValueAtTime(0.0001, t + rand(0.02, 0.05));
      src.connect(g).connect(bright);
      src.start(t, rand(0, 1.9), 0.06);
    }
  }

  /** The home end erupts. */
  cheer(size = 1): void {
    if (!this.ready()) return;
    this.shout({ vowel: "ah", voices: Math.round(18 + 22 * size), pitch: [190, 300], attack: 0.35, hold: 1.6 + 1.6 * size,
                 release: 2.2, level: 0.32 * size });
    this.shout({ vowel: "eh", voices: 10, pitch: [260, 360], attack: 0.5, hold: 1.2 * size, release: 1.6, level: 0.12 * size });
    this.whistles(Math.round(4 * size), 3);
    this.clapping(4 + 2 * size, 45 * size);
  }

  /** The away end celebrates, across the pitch. */
  farCheer(size = 1): void {
    if (!this.ready()) return;
    this.shout({ vowel: "ah", voices: 16, pitch: [200, 290], attack: 0.4, hold: 1.4 * size, release: 1.8, level: 0.14 * size, far: true });
  }

  /** The home end's disappointment: a falling "aww". */
  groan(size = 1): void {
    if (!this.ready()) return;
    this.shout({ vowel: "aw", voices: 26, pitch: [230, 140], attack: 0.25, hold: 0.5, release: 1.6, level: 0.2 * size });
  }

  /** Boos: long and low. */
  boo(size = 1): void {
    if (!this.ready()) return;
    this.shout({ vowel: "oo", voices: Math.round(20 + 16 * size), pitch: [125, 110], attack: 0.7, hold: 1.4 + 1.4 * size,
                 release: 1.4, level: 0.26 * size });
  }

  /** Jeers: whistling and a mocking "ohh". */
  jeer(size = 1): void {
    if (!this.ready()) return;
    this.whistles(Math.round(8 * size), 2.5, 0.07);
    this.shout({ vowel: "oh", voices: 18, pitch: [210, 170], attack: 0.3, hold: 0.8, release: 1.2, level: 0.12 * size });
  }

  /** "Oooh!": a chance that nearly went in, rising then falling away. */
  ooh(size = 1): void {
    if (!this.ready()) return;
    this.shout({ vowel: "oh", voices: 30, pitch: [170, 260], attack: 0.3, hold: 0.45, release: 1.5, level: 0.24 * size });
    this.clapping(2, 18 * size, 0.14);
  }

  /** Clapping on its own: kick-off, half time, a fine save. */
  applause(size = 1): void {
    if (!this.ready()) return;
    this.clapping(3 + 2 * size, 40 * size);
  }

  private ready(): boolean {
    return this.enabled && this.ctx !== null && this.out !== null;
  }
}

export const crowd = new Crowd();

/**
 * How the crowd reacts to a moment. It's the home crowd, with a small away end. ``team``: the side the moment
 * belongs to (the scorer's, the booked player's, the keeper's, the side given the penalty).
 */
export function react(kind: string, team: string | null, home: string, result?: { home: number; away: number }): void {
  const ours = team === home;
  switch (kind) {
    case "kick_off": crowd.murmurAt(0.05); crowd.applause(0.8); break;
    case "goal": if (ours) crowd.cheer(1); else { crowd.groan(1); crowd.farCheer(1); } break;
    case "goal_disallowed": if (ours) { crowd.boo(1); crowd.jeer(0.6); } else { crowd.cheer(0.8); crowd.jeer(0.8); } break;
    case "penalty": if (ours) crowd.cheer(0.7); else { crowd.boo(1); crowd.jeer(0.7); } break;
    case "red_card": if (ours) crowd.boo(1); else { crowd.cheer(0.6); crowd.jeer(1); } break;
    case "yellow_card": if (ours) crowd.boo(0.5); else crowd.jeer(0.5); break;
    case "save": if (ours) crowd.applause(0.8); else crowd.ooh(1); break; // our keeper's save, or our chance gone
    case "var_check": crowd.murmurAt(0.09, 1); setTimeout(() => crowd.murmurAt(0.05, 3), 5000); break;
    case "half_time": crowd.applause(0.7); crowd.murmurAt(0.025, 4); break;
    case "full_time":
      if (result && result.home > result.away) crowd.cheer(1);
      else if (result && result.home < result.away) { crowd.boo(0.8); crowd.farCheer(1); }
      else crowd.applause(1);
      setTimeout(() => crowd.murmurAt(0, 6), 5000);
      break;
  }
}
