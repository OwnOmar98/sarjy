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

- [ ] **EXPECT:** acknowledges naturally. (You'll test whether it actually _remembers_ this later, in
      Turn 20.)

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

### Turn 8 — YOU SAY: _"Book something else for tomorrow at 3:15."_

- [ ] **EXPECT:** it reports a scheduling conflict with the meeting you just booked — does **not**
      silently double-book.

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

### Turn 14 — YOU SAY: _"Cancel the Team Sync meeting tomorrow at 3pm."_

- [ ] **EXPECT:** proposes the cancellation and waits — does **not** cancel in the same turn.

### Turn 15 — YOU SAY: _"No, keep it."_

- [ ] **EXPECT:** not cancelled. (Confirm later in Turn 21 that it's still there.)

### Turn 16 — YOU SAY: _"Cancel my meeting."_ (no time given)

- [ ] **EXPECT:** since you have two meetings tomorrow, it either asks which one or checks your calendar
      first — it should **not** guess or cancel the wrong one.

### Turn 17 — YOU SAY: _"The Team Sync one."_ → then _"Yes, cancel it."_

- [ ] **EXPECT:** cancelled, reads back what was removed.

### Turn 18 — YOU SAY: _"Undo that."_

- [ ] **EXPECT:** the Team Sync meeting is restored.

### Turn 19 — Barge-in test

- [ ] YOU SAY: _"Can you explain everything you're able to help me with?"_ — wait for it to start a longer
      reply, then **talk over it partway through**: _"Actually, what's your name?"_
- [ ] **EXPECT:** it stops mid-sentence and answers the new question — does **not** finish its old
      sentence first.

### Turn 20 — YOU SAY: _"What's my favorite color, and what's my name?"_

- [ ] **EXPECT:** correctly recalls "blue" and "Omar" from Turn 3, in the same conversation.

### Turn 21 — YOU SAY: _"نعم."_ — said suddenly, out of context, with nothing pending

- [ ] **EXPECT:** nothing gets booked, cancelled, or confirmed — there was no pending proposal for it to
      apply to.

### Turn 22 — YOU SAY: _"Can you send an email to my manager?"_ (out of scope)

- [ ] **EXPECT:** it says it can't do that — does **not** invent a fake confirmation or hallucinate a tool
      result.

### Turn 23 — Disconnect and reconnect

- [ ] Click Stop. **EXPECT:** clean disconnect, back to the Start screen, no stuck state.
- [ ] Click Start again (same browser window, same identity).
- [ ] **EXPECT:** the new greeting is personalized — greets you by name (Omar), in whichever language you
      mostly used last time, and does **not** re-explain "I speak Arabic and English" the way the very
      first greeting in Turn 0 did.

### Turn 24 — YOU SAY: _"What's my favorite color?"_

- [ ] **EXPECT:** still correctly recalls "blue" — proves memory survived across the disconnect, not just
      within one conversation.

### Turn 25 — YOU SAY: _"What's on my calendar tomorrow?"_

- [ ] **EXPECT:** shows the after-Maghrib meeting and the restored Team Sync meeting — confirms everything
      booked/cancelled/undone earlier actually persisted.
- [ ] **EXPECT:** again, every time is spoken fully in English — no stray Arabic word mixed in (see Turn 13).

### Turn 26 — Wrap up

- [ ] Click Stop. Confirm clean disconnect.
- [ ] While it was talking at any point, listen closely to how it spoke dates/times back to you — should
      always sound like a natural sentence, never garbled or nonsensical.
- [ ] Note anything that didn't match its **EXPECT** line, with the exact turn number and what actually
      happened, so it's reproducible.

---

If every turn above matches, that's real, ordered evidence the whole system works end to end — and
specific moments here (the undo in Turn 18, the barge-in in Turn 19, the returning-user greeting in Turn 23) are strong material to actually show in the presentation rather than just describe.
