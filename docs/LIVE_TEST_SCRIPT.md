# Sarjy — Live Test Script

One continuous conversation, in order, from connecting to disconnecting. Read your lines aloud to the
real deployed app — this is for an actual human talking to Sarjy through a real browser and microphone,
which nothing else in this project can substitute for. Each turn assumes the ones before it happened, so
don't skip around.

Use a fresh/private browser window so you start as a first-time user. Note the actual current date/time
before you begin, so you can sanity-check anything relative ("tomorrow," "after Maghrib").

Format: **YOU SAY** is your line. **EXPECT** is what should happen. Check the box once it matches.

---

### Turn 0 — Connect

- [ ] Click Start. Confirm: shows "Connecting…", then "Sarjy is getting ready…" — the mic meter, Mute,
      and Stop buttons must **not** appear yet.
- [ ] **EXPECT:** the greeting plays on its own, in English, mentioning it also speaks Arabic. Only once
      it's actually speaking do Mute/Stop/mic meter appear.

### Turn 1 — YOU SAY (English): _"Hello, how are you?"_

- [ ] **EXPECT:** a reply fully in English.

### Turn 2 — YOU SAY (Arabic): _"مرحبا، تمام. شو اسمك؟"_

- [ ] **EXPECT:** a reply fully in Arabic — confirms it switched languages rather than staying in English.

### Turn 3 — YOU SAY: _"اسمي عمر، ولوني المفضل هو الأزرق."_ (my name is Omar, my favorite color is blue)

- [ ] **EXPECT:** acknowledges naturally. (You'll test whether it actually _remembers_ this, unchanged,
      later in Turn 21 — and whether a correction genuinely replaces it, in Turns 27–28.)

### Turn 4 — YOU SAY: _"بدي أعمل booking لبكرا."_

- [ ] **EXPECT:** a naturally mixed reply — an English word like "booking" embedded in an otherwise-Arabic
      sentence — not forced entirely into one language, and not the same sentence repeated in both
      languages.

### Turn 5 — YOU SAY: _"What time is Maghrib in Riyadh today?"_

- [ ] **EXPECT:** a real, natural-sounding answer (switches back to English since you asked in English).

### Turn 6 — YOU SAY: _"Book a meeting called Team Sync tomorrow at 3pm for 30 minutes."_

- [ ] **EXPECT:** it proposes the booking back to you (title, time, duration) — does **not** say it's
      booked yet.

### Turn 7 — YOU SAY: _"Yes, go ahead."_

- [ ] **EXPECT:** confirms booked, states the time back as a natural spoken sentence ("3 PM tomorrow," not
      "15:00" or a raw date string), and mentions you can undo it if it's wrong.

### Turn 8 — YOU SAY: _"Book something at 3:15 tomorrow."_ (no title given)

- [ ] **EXPECT:** it checks availability **immediately**, before ever asking you for a title — this used
      to ask "what should I call it?" first, only discovering the conflict afterward, wasting a whole
      turn on a detail that didn't matter once the slot turned out to be taken.
- [ ] **EXPECT:** it reports a scheduling conflict with Team Sync — does **not** silently double-book,
      and does **not** end up asking you for a title at all once the conflict is found.

### Turn 9 — YOU SAY: _"Book something for tomorrow between 1 and 2."_

- [ ] **EXPECT:** it computes a 60-minute duration itself and does **not** ask you for the duration again.

### Turn 10 — YOU SAY: _"Actually, no, don't book it."_

- [ ] **EXPECT:** not booked — it drops the proposal rather than confirming.

### Turn 11 — YOU SAY: _"بدي احجز موعد بعد المغرب بكرا."_ (book something after Maghrib tomorrow)

- [ ] **EXPECT:** resolves the time relative to the real Maghrib prayer time (not a guess), and proposes it
      back.

### Turn 12 — YOU SAY: _"نعم، احجزها."_

- [ ] **EXPECT:** confirms booked.

### Turn 13 — YOU SAY: _"What's on my calendar tomorrow?"_

