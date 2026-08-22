# Sarjy — Live Test Script (English only)

One continuous conversation, in order, from connecting to disconnecting — every line spoken and every
reply expected in English throughout. Read your lines aloud to the real deployed app.

This is a control alongside `docs/LIVE_TEST_SCRIPT.md`'s bilingual script, not a replacement for it —
its whole point is removing code-switching from the picture entirely, so if a reply ever drifts into
Arabic here, that's a pure language-anchoring bug, not an artifact of genuinely mixed input. Every turn
below carries an extra check for exactly that.

Use a fresh/private browser window so you start as a first-time user. Note the actual current date/time
before you begin, so you can sanity-check anything relative ("tomorrow," "after Maghrib").

Format: **YOU SAY** is your line. **EXPECT** is what should happen. Check the box once it matches.

---

### Turn 0 — Connect

- [ ] Click Start. Confirm: shows "Connecting…", then "Sarjy is getting ready…" — the mic meter, Mute,
      and Stop buttons must **not** appear yet.
- [ ] **EXPECT:** the greeting plays on its own, in English, mentioning it also speaks Arabic. Only once
      it's actually speaking do Mute/Stop/mic meter appear.

### Turn 1 — YOU SAY: _"Hello, how are you?"_

- [ ] **EXPECT:** a reply fully in English.

### Turn 2 — YOU SAY: _"My name is Omar, and my favorite color is blue."_

- [ ] **EXPECT:** acknowledges naturally, in English. (You'll test whether it actually _remembers_ this,
      unchanged, later in Turn 23 — and whether a correction genuinely replaces it, in Turns 24–25.)

### Turn 3 — YOU SAY: _"What time is Maghrib in Riyadh today?"_

- [ ] **EXPECT:** a real, natural-sounding English answer — a spoken sentence ("6 PM," or whatever the
      real time is), never a raw 24-hour string like "18:00".

### Turn 4 — YOU SAY: _"Book a meeting called Team Sync tomorrow at 3pm for 30 minutes."_

- [ ] **EXPECT:** it proposes the booking back to you in English (title, time, duration) — does **not**
      say it's booked yet.

### Turn 5 — YOU SAY: _"Yes, go ahead."_

