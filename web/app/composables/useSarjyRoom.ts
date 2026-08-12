// Connects to the LiveKit room the agent joins.
// TODO(day 1): mute controls, reconnect handling.
// TODO(day 2): subscribe to the agent's "latency" topic (agent/latency.py)
// and feed LatencyHud.vue.

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

export type ConversationState = "listening" | "thinking" | "speaking";
export type ConnectError = "mic-denied" | "connect-failed";

export function useSarjyRoom() {
  const room = new Room();
  const connected = ref(false);
  const connecting = ref(false);
  const connectError = ref<ConnectError | null>(null);
  const transcript = ref<TranscriptEntry[]>([]);
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
    awaitingReply,
    agentSpeaking,
    conversationState,
    audioBlocked,
    resumeAudio,
  };
}