- [ ] **EXPECT:** correctly lists both meetings you actually confirmed (Team Sync, and the after-Maghrib
      one) — not the one you declined in Turn 10.
- [ ] **EXPECT:** every time is spoken fully in English (e.g. "3 PM"), with no stray Arabic word mixed in
      (e.g. "3 صباحًا") — this reply follows two Arabic turns (11–12), which is exactly the condition that
      once made an English time get a spurious Arabic AM/PM marker spliced into it.

### Turn 14 — YOU SAY: _"غيّر اسم Team Sync إلى Team Sync Weekly."_ (rename Team Sync to Team Sync Weekly)

- [ ] **EXPECT:** proposes the rename (old title → new title) and waits — does **not** rename in the same
      turn. It should find the event by its time, not need you to repeat the time yourself.

### Turn 15 — YOU SAY: _"نعم."_

- [ ] **EXPECT:** confirms renamed, in Arabic — says the new title back. The event's time (3 PM) and
      duration should be unchanged, only the title.
- [ ] **EXPECT (language check):** no stray English beyond the title itself, which is expected to stay as
      given — this is a confirmation right after a tool call, the exact shape that broke before.

### Turn 16 — YOU SAY: _"Actually, move Team Sync Weekly to 4pm instead."_

- [ ] **EXPECT:** proposes the reschedule (same event, new time) and waits.

### Turn 17 — YOU SAY: _"Yes, go ahead."_

- [ ] **EXPECT:** confirms rescheduled, in English, states the new time back naturally.

### Turn 18 — YOU SAY: _"انقل Team Sync Weekly لبعد المغرب بكرة بدل هيك."_ (move it to after Maghrib instead)

- [ ] **EXPECT:** this should conflict with the after-Maghrib event from Turn 11/12 (same slot) — it
      reports the conflict, in Arabic, and does **not** move the meeting.

### Turn 19 — YOU SAY: _"Undo that."_

