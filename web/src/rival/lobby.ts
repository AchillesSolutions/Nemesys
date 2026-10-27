/**
 * The lobby host: the voice that asks who you support before you're matched with a rival. It is Azure AI
 * Speech, deliberately not the rival's GPT-Live voice, played through a light band-limit and a short comb
 * so it sounds like a system rather than a person. Azure speech recognition listens for the club you name.
 */

let ctx: AudioContext | null = null;
const prepared = new Map<string, Promise<AudioBuffer | null>>();

/** Fetch and decode a line ahead of time (the welcome, while the page loads), so it plays the instant it's needed.
 * Browsers only allow sound after the viewer has clicked, so the welcome can be ready, but not play, on load. */
export function prepare(text: string): Promise<AudioBuffer | null> {
  let line = prepared.get(text);
  if (!line) {
    ctx ??= new AudioContext(); // suspended until the first click; decoding works regardless
    const context = ctx;
    line = fetch("/api/rival/host", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    })
      .then((r) => (r.ok ? r.arrayBuffer() : null))
      .then((audio) => (audio ? context.decodeAudioData(audio) : null))
      .catch(() => null);
    prepared.set(text, line);
  }
  return line;
}

let playing: AudioBufferSourceNode | null = null;

/** Say one line in the host's voice; resolves when it has finished (or straight away if speech is unavailable).
 * A new line cuts off the one before (a tap mid-sentence moves the lobby on), so the host never talks over itself. */
export async function hostSay(text: string): Promise<void> {
  const buffer = await prepare(text);
  if (!buffer || !ctx) return;
  await ctx.resume();
  if (playing) {
    try { playing.stop(); } catch { /* already finished */ }
  }
  const source = ctx.createBufferSource();
  playing = source;
  source.buffer = buffer;

  // Band-limited like a public-address system, with a faint metallic edge from a short feedback comb.
  const highpass = new BiquadFilterNode(ctx, { type: "highpass", frequency: 300 });
  const lowpass = new BiquadFilterNode(ctx, { type: "lowpass", frequency: 5000 });
  const comb = new DelayNode(ctx, { delayTime: 0.0055 });
  const feedback = new GainNode(ctx, { gain: 0.38 });
  const wet = new GainNode(ctx, { gain: 0.5 });
  source.connect(highpass).connect(lowpass);
  lowpass.connect(ctx.destination);
  lowpass.connect(comb).connect(feedback).connect(comb);
  comb.connect(wet).connect(ctx.destination);

  return new Promise((resolve) => {
    source.onended = () => resolve();
    source.start();
  });
}

const STALL_MS = 3000; // they've been talking this long and the recogniser has given no sign of hearing them
const VOICE_RMS = 0.02; // louder than a quiet room: someone's talking

/**
 * Listen for the viewer's answer. Each phrase heard goes to ``onHeard``; ``onPartial`` shows words as they
 * come. ``open`` says whether an answer could be coming now (not while the host is speaking). Returns a function
 * that stops listening, or null if Azure speech isn't available (the crests still work).
 *
 * Now and then a connection to Azure AI Speech takes the audio but never answers, and only gives up after about
 * twelve seconds: the viewer, unheard, says it again and again. So the microphone's level is watched here too,
 * and if they've been talking for a few seconds with no sign the recogniser heard, it starts afresh.
 */
export async function listen(onHeard: (text: string, lang?: string) => void, onPartial: (text: string) => void,
                             open: () => boolean = () => true): Promise<(() => void) | null> {
  const res = await fetch("/api/speech/token").catch(() => null);
  if (!res?.ok) return null;
  const { token, region } = (await res.json()) as { token: string; region: string };
  const sdk = await import("microsoft-cognitiveservices-speech-sdk");
  const config = sdk.SpeechConfig.fromAuthorizationToken(token, region);
  config.setProperty(sdk.PropertyId.Speech_SegmentationSilenceTimeoutMs, "500");
  // English, Mandarin or Spanish, whichever they speak, phrase by phrase: the language they answer in tells
  // the host which one to carry on in.
  config.setProperty(sdk.PropertyId.SpeechServiceConnection_LanguageIdMode, "Continuous");
  const languages = sdk.AutoDetectSourceLanguageConfig.fromLanguages(["en-GB", "zh-CN", "es-MX"]);

  let heardAt = performance.now(); // the recogniser's last sign of life
  const alive = () => { heardAt = performance.now(); };
  const recognise = async () => {
    const recognizer = sdk.SpeechRecognizer.FromConfig(config, languages, sdk.AudioConfig.fromDefaultMicrophoneInput());
    const phrases = sdk.PhraseListGrammar.fromRecognizer(recognizer);
    ["Kilburn", "Kilburn Athletic", "Harbour City", "Harbour", "the Otters", "the Gulls", "English", "Español", "中文",
     "基尔本", "港城"].forEach((p) => phrases.addPhrase(p));
    recognizer.speechStartDetected = alive;
    recognizer.recognizing = (_, e) => { alive(); onPartial(e.result.text); };
    recognizer.recognized = (_, e) => {
      alive();
      if (e.result.reason === sdk.ResultReason.RecognizedSpeech && e.result.text) {
        onHeard(e.result.text, sdk.AutoDetectSourceLanguageResult.fromResult(e.result).language);
      }
    };
    await new Promise<void>((resolve, reject) => recognizer.startContinuousRecognitionAsync(resolve, reject));
    alive();
    return recognizer;
  };
  const close = (r: import("microsoft-cognitiveservices-speech-sdk").SpeechRecognizer) =>
    r.stopContinuousRecognitionAsync(() => r.close(), () => r.close());

  let recognizer: Awaited<ReturnType<typeof recognise>>;
  try {
    recognizer = await recognise();
  } catch {
    return null;
  }

  // The watchdog: the microphone's level, a few times a second.
  let level: (() => number) | null = null;
  let mic: MediaStream | null = null;
  let meter: AudioContext | null = null;
  try {
    mic = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    meter = new AudioContext();
    const analyser = new AnalyserNode(meter, { fftSize: 1024 });
    meter.createMediaStreamSource(mic).connect(analyser);
    const samples = new Float32Array(analyser.fftSize);
    level = () => {
      analyser.getFloatTimeDomainData(samples);
      return Math.sqrt(samples.reduce((sum, x) => sum + x * x, 0) / samples.length);
    };
  } catch { /* no watchdog: the recogniser still works, it just isn't watched */ }
  let talkingSince = 0, restarting = false, stopped = false;
  const watch = window.setInterval(async () => {
    if (!level || restarting || stopped) return;
    const now = performance.now();
    if (talkingSince && heardAt >= talkingSince) talkingSince = 0; // it heard them
    if (!talkingSince && open() && level() > VOICE_RMS) talkingSince = now;
    if (!talkingSince || now - talkingSince < STALL_MS) return;
    talkingSince = 0;
    restarting = true;
    const stale = recognizer;
    try {
      recognizer = await recognise();
      close(stale);
    } catch { /* keep the one we had */ }
    restarting = false;
  }, 200);

  return () => {
    stopped = true;
    window.clearInterval(watch);
    mic?.getTracks().forEach((t) => t.stop());
    void meter?.close();
    close(recognizer);
  };
}
