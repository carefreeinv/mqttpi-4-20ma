# Anchor conventions for this project

Detected/declared language or framework: **python**

- Follow SOLID principles by default (see `.anchor/ANCHOR.md`, "Code quality defaults").
- Prefer python's idiomatic composition mechanism over deep inheritance: Protocols (PEP 544) / narrow ABCs, composition over inheritance.
- Actively avoid spaghetti control flow and dead code; treat shortcuts as tracked
  technical debt (name them in `## Deferred / concerns`), not silent debt.
- Wrong guess? Edit this file — it's just a note for whichever model works on this project next.

## Preferred orchestrator

**Preferred orchestrator:** `grok`

This is who should **plan multi-step work, coordinate fleets, make architecture calls, review large merges, and evaluate plan **Depends on** against existing `.plans/**` for this project.

### Temporary coordinator (when Preferred orchestrator is unset)

If the Preferred orchestrator line is **unset** / empty and **no** project MCP coordinator is registered for this tree:

1. If **you** are a **frontier or near-frontier** model (e.g. Fable-class, Opus-class, GPT-5.x Sol/Terra-class, a Grok session used as a strong session lead — see `.anchor/model-fitness.md`), you **may take a temporary coordinator role** for this session only.
2. While temporary coordinator: inventory `.plans/**`, propose/fill **Depends on**, draft or refine plans under `drafts/`, refuse to start work with unmet deps, and say clearly: `TEMPORARY-COORDINATOR: <your model name> — Preferred orchestrator unset`.
3. Still recommend the operator set a durable Preferred orchestrator (`anchor --set-orchestrator …`) so the next session is not ambiguous.
4. If you are **mid / small / local / executor-tier**, do **not** self-appoint; escalate (below) or ask the human to pick a stronger session.

### If you are a lesser / executor / local / small model

When the user asks you to act as the project **orchestrator** (long-horizon planning across services, multi-hour autonomy, fleet coordination, dependency analysis across plans, promotion of drafts, or any task in your weak column in `.anchor/model-fitness.md`):

1. Do **not** silently attempt it.
2. Recommend the **Preferred orchestrator** above when set; otherwise recommend a frontier/near-frontier session as temporary coordinator (or the top of the model-priority list).
3. Your ENTIRE first line must be `SUGGEST-ESCALATE: grok — <one-line reason>`, then stop.
4. You may still execute well-scoped task specs and ready `/work` plans whose **Preferred models** match your tier **and** whose **Depends on** are met.

**This cuts both ways — do not under-claim.** The escalation duty above covers orchestration-class work and your documented weak column. It is **not** a reason to decline scoped work you can do. Specifically:

- Only the **tiers** in a plan's **Preferred models** set the floor. Stronger product *names* in that list are extra good-fit hits, not a raised bar.
- A plan with no **Preferred models** line, or one naming only models you are not, is **unknown** fit — eligible, not off-limits.
- Difficulty discovered *after* claiming is a per-step route/escalate decision, not grounds to refuse the claim.

Refusing work that fits you stalls the backlog exactly as badly as attempting work that doesn't. "A better model could do this" is not a fit verdict.

Change the durable orchestrator any time: edit the bold line, or run `anchor <project-dir> --set-orchestrator <token>`.

## Model routing (fit check)

Before starting any task, check your own row in `.anchor/model-fitness.md` on
**two axes** — power, then specialty. Power is **bidirectional**: if the task
lands in your weak column (too hard), your ENTIRE first line must be
`SUGGEST-ESCALATE: <better-suited model> — <one-line reason>`, then stop —
prefer the **Preferred orchestrator** above when the work is orchestration-class.
If the task is clearly over-tier for this session instead (too easy —
boilerplate, formatting, a rename, a single well-specified function), your
ENTIRE first line must be `SUGGEST-DOWNGRADE: <cheaper model or tier> —
<one-line reason>`, then stop and wait. If power is fine but you are the wrong
*kind* of model for the work (e.g. a pure chat session handed multi-file
software execution), `SUGGEST-REROUTE: <target model or profile> —
<one-line reason>`, then stop. Good fit on every axis → silence. The operator
may insist you proceed on any of these — then stay strictly in scope and mark
shaky output `(unverified)`.
The operator's model priority for this project, highest first (saved by `config.sh` at scaffold time):

1. `grok`
2. `nim`
3. `claude:sonnet`
4. `claude:opus`
5. `claude:fable`

Suggest the nearest better-fitting model from this list; skip tiers only when the fitness table says every intermediate one is also a poor fit. For orchestration-class work, jump to the Preferred orchestrator.