- [ ] **EXPECT:** since the last edit attempt failed (Turn 18's conflict), there should be nothing to undo
      from it — this should undo the successful 4pm reschedule from Turn 17 instead, putting Team Sync
      Weekly back at 3pm. If it instead says there's nothing to undo, that's also acceptable (a failed
      proposal never completed, so strictly nothing succeeded to reverse) — but it must **not** silently
      do nothing while claiming success, and it must **not** undo the Turn 15 rename.

### Turn 20 — YOU SAY: _"What's on my calendar tomorrow?"_

- [ ] **EXPECT:** lists "Team Sync Weekly" (reflecting the rename, and whichever time is actually correct
      after Turns 16/17/19) and the after-Maghrib meeting — not the original "Team Sync" title, and not a
      duplicate event from any of the edit attempts above.

### Turn 21 — YOU SAY: _"What's my favorite color, and what's my name?"_

- [ ] **EXPECT:** correctly recalls **blue** and **Omar** from Turn 3, unchanged so far, in the same
      conversation. (This is the baseline Turns 27–28 are about to change — if this doesn't already work,
      the correction test there can't tell you anything.)

### Turn 22 — YOU SAY: _"Cancel Team Sync Weekly."_

- [ ] **EXPECT:** proposes the cancellation and waits — does **not** cancel in the same turn.

### Turn 23 — YOU SAY: _"No, keep it."_

- [ ] **EXPECT:** not cancelled. (It does get cancelled and then undone a few turns later, in Turns
      25–26 — Turn 34's final calendar check is what actually confirms it survived everything.)

### Turn 24 — YOU SAY: _"Cancel my meeting."_ (no time given, and you now have two events)

- [ ] **EXPECT:** since you have two events tomorrow, it either asks which one or checks your calendar
      first — it should **not** guess or cancel the wrong one.

### Turn 25 — YOU SAY: _"Team Sync Weekly."_ → then _"نعم، الغيه."_

- [ ] **EXPECT:** cancelled, reads back what was removed, in Arabic.
- [ ] **EXPECT (language check):** no stray English beyond the title in the cancellation confirmation.

### Turn 26 — YOU SAY: _"تراجع عن هذا."_ (undo that)

- [ ] **EXPECT:** Team Sync Weekly is restored, in Arabic.

### Turn 27 — Memory correction: favorite color

- [ ] YOU SAY: _"في الحقيقة، لوني المفضل صار أحمر، مو أزرق."_ (actually my favorite color is red now, not
      blue)
- [ ] **EXPECT:** acknowledges the change naturally, in Arabic.
- [ ] YOU SAY: _"شو لوني المفضل؟"_ (what's my favorite color?)
- [ ] **EXPECT:** says **red only** — not blue, and not both. The old fact must actually be replaced, not
      just added alongside the new one.

### Turn 28 — Memory correction: spelled name

- [ ] YOU SAY: _"My name is actually spelled O-M-A-R."_
- [ ] **EXPECT:** acknowledges naturally.
- [ ] YOU SAY: _"What's my name?"_
- [ ] **EXPECT:** says **"Omar"** — a normal spoken name, never the literal letters "O-M-A-R" read back,
      and never a different spelling like "Umar". This is true whether or not it ever actually misheard
      you earlier — spelling something out should always resolve to the properly-capitalized word.

### Turn 29 — Barge-in test

- [ ] YOU SAY: _"Can you explain everything you're able to help me with?"_ — wait for it to start a longer
      reply, then **talk over it partway through**: _"Actually, what's your name?"_
- [ ] **EXPECT:** it stops mid-sentence and answers the new question — does **not** finish its old
      sentence first.

### Turn 30 — YOU SAY: _"نعم."_ — said suddenly, out of context, with nothing pending

- [ ] **EXPECT:** nothing gets booked, cancelled, confirmed, renamed, or rescheduled — there was no
      pending proposal for it to apply to.

### Turn 31 — YOU SAY: _"Can you send an email to my manager?"_ (out of scope)

- [ ] **EXPECT:** it says it can't do that — does **not** invent a fake confirmation or hallucinate a tool
      result.

### Turn 32 — Disconnect and reconnect

- [ ] Click Stop. **EXPECT:** clean disconnect, back to the Start screen, no stuck state.
- [ ] Click Start again (same browser window, same identity).
- [ ] **EXPECT:** the new greeting is personalized — greets you by name (Omar, not Umar or "O-M-A-R"), in
      whichever language you mostly used last time, and does **not** re-explain "I speak Arabic and
      English" the way the very first greeting in Turn 0 did.

### Turn 33 — YOU SAY: _"What's my favorite color, and what's my name?"_

- [ ] **EXPECT:** says **red** (not blue — proves the color correction from Turn 27 persisted across the
      disconnect, not just within one conversation) and **Omar** (not O-M-A-R or Umar).

### Turn 34 — YOU SAY: _"What's on my calendar tomorrow?"_

- [ ] **EXPECT:** shows Team Sync Weekly (restored by the undo in Turn 26) and the after-Maghrib meeting —
      confirms everything booked/renamed/rescheduled/cancelled/undone earlier actually persisted, and
      that no stray duplicate events exist from the edit-conflict attempts.
- [ ] **EXPECT:** again, every time is spoken fully in English — no stray Arabic word mixed in (see
      Turn 13).

### Turn 35 — Wrap up

- [ ] Click Stop. Confirm clean disconnect.
- [ ] While it was talking at any point, listen closely to how it spoke dates/times back to you — should
      always sound like a natural sentence, never garbled or nonsensical.
- [ ] Read back over the confirmations in Turns 7, 12, 15, 17, and 25 specifically — each one lands right
      after a tool call, which is exactly the shape that once let language drift or duplicate work slip
      through.
- [ ] Note anything that didn't match its **EXPECT** line, with the exact turn number and what actually
      happened, so it's reproducible.

---

If every turn above matches, that's real, ordered evidence the whole system works end to end — and
specific moments here (the undo in Turn 19 and 26, the conflict-aware edit in Turn 18, the barge-in in
Turn 29, the memory corrections in Turns 27–28, and the returning-user greeting in Turn 32) are strong
material to actually show in the presentation rather than just describe.
