// Connects to the LiveKit room the agent joins.
// TODO(day 1): mute controls, reconnect handling.

import { Room, RoomEvent, Track, type Participant } from "livekit-client";

// Anonymous per-browser identity (docs/PRD.md non-goals: no auth, just
// this + a "forget me" button). Memory keys facts off this id — a
// fresh random identity per session would make cross-session recall
// silently never work, so it must survive a reload.
const IDENTITY_STORAGE_KEY = "sarjy:identity";

function getOrCreateIdentity(): string {
  const existing = localStorage.getItem(IDENTITY_STORAGE_KEY);
  if (existing) return existing;
  const identity = crypto.randomUUID();
  localStorage.setItem(IDENTITY_STORAGE_KEY, identity);
  return identity;
}

export interface TranscriptEntry {
  id: string;
  role: "user" | "agent";
  text: string;
  final: boolean;
  /** ms since connect() — a session-elapsed clock, not a wall time. */
  elapsedMs: number;
}

export interface LatencyStage {
  stage: string;
  ms: number;
}

export interface LatencyPercentile {
  stage: string;
  p50: number;
  p95: number;
  n: number;
}

// Nearest-rank method: sorted[0] for p50 of a single sample, etc. — no
// interpolation, since these are small samples (a handful of turns per
// demo session), not a statistics-grade dataset.
function percentile(sorted: number[], p: number): number {
  const idx = Math.min(
    sorted.length - 1,
    Math.max(0, Math.ceil((p / 100) * sorted.length) - 1),
  );
  return sorted[idx]!;
}

export type ConversationState = "listening" | "thinking" | "speaking";
export type ConnectError = "mic-denied" | "connect-failed";