- [ ] **EXPECT:** confirms booked, **in English**, states the time back as a natural spoken sentence ("3
      PM tomorrow," not "15:00"), and mentions you can undo it if it's wrong.
- [ ] **EXPECT (language check):** no Arabic anywhere in this reply. This is the exact shape of turn
      that broke before — a short English confirmation landing right after a tool call — so read the
      full reply closely, not just the first few words.

### Turn 6 — YOU SAY: _"Book something at 3:15 tomorrow."_ (no title given)

- [ ] **EXPECT:** it checks availability **immediately**, before ever asking you for a title — this used
      to ask "what should I call it?" first, only discovering the conflict afterward, wasting a whole
      turn on a detail that didn't matter once the slot turned out to be taken.
- [ ] **EXPECT:** it reports a scheduling conflict with Team Sync, in English — does **not** silently
      double-book, and does **not** end up asking you for a title at all once the conflict is found.

### Turn 7 — YOU SAY: _"Book something for tomorrow between 1 and 2."_

- [ ] **EXPECT:** it computes a 60-minute duration itself and does **not** ask you for the duration
      again.

### Turn 8 — YOU SAY: _"Actually, no, don't book it."_

- [ ] **EXPECT:** not booked — it drops the proposal rather than confirming. Reply in English.

### Turn 9 — YOU SAY: _"Book something for right after Maghrib tomorrow, for 30 minutes, call it 'Team catch-up'."_

- [ ] **EXPECT:** resolves the time relative to the real Maghrib prayer time (not a guess), proposes it
      back in English.

### Turn 10 — YOU SAY: _"Yes, confirm it."_

- [ ] **EXPECT:** confirms booked, in English.
- [ ] **EXPECT (language check):** same as Turn 5 — no Arabic in this reply.

### Turn 11 — YOU SAY: _"Rename Team Sync to 'Team Sync Weekly'."_

- [ ] **EXPECT:** proposes the rename (old title → new title) and waits — does **not** rename in the
      same turn. It should find the event by its time, not need you to repeat the time yourself if it
      already knows it from Turn 4.

### Turn 12 — YOU SAY: _"Yes."_

- [ ] **EXPECT:** confirms renamed, in English — says the new title back. The event's time (3 PM) and
      duration should be unchanged, only the title.

### Turn 13 — YOU SAY: _"Actually, move Team Sync Weekly to 4pm instead."_

- [ ] **EXPECT:** proposes the reschedule (same event, new time) and waits.

### Turn 14 — YOU SAY: _"Yes, go ahead."_

- [ ] **EXPECT:** confirms rescheduled, in English, states the new time back naturally.
- [ ] **EXPECT (language check):** no Arabic in this reply — another confirmation-after-tool-call.

### Turn 15 — YOU SAY: _"Move Team Sync Weekly to right after Maghrib tomorrow instead."_

- [ ] **EXPECT:** this should conflict with the "Team catch-up" event you booked in Turn 9/10 (same
      after-Maghrib slot) — it reports the conflict and does **not** move the meeting.

### Turn 16 — YOU SAY: _"Undo that."_

- [ ] **EXPECT:** since the last edit attempt failed (Turn 15's conflict), there should be nothing to
      undo from it — this should undo the successful 4pm reschedule from Turn 14 instead, putting Team
      Sync Weekly back at 3pm. If it instead says there's nothing to undo, that's also acceptable (a
      failed proposal never completed, so strictly nothing succeeded to reverse) — but it must **not**
      silently do nothing while claiming success, and it must **not** undo the Turn 12 rename.

### Turn 17 — YOU SAY: _"What's on my calendar tomorrow?"_

- [ ] **EXPECT:** lists "Team Sync Weekly" (reflecting the rename, and whichever time is actually
      correct after Turns 13/14/16) and "Team catch-up" after Maghrib — not the original "Team Sync"
      title, and not a duplicate event from any of the edit attempts above.

### Turn 18 — YOU SAY: _"Cancel Team Sync Weekly."_

- [ ] **EXPECT:** proposes the cancellation and waits — does **not** cancel in the same turn.

### Turn 19 — YOU SAY: _"No, keep it."_

- [ ] **EXPECT:** not cancelled. (It does get cancelled and then undone a few turns later, in Turns
      21–22 — Turn 31's final calendar check is what actually confirms it survived everything.)

### Turn 20 — YOU SAY: _"Cancel my meeting."_ (no time given, and you now have two events)

- [ ] **EXPECT:** since you have two events tomorrow, it either asks which one or checks your calendar
      first — it should **not** guess or cancel the wrong one.

### Turn 21 — YOU SAY: _"Team Sync Weekly."_ → then _"Yes, cancel it."_

- [ ] **EXPECT:** cancelled, reads back what was removed — in English.
- [ ] **EXPECT (language check):** no Arabic in the cancellation confirmation.

### Turn 22 — YOU SAY: _"Undo that."_

- [ ] **EXPECT:** Team Sync Weekly is restored, in English.

### Turn 23 — YOU SAY: _"What's my favorite color, and what's my name?"_

- [ ] **EXPECT:** correctly recalls **blue** and **Omar** from Turn 2, unchanged so far, in the same
      conversation. (This is the baseline the next two turns are about to change — if this doesn't
      already work, the correction test below can't tell you anything.)

### Turn 24 — Memory correction: favorite color

- [ ] YOU SAY: _"Actually, my favorite color is red, not blue."_
- [ ] **EXPECT:** acknowledges the change naturally, in English.
- [ ] YOU SAY: _"What's my favorite color?"_
- [ ] **EXPECT:** says **red only** — not blue, and not both. The old fact must actually be replaced,
      not just added alongside the new one.

### Turn 25 — Memory correction: spelled name

- [ ] YOU SAY: _"My name is actually spelled O-M-A-R."_
- [ ] **EXPECT:** acknowledges naturally.
- [ ] YOU SAY: _"What's my name?"_
- [ ] **EXPECT:** says **"Omar"** — a normal spoken name, never the literal letters "O-M-A-R" read back,
      and never a different spelling like "Umar". This is true whether or not it ever actually misheard
      you earlier — spelling something out should always resolve to the properly-capitalized word.

### Turn 26 — Barge-in test

- [ ] YOU SAY: _"Can you explain everything you're able to help me with?"_ — wait for it to start a
      longer reply, then **talk over it partway through**: _"Actually, what's your name?"_
- [ ] **EXPECT:** it stops mid-sentence and answers the new question — does **not** finish its old
      sentence first.

### Turn 27 — YOU SAY: _"Yes."_ — said suddenly, out of context, with nothing pending

- [ ] **EXPECT:** nothing gets booked, cancelled, confirmed, renamed, or rescheduled — there was no
      pending proposal for it to apply to.

### Turn 28 — YOU SAY: _"Can you send an email to my manager?"_ (out of scope)

- [ ] **EXPECT:** it says it can't do that, in English — does **not** invent a fake confirmation or
      hallucinate a tool result.

### Turn 29 — Disconnect and reconnect

- [ ] Click Stop. **EXPECT:** clean disconnect, back to the Start screen, no stuck state.
- [ ] Click Start again (same browser window, same identity).
- [ ] **EXPECT:** the new greeting is personalized — greets you by name (Omar, not Umar or "O-M-A-R"),
      in English (matching what you actually spoke last time), and does **not** re-explain "I speak
      Arabic and English" the way the very first greeting in Turn 0 did.

### Turn 30 — YOU SAY: _"What's my favorite color, and what's my name?"_

- [ ] **EXPECT:** says **red** (not blue — proves the color correction from Turn 24 persisted across
      the disconnect, not just within one conversation) and **Omar** (not O-M-A-R or Umar). Reply in
      English.

### Turn 31 — YOU SAY: _"What's on my calendar tomorrow?"_

- [ ] **EXPECT:** shows Team Sync Weekly (restored by the undo in Turn 22) and "Team catch-up" after
      Maghrib — confirms everything booked/renamed/rescheduled/cancelled/undone earlier actually
      persisted, and that no stray duplicate events exist from the edit-conflict attempts.

### Turn 32 — Wrap up

- [ ] Click Stop. Confirm clean disconnect.
- [ ] Read back over every reply in this whole conversation. **EXPECT:** not one word of Arabic
      anywhere, in any reply, at any point — including immediately after every booking/edit/cancellation
      confirmation (Turns 5, 10, 12, 14, 21), which is specifically what used to break.
- [ ] Note anything that didn't match its **EXPECT** line, with the exact turn number and what actually
      happened, so it's reproducible.

---

If every turn above matches — especially the language checks on Turns 5, 10, 12, 14, and 21, the
title-before-conflict check on Turn 6, and the correction checks on Turns 23–25 and 30 — that's real,
ordered evidence all three fixes hold under zero code-switching pressure, not just in the bilingual
script's mixed scenarios.
