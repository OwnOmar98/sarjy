// Isomorphic (Nuxt 4's shared/ layer — #shared, auto-imported both
// app/ and server/ side) because the live-update wire contract needs to
// agree on exactly one shape in three places: agent/conversations.py's
// _session_dict()/_message_dict() (Python, but by field name — this is
// the TypeScript half of that same contract), server/api/internal/
// notify.post.ts (relays the event through, never reshapes it), and
// app/composables/useLiveUpdates.ts (the consumer). One definition
// instead of three copies drifting apart.

export interface SessionSummary {
  id: string;
  started_at: string;
  updated_at: string;
  ended_at: string | null;
  summary: string | null;
}

export interface TranscriptMessage {
  id: string;
  role: string;
  content: string;
  created_at: string;
}

export type LiveUpdateEvent =
  | { type: "session-upserted"; session: SessionSummary }
  | { type: "message-added"; sessionId: string; message: TranscriptMessage };