export function useSarjyRoom() {
  const room = new Room();
  const connected = ref(false);
  const connecting = ref(false);
  const connectError = ref<ConnectError | null>(null);
  const transcript = ref<TranscriptEntry[]>([]);
  const latencyStages = ref<LatencyStage[]>([]);
  // Every stage value seen this session, keyed by stage name — unlike
  // latencyStages (reset per turn, for the current-turn waterfall), this
  // only grows, so p50/p95 (docs/PRD.md §4) reflect the whole session,
  // not one lucky/unlucky turn.
  const latencyHistory = ref<Record<string, number[]>>({});
  const latencyPercentiles = computed<LatencyPercentile[]>(() =>
    Object.entries(latencyHistory.value)
      .map(([stage, values]) => {
        const sorted = [...values].sort((a, b) => a - b);
        return {
          stage,
          p50: percentile(sorted, 50),
          p95: percentile(sorted, 95),
          n: sorted.length,
        };
      })
      .sort((a, b) => a.stage.localeCompare(b.stage)),
  );
  const awaitingReply = ref(false);
  const agentSpeaking = ref(false);
  // Browsers can silently block audio autoplay even after a user gesture
  // (the "Start talking" click) if the agent's track subscribes slightly
  // later, asynchronously, outside that click's immediate scope — LiveKit
  // surfaces this via canPlaybackAudio/AudioPlaybackStatusChanged rather
  // than an error. Unwired, this fails exactly like a real TTS bug: the
  // transcript updates live, nothing is heard, zero indication why.
  const audioBlocked = ref(false);
  let connectedAt = 0;

  function resumeAudio() {
    void room.startAudio();
  }

  // Real signals only — awaitingReply and agentSpeaking are both driven by
  // room events, never inferred or faked, so this can't drift from what's
  // actually happening.
  const conversationState = computed<ConversationState>(() => {
    if (agentSpeaking.value) return "speaking";
    if (awaitingReply.value) return "thinking";
    return "listening";
  });

  // Local mic level, 0-1 — read straight off the published mic track via
  // Web Audio rather than room.localParticipant.audioLevel, which only
  // updates on the SFU's ~periodic active-speaker push (visibly laggy/
  // steppy); an AnalyserNode on the raw track updates every animation
  // frame, so the meter actually tracks your voice instead of trailing it.
  const micLevel = ref(0);
  let audioCtx: AudioContext | null = null;
  let analyser: AnalyserNode | null = null;
  let levelData: Uint8Array<ArrayBuffer> | null = null;
  let rafId: number | null = null;

  function startLevelMeter(track: MediaStreamTrack) {
    audioCtx = new AudioContext();
    const source = audioCtx.createMediaStreamSource(new MediaStream([track]));
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 256;
    analyser.smoothingTimeConstant = 0.6;
    source.connect(analyser);
    // Explicit ArrayBuffer, not the (frequencyBinCount) shorthand — that
    // infers Uint8Array<ArrayBufferLike>, which getByteTimeDomainData's
    // stricter DOM typing (Uint8Array<ArrayBuffer>) rejects.
    levelData = new Uint8Array(new ArrayBuffer(analyser.frequencyBinCount));

    const tick = () => {
      if (!analyser || !levelData) return;
      analyser.getByteTimeDomainData(levelData);
      let sumSquares = 0;
      for (const sample of levelData) {
        const normalized = (sample - 128) / 128;
        sumSquares += normalized * normalized;
      }
      const rms = Math.sqrt(sumSquares / levelData.length);
      // Raw mic RMS sits low for normal speech — scaled up so the meter
      // actually uses its visual range instead of barely twitching.
      micLevel.value = Math.min(1, rms * 4);
      rafId = requestAnimationFrame(tick);
    };
    tick();
  }

  function stopLevelMeter() {
    if (rafId !== null) cancelAnimationFrame(rafId);
    rafId = null;
    analyser = null;
    levelData = null;
    void audioCtx?.close();
    audioCtx = null;
    micLevel.value = 0;
  }

  async function connect() {
    // Guards a double-click during the async window below from firing a
    // second concurrent token fetch + room.connect() on the same Room.
    if (connecting.value || connected.value) return;
    connecting.value = true;
    connectError.value = null;
    transcript.value = [];
    latencyStages.value = [];
    latencyHistory.value = {};
    awaitingReply.value = false;
    agentSpeaking.value = false;
    connectedAt = Date.now();

    try {
      const { token, url } = await $fetch<{ token: string; url: string }>(
        "/api/token",
        { query: { identity: getOrCreateIdentity() } },
      );
      await room.connect(url, token);
      audioBlocked.value = !room.canPlaybackAudio;
      const publication =
        await room.localParticipant.setMicrophoneEnabled(true);
      if (publication?.track) {
        startLevelMeter(publication.track.mediaStreamTrack);
      }
      connected.value = true;
    } catch (err) {
      // Graceful degradation (docs/PRD.md §2 — "voice has no spinner,
      // silence is the failure mode"): a denied mic permission or a
      // failed room.connect() must surface real feedback, not leave the
      // user on the button forever. Never leave a half-connected room
      // around (e.g. joined but no mic) — reset fully so retrying starts
      // clean.
      connectError.value =
        err instanceof DOMException && err.name === "NotAllowedError"
          ? "mic-denied"
          : "connect-failed";
      room.disconnect();
      connected.value = false;
    } finally {
      connecting.value = false;
    }
  }

  function disconnect() {
    room.disconnect();
  }

  room.on(RoomEvent.Disconnected, () => {
    connected.value = false;
    awaitingReply.value = false;
    agentSpeaking.value = false;
    audioBlocked.value = false;
    stopLevelMeter();
  });

  room.on(RoomEvent.AudioPlaybackStatusChanged, () => {
    audioBlocked.value = !room.canPlaybackAudio;
  });

  // livekit-client's vanilla SDK (unlike its React/Vue component
  // wrappers) does not auto-attach a subscribed remote track to a
  // playable element — without this, audio arrives over the connection
  // with nowhere to play into.
  room.on(RoomEvent.TrackSubscribed, (track) => {
    if (track.kind === Track.Kind.Audio) {
      track.attach();
    }
  });

  room.on(RoomEvent.TrackUnsubscribed, (track) => {
    if (track.kind === Track.Kind.Audio) {
      track.detach();
    }
  });

  // Per-turn latency waterfall (agent/latency.py). Stages publish from two
  // different places agent-side (a sync event handler and an async method
  // awaiting a slower memory.py round-trip) and can arrive in any order —
  // grouping by the agent's own turn counter, not stage name or arrival
  // order, is what keeps a same-turn stage from being wiped or misfiled
  // by a race between them.
  let currentLatencyTurn = -1;
  room.on(RoomEvent.DataReceived, (payload, _participant, _kind, topic) => {
    if (topic !== "latency") return;
    const { stage, ms, turn } = JSON.parse(
      new TextDecoder().decode(payload),
    ) as LatencyStage & {
      turn: number;
    };
    if (turn !== currentLatencyTurn) {
      currentLatencyTurn = turn;
      latencyStages.value = [];
    }
    const roundedMs = Math.round(ms);
    latencyStages.value.push({ stage, ms: roundedMs });
    (latencyHistory.value[stage] ??= []).push(roundedMs);
  });

  // Coarse (SFU-pushed, not per-frame) but it's the only honest signal for
  // "is the agent's voice active right now" — used for the thinking/
  // speaking state label only, never for a fabricated amplitude meter.
  room.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
    agentSpeaking.value = speakers.some(
      (p) => p.identity !== room.localParticipant.identity,
    );
  });

  // Fires for both the user's own STT transcript and the agent's
  // TTS-synchronized text — AgentSession publishes both through the same
  // synchronized-transcription mechanism, so no agent-side change is
  // needed to receive either.
  room.on(
    RoomEvent.TranscriptionReceived,
    (segments, participant?: Participant) => {
      const isUser = participant?.identity === room.localParticipant.identity;
      for (const segment of segments) {
        const existing = transcript.value.find((e) => e.id === segment.id);
        if (existing) {
          existing.text = segment.text;
          existing.final = segment.final;
        } else {
          transcript.value.push({
            id: segment.id,
            role: isUser ? "user" : "agent",
            text: segment.text,
            final: segment.final,
            elapsedMs: Date.now() - connectedAt,
          });
        }
      }
      if (isUser && segments.some((s) => s.final)) {
        awaitingReply.value = true;
      } else if (!isUser) {
        awaitingReply.value = false;
      }
    },
  );

  return {
    room,
    connected,
    connecting,
    connectError,
    connect,
    disconnect,
    micLevel,
    transcript,
    latencyStages,
    latencyPercentiles,
    awaitingReply,
    agentSpeaking,
    conversationState,
    audioBlocked,
    resumeAudio,
  };
}
