/**
 * A GPT-Live conversation over WebRTC. The microphone goes up a media track with the browser's echo
 * cancellation on, the rival's voice comes back on another, and JSON events (transcripts, usage)
 * arrive on the "oai-events" data channel. The server creates the session, as GPT-Live has no
 * ephemeral keys for browsers, and steers it from a sideband connection.
 */

export type Speaker = "user" | "rival";

export interface LiveHandlers {
  onTranscript: (who: Speaker, text: string) => void;
  onUsage: (seconds: number) => void;
  onClosed: (reason: string) => void;
  onError: (message: string, code?: string) => void;
}

export interface LiveOptions {
  watch: string; // the viewer's watch; the same call reconnects the voice to a match already under way
  voice?: string; // overrides the chosen rival's own voice and accent (for trying voices out)
  accent?: string;
}

export class LiveCall {
  /** The match this voice belongs to; it outlives the call if the connection drops. */
  readonly watchId: string;
  readonly resumed: boolean;
  private constructor(
    watchId: string,
    resumed: boolean,
    private pc: RTCPeerConnection,
    private channel: RTCDataChannel,
    private mic: MediaStreamTrack,
    private audio: HTMLAudioElement,
  ) {
    this.watchId = watchId;
    this.resumed = resumed;
  }

  static async start(options: LiveOptions, handlers: LiveHandlers, onOpen: (call: LiveCall) => void): Promise<LiveCall> {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    const mic = stream.getAudioTracks()[0];
    const pc = new RTCPeerConnection();
    pc.addTrack(mic, stream);
    const audio = new Audio();
    audio.autoplay = true;
    pc.ontrack = (e) => { audio.srcObject = e.streams[0]; };
    const channel = pc.createDataChannel("oai-events");

    await pc.setLocalDescription(await pc.createOffer());
    const query = new URLSearchParams({ watch: options.watch });
    if (options.voice) query.set("voice", options.voice);
    if (options.accent) query.set("accent", options.accent);
    const resp = await fetch(`/api/rival/session?${query}`, {
      method: "POST",
      headers: { "Content-Type": "application/sdp" },
      body: pc.localDescription!.sdp,
    });
    if (!resp.ok) {
      mic.stop();
      pc.close();
      const detail = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(detail.detail ?? "Couldn't start the session");
    }
    const watchId = resp.headers.get("X-Watch-Id")!;
    const resumed = resp.headers.get("X-Resumed") === "1";
    await pc.setRemoteDescription({ type: "answer", sdp: await resp.text() });

    const call = new LiveCall(watchId, resumed, pc, channel, mic, audio);
    let closed = false;
    const finish = (reason: string) => {
      if (closed) return;
      closed = true;
      handlers.onClosed(reason);
      call.end();
    };
    channel.onopen = () => { onOpen(call); call.watchAudio(); };
    channel.onclose = () => finish("connection_lost");
    channel.onmessage = ({ data }) => {
      const ev = JSON.parse(data);
      switch (ev.type) {
        case "session.input_transcript.delta": handlers.onTranscript("user", ev.delta); break;
        case "session.output_transcript.delta": handlers.onTranscript("rival", ev.delta); break;
        case "session.usage.updated": handlers.onUsage(ev.usage?.seconds ?? 0); break;
        case "session.closed": handlers.onUsage(ev.usage?.seconds ?? 0); finish(ev.reason ?? "closed"); break;
        case "error": handlers.onError(ev.error?.message ?? "GPT-Live error", ev.error?.code); break;
      }
    };
    pc.onconnectionstatechange = () => {
      if (pc.connectionState === "failed") finish("connection_lost");
    };
    return call;
  }

  private statsTimer = 0;

  /**
   * Every few seconds, how well the rival's voice is arriving, sent to the server's log: so when it stutters we
   * can tell the network (packets lost, jitter) from audio arriving late from GPT-Live (concealment with nothing
   * lost) or a busy page (long tasks on the main thread).
   */
  watchAudio(every = 5000) {
    let last: Record<string, number> = {};
    let longTasks = 0, longest = 0;
    try {
      new PerformanceObserver((list) => list.getEntries().forEach((e) => {
        longTasks += 1;
        longest = Math.max(longest, e.duration);
      })).observe({ type: "longtask", buffered: false });
    } catch { /* not supported here */ }
    this.statsTimer = window.setInterval(async () => {
      const report = await this.pc.getStats().catch(() => null);
      if (!report) return;
      const now: Record<string, number> = {};
      let rtt = 0;
      report.forEach((s) => {
        if (s.type === "inbound-rtp" && s.kind === "audio") {
          for (const k of ["packetsReceived", "packetsLost", "concealedSamples", "silentConcealedSamples",
            "concealmentEvents", "totalSamplesReceived", "insertedSamplesForDeceleration",
            "removedSamplesForAcceleration", "jitterBufferDelay", "jitterBufferEmittedCount"]) now[k] = s[k] ?? 0;
          now.jitter_ms = (s.jitter ?? 0) * 1000;
        }
        if (s.type === "candidate-pair" && s.nominated && s.currentRoundTripTime) rtt = s.currentRoundTripTime * 1000;
      });
      const d = (k: string) => (now[k] ?? 0) - (last[k] ?? 0);
      const samples = d("totalSamplesReceived");
      const sample = {
        packets: d("packetsReceived"), lost: d("packetsLost"),
        concealed_pct: samples > 0 ? Math.round((1000 * (d("concealedSamples") - d("silentConcealedSamples"))) / samples) / 10 : 0,
        concealment_events: d("concealmentEvents"),
        stretched_pct: samples > 0 ? Math.round((1000 * (d("insertedSamplesForDeceleration") + d("removedSamplesForAcceleration"))) / samples) / 10 : 0,
        buffer_ms: d("jitterBufferEmittedCount") > 0 ? Math.round((1000 * d("jitterBufferDelay")) / d("jitterBufferEmittedCount")) : 0,
        jitter_ms: Math.round(now.jitter_ms ?? 0), rtt_ms: Math.round(rtt), long_tasks: longTasks, longest_task_ms: Math.round(longest),
      };
      last = now;
      longTasks = 0;
      longest = 0;
      if (sample.packets === 0) return; // paused, or nothing playing
      void fetch(`/api/rival/${this.watchId}/audio`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(sample),
      }).catch(() => {});
    }, every);
  }

  /** Paused: the rival can't hear you and you don't hear the rival. */
  setPaused(paused: boolean) {
    this.mic.enabled = !paused;
    this.audio.muted = paused;
  }

  close() {
    if (this.channel.readyState === "open") this.channel.send(JSON.stringify({ type: "session.close" }));
    else this.end();
  }

  end() {
    window.clearInterval(this.statsTimer);
    this.mic.stop();
    this.audio.srcObject = null;
    this.pc.close();
  }
}
