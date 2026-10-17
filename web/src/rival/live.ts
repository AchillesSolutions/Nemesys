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
  onError: (message: string) => void;
}

export class LiveCall {
  readonly sessionId: string;
  private constructor(
    sessionId: string,
    private pc: RTCPeerConnection,
    private channel: RTCDataChannel,
    private mic: MediaStreamTrack,
    private audio: HTMLAudioElement,
  ) {
    this.sessionId = sessionId;
  }

  static async start(handlers: LiveHandlers, onOpen: (call: LiveCall) => void): Promise<LiveCall> {
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
    const resp = await fetch("/api/rival/session", {
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
    const sessionId = resp.headers.get("X-Session-Id")!;
    await pc.setRemoteDescription({ type: "answer", sdp: await resp.text() });

    const call = new LiveCall(sessionId, pc, channel, mic, audio);
    channel.onopen = () => onOpen(call);
    channel.onmessage = ({ data }) => {
      const ev = JSON.parse(data);
      switch (ev.type) {
        case "session.input_transcript.delta": handlers.onTranscript("user", ev.delta); break;
        case "session.output_transcript.delta": handlers.onTranscript("rival", ev.delta); break;
        case "session.usage.updated": handlers.onUsage(ev.usage?.seconds ?? 0); break;
        case "session.closed": handlers.onUsage(ev.usage?.seconds ?? 0); handlers.onClosed(ev.reason); call.end(); break;
        case "error": handlers.onError(ev.error?.message ?? "GPT-Live error"); break;
      }
    };
    pc.onconnectionstatechange = () => {
      if (pc.connectionState === "failed") handlers.onClosed("connection_lost");
    };
    return call;
  }

  setMuted(muted: boolean) {
    this.mic.enabled = !muted;
  }

  close() {
    if (this.channel.readyState === "open") this.channel.send(JSON.stringify({ type: "session.close" }));
    else this.end();
  }

  private end() {
    this.mic.stop();
    this.audio.srcObject = null;
    this.pc.close();
  }
}
