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

/** Say one line in the host's voice; resolves when it has finished (or straight away if speech is unavailable). */
export async function hostSay(text: string): Promise<void> {
  const buffer = await prepare(text);
  if (!buffer || !ctx) return;
  await ctx.resume();
  const source = ctx.createBufferSource();
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

/**
 * Listen for the viewer's answer. Each phrase heard goes to ``onHeard``; ``onPartial`` shows words as they
 * come. Returns a function that stops listening, or null if Azure speech isn't available (the crests still work).
 */
export async function listen(onHeard: (text: string) => void, onPartial: (text: string) => void): Promise<(() => void) | null> {
  const res = await fetch("/api/speech/token").catch(() => null);
  if (!res?.ok) return null;
  const { token, region } = (await res.json()) as { token: string; region: string };
  const sdk = await import("microsoft-cognitiveservices-speech-sdk");
  const config = sdk.SpeechConfig.fromAuthorizationToken(token, region);
  config.speechRecognitionLanguage = "en-GB";
  config.setProperty(sdk.PropertyId.Speech_SegmentationSilenceTimeoutMs, "500");
  const recognizer = new sdk.SpeechRecognizer(config, sdk.AudioConfig.fromDefaultMicrophoneInput());
  const phrases = sdk.PhraseListGrammar.fromRecognizer(recognizer);
  ["Kilburn", "Kilburn Athletic", "Harbour City", "Harbour", "the Otters", "the Gulls"].forEach((p) => phrases.addPhrase(p));
  recognizer.recognizing = (_, e) => onPartial(e.result.text);
  recognizer.recognized = (_, e) => {
    if (e.result.reason === sdk.ResultReason.RecognizedSpeech && e.result.text) onHeard(e.result.text);
  };
  try {
    await new Promise<void>((resolve, reject) => recognizer.startContinuousRecognitionAsync(resolve, reject));
  } catch {
    recognizer.close();
    return null;
  }
  return () => recognizer.stopContinuousRecognitionAsync(() => recognizer.close(), () => recognizer.close());
}
