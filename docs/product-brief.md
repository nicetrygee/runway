# Runway — Product Brief

*Status: delivered. Written Sep 2026 as the scope for the MVP build; all five MVP features shipped by 9 Sep 2026 (PRs #3–#7). Kept as the record of what Runway is for and why it's shaped this way. [`technical-design.md`](technical-design.md) covers how it was built and where the build departed from the plan.*

## The problem

> What should I be doing right now, what am I waiting on, what am I neglecting, and where am I becoming a bottleneck?

Runway is a personal cognition aid for an Engineering Manager, structured around the actual working day. It is not a better task manager. Its job is to **reduce the cognitive load of being an EM** by remembering commitments, maintaining relationships, managing delegation, protecting strategic time, and continuously deciding what deserves attention.

The one question the MVP had to answer honestly: *does this help an EM feel more in control of their workload with less mental overhead?* Every feature had to earn its place by serving that, and nothing else.

## Why "another Linear" is the trap

Before this build, Runway felt like a Jira/Linear clone because it only **stored state**: a task had a status, a load and a due date, and I went and read the list. Jira, Linear and Asana are deliberately built that way. They are *team systems of record*, designed to be a shared, neutral ledger, which is exactly why they never tell one person what to do next or notice that they've gone quiet on someone.

Runway's differentiation is that it **derives attention from state + time + relationships**. The moat is the derivation layer, not the CRUD underneath it. The four questions are all things a system of record refuses to answer:

- **What should I do now:** a ranked recommendation, not a list, that fits the time I actually have.
- **What am I waiting on:** the items where the ball is in someone else's court, aged so I can chase them.
- **What am I neglecting:** high-stakes work and people that have gone quiet.
- **Where am I a bottleneck:** the mirror of "waiting on", the things where *other people* are blocked on *me*.

## The core model

The most important design idea: **model the relationship and its direction, plus time, not four separate lists.** "Waiting on", "delegated" and "commitment" are the same relationship seen from different angles, so they share one item model with a direction discriminator and a person attached.

**Streams** (the direction an item points):

| Stream | Meaning | Whose court | Answers |
|---|---|---|---|
| `task` | My own work | Mine | "What should I do now" |
| `commitment` | A promise I made to someone (I do the work) | Mine, owed to them | "What did I promise the CTO" |
| `delegation` | I asked someone else to do it | Theirs | "What have I handed off" |
| `waiting` | I'm blocked pending someone/something | Theirs | "What am I waiting on" |

`delegation` and `waiting` are close cousins (both mean the ball isn't in my court), and the original requirements suggested combining them. I kept them distinct because the follow-up behaviour differs: a delegation I chase as a manager, a waiting-on I chase as a stakeholder. The data model is the same shape.

**Signals** the app *computes* rather than stores (the derivation layer):

- **Urgency:** from due date proximity.
- **Blocking:** is this item holding other people up (bottleneck detection).
- **Staleness:** how long since this item, or this person, was last touched (neglect detection).
- **Time-fit:** does its estimated effort fit the window I have right now.
- **Reactive vs proactive balance:** how much of the week is firefighting vs strategy.
- **Capacity load:** committed effort vs available hours.

## MVP scope

Deliberately narrow, in priority order. The product revolves around one loop: **CAPTURE → UNDERSTAND → PRIORITISE → ACT → DELEGATE/WAIT → REVIEW → REPLAN.**

**1. Unified Work Inbox** *(capture must be effortless or nothing else matters)*
One place to dump a line, such as "Sarah asked me to review the Q4 hiring plan", and have it classified: **type** (People / Delivery / Technical / Stakeholder / Strategy / Hiring / Operational / Personal-admin), **priority** (Critical / Important / Normal / Delegate / Ignore), **expected effort** (5m / 30m / 1h / 2h / half-day / multi-day), an optional **person**, a **stream**, and a **due date**. This extended the existing Anthropic quick-add hook to the new taxonomy and to spotting a person and a commitment.
*Shipped:* `/add` and `/quick-add` (Slice A). Works without an API key; the fields are then filled in by hand.

**2. "What should I do now?"** *(where it stops being Todoist)*
Instead of 47 open tasks, a short ranked list of recommended next actions with the *reason* on each ("10 min · blocking 3 people", "45 min · due tomorrow"). It weighs priority, deadline, blocking, staleness and available time, and it is **time-box aware**: with 25 minutes before a meeting it must not recommend a two-hour strategy document.
*Shipped:* `/now` (Slice B), with items that don't fit the window listed separately.

**3. Commitment tracker** *(attacks the EM's cognitive burden directly)*
The promises I make constantly ("I'll send that tomorrow", "I'll speak to Sarah"), tracked as first-class items: to whom, what, due, status. Manual creation only; automatic extraction from meetings is post-MVP.
*Shipped:* `/commitments` (Slice C).

**4. Delegated + Waiting On** *(a huge slice of real EM work that task managers don't model)*
Every item can be owned by me or by someone else. Two lists, "Waiting on others" (James — architecture investigation — 2 days) and "I've delegated" (Sarah — hiring analysis — due Thursday), whose key behaviour is **follow-up prompting and escalation**: "James' investigation was due yesterday. Follow up?" / "You've been waiting 6 days for Product to confirm scope."
*Shipped:* `/waiting`, `/delegated`, follow-up logging, and a `/people` directory (Slice C).

**5. Weekly workload review** *(closes the loop)*
A simple Friday ritual: counts (completed / carried forward / delegated / waiting), a time breakdown by type, a strategic-time percentage, and a capacity read ("31 hours of committed work next week, ~24 available"), plus delegation suggestions. It ends by asking what to carry into next week: a deliberate reset.
*Shipped:* `/review` (Slice D). An optional AI narrative sits on top of the numbers, off unless `WEEKLY_SUMMARY_AI_ENABLED=1`.

The landing page (`/`) puts the four questions on one screen by composing these surfaces (PR #7).

## Out of scope for the MVP

Left out on purpose, to find out whether the core workflow is valuable before adding complexity: Slack / Teams / Jira / GitHub integrations, automatic meeting transcription, sophisticated people analytics, performance management, OKRs, project/sprint planning, elaborate dashboards, complex AI agents, and the full "EM memory" Q&A across sources. The people-management view and meeting intelligence from the full vision are post-MVP; the schema (`people`, `events`) is shaped so they can slot in without a rewrite.

## Decisions (Sep 2026)

1. **Four streams.** `task` / `commitment` / `delegation` / `waiting` are kept distinct because the follow-up behaviour differs, even though they share one data model.
2. **A lightweight `people` table from day one.** Cheap to add then, expensive to retrofit, and the join point for the later vision ("James has 4 open items from you", the people-attention view).
3. **Weekly-review time from estimated effort + a manual meeting-hours number.** The review sums `effort_minutes` on items and takes one manually entered "meeting hours this week" figure. No calendar integration; real time tracking is post-MVP.
