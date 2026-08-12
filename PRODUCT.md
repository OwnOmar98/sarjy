# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Nuxt 4 (Vue) + Vuetify web frontend; LiveKit Agents (Python) voice pipeline; Postgres/pgvector for memory; Redis for caching. Full rationale in `docs/PRD.md`.

## Users

In-universe: a bilingual (Arabic/English) individual using a personal voice assistant — recalling facts ("what's my favorite color"), checking their schedule ("what's on my plate tonight"), and booking things via natural conversation (e.g. scheduling relative to prayer time).

Real audience: Sarj (سرج) — an Arabic voice-AI company (inbound/outbound call agents, Arabic + English) — evaluating this as a take-home assignment. The panel is mostly technical, some not; delivered as a ~5-minute live presentation, judged primarily on communication and execution/craft rather than a finished product with paying users.

## Product Purpose

Demonstrate a working, deployed, bilingual voice assistant that a technical Arabic-voice-AI team will recognize as speaking their language — both literally (Arabic/English code-switching handled well) and technically (measured latency, real memory, tool use, honest treatment of where Arabic currently lags English in the voice-AI stack).

## Positioning

Not a demo of "an LLM with a microphone." The differentiator: Arabic/English code-switching treated as a first-class requirement rather than "also supports Arabic"; a multi-step tool chain grounded in local context (prayer times + calendar, not a generic weather-bot demo); and transparent, measured engineering tradeoffs (latency budgets, provider comparisons, cost-at-scale model) instead of a black-box pitch.

## Operating Context

- Delivered as a public GitHub repo (`github.com/OwnOmar98/sarjy`) + a deployed web app + a ~5-minute live presentation to the Sarj team.
- Built solo, part-time, over a ~3–4 day window.
- Review happens continuously, not just at handoff: a shared PRD/TDD sent day 0, a draft PR, and daily async updates are explicit rubric criteria, not optional extras.

## Capabilities and Constraints

- Bilingual voice conversation (Arabic + English, including mid-sentence code-switching); remembers facts across sessions (long-term memory, not just in-call context).
- At least one real external API integration — committed: prayer times (Aladhan API) + calendar booking as a multi-step tool-use flow.
- Explicit non-goals (see `docs/PRD.md` §1): no telephony, no video avatar, no multimodal image input, no production auth system (anonymous per-browser identity only).
- Deploy target: Vercel (web) + LiveKit Cloud (transport/SFU) + Fly.io (Python agent worker).
- `docs/PRD.md` is the authoritative technical plan (architecture, latency budget, memory design, cost model, tiered scope, risks) — this file records product truth, that one records the engineering plan.

## Brand Commitments

- Name "Sarjy" is fixed — given directly in the take-home brief, not open for renaming.
- No logo, palette, or typography has been set by Sarj — nothing here is externally imposed.
- Internally, a real visual direction is chosen and built: **Mashrabiya Lattice** (warm sand/walnut/brass, a constructed diamond-trellis + 8-point-star module, IBM Plex Sans/Arabic). Full direction contract in `web/app/app.vue`'s opening template comment; recorded formally in `DESIGN.md` once the finish review runs.

## Evidence on Hand

- No real user testimonials, case studies, press, or production usage data — this is a from-scratch take-home build, not a live product.
- No existing logo or brand assets from Sarj to reference.

## Product Principles

1. **Measure, don't assert.** Latency, cost, and Arabic-vs-English quality are reported as real numbers with a stated method, not claimed.
2. **Arabic is first-class, not a translated afterthought.** Code-switching, RTL, and Arabic TTS/ASR quality get dedicated attention — not just "also supports."
3. **Communication is part of the deliverable.** The PRD, daily updates, and draft PR count toward the outcome as much as the running app does.
4. **Honest tradeoffs over polish theater.** Explicit non-goals and stated limitations (e.g. Arabic endpointing likely underperforming English) are treated as credibility signals, not weaknesses to hide.

## Accessibility & Inclusion

Bilingual RTL/LTR support (Arabic + English) is a functional requirement, not an accessibility add-on. No additional accessibility requirement has been specified beyond that.
