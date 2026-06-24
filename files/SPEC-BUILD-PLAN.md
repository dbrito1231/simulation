# SPEC-BUILD-PLAN.md — AI Simulation World
## Master Consolidated Plan | KISS Methodology | Manager–Employee Subagent Model | Spec-Driven

---

## 0. HOW TO USE THIS PLAN

This is the **master plan**. It references 6 separate spec files located in `./specs/`.

**Workflow:**
1. Paste this master file into Cursor as the **Manager subagent** instruction.
2. The Manager reads this plan, then delegates each spec file to a **Worker subagent**, one at a time.
3. Each spec is built, reviewed at its gate, and approved before the next begins.
4. No code is written before the relevant spec is read in full.

**The spec files:**

| Spec | File | What it covers |
|------|------|----------------|
| 00 | `specs/00-overview.md` | Project goals, scope, reference to Project Sid |
| 01 | `specs/01-architecture.md` | Tech stack, file structure, data flow |
| 02 | `specs/02-server-spec.md` | `server.py` Flask proxy + LM Studio integration |
| 03 | `specs/03-world-spec.md` | Canvas world rendering (zones, terrain) |
| 04 | `specs/04-agent-spec.md` | The 12 agents, sprites, state, movement |
| 05 | `specs/05-simulation-loop-spec.md` | Game loop, LLM calls, roles, relationships, UI |

---

## 1. SUBAGENT ASSIGNMENT MODEL (Manager–Employee)

This project uses the **manager–employee delegation model**.

### The Manager subagent
- Owns this master plan
- Reads each spec and assigns it to a Worker
- Reviews Worker output at each gate
- **Never writes code itself** — it only delegates, reviews, and approves
- Enforces the gate system and the no-assumptions rule
- Reports status back to the human at every gate

### The Worker subagent
- Receives one spec file at a time from the Manager
- Builds exactly what that spec describes — nothing more
- Follows KISS: simplest working solution
- Stops at its gate and hands output back to the Manager
- Asks for clarification (multiple choice format) when anything is unclear

### Delegation order (strict — do not reorder)

```
Manager → Worker: build Spec 00 understanding  → GATE A (plan approval)
Manager → Worker: build Spec 02 (server.py)    → GATE B
Manager → Worker: build Spec 03 (world render) → GATE C
Manager → Worker: build Spec 04 (agents)       → GATE D
Manager → Worker: build Spec 05 (sim loop)     → GATE E
Manager → Human: final review                  → GATE F
```

Spec 01 (architecture) is read by both Manager and Worker before any building starts — it is reference context, not a build target.

---

## 2. BUILD ORDER (STRICT)

| Step | Spec | Output | Gate |
|------|------|--------|------|
| 1 | 00 + 01 | Worker confirms understanding + outputs a build plan | GATE A |
| 2 | 02 | `server.py` complete and runnable | GATE B |
| 3 | 03 | `index.html` with world rendering only (no agents) | GATE C |
| 4 | 04 | `index.html` with 12 agents drawn + moving (no LLM) | GATE D |
| 5 | 05 | Full simulation: LLM-driven, roles, relationships, UI | GATE E |
| 6 | — | Human final acceptance review | GATE F |

**Rule:** No step begins until the previous gate is explicitly approved by the human.

---

## 3. GATE SYSTEM

| Gate | Trigger | What the Manager must show the human | Pass condition |
|------|---------|--------------------------------------|----------------|
| GATE A | After reading specs 00 + 01 | Plain-English plan of both files, list of every function/route | Human approves the plan |
| GATE B | `server.py` done | Full file + how-to-run instructions | Server starts, no errors |
| GATE C | World renders | Screenshot/description of rendered world | All zones visible + labeled |
| GATE D | Agents added | 12 sprites drawn, moving with rule-based logic, no LLM | Agents distinct + moving |
| GATE E | LLM connected | All 12 agents thinking via LM Studio | Agents act on real decisions |
| GATE F | Everything done | Full success-criteria checklist | All criteria met |

---

## 4. ANTI-HALLUCINATION CHECKLIST (MULTI-GATE)

The Manager enforces this checklist at **every** gate. The Worker must confirm each item before a gate passes:

1. **No invented files** — only `server.py` and `index.html` exist. Any extra file requires human approval.
2. **No invented libraries** — server uses only Flask, flask-cors, requests. HTML uses zero external libraries.
3. **No invented routes** — server exposes only `POST /agent/think`.
4. **No invented agents** — exactly the 12 named agents in Spec 04, no more, no fewer.
5. **No invented zones** — exactly the zones listed in Spec 03.
6. **No assumed defaults** — any unspecified behavior triggers a clarification question, never a guess.
7. **No copyrighted assets** — all sprites and terrain drawn with Canvas primitives; the uploaded character image is NOT used.
8. **No scope creep** — anything in the "Out of Scope" list stays unbuilt.
9. **Code matches spec** — every function named in the spec exists; nothing extra is added.
10. **Runs without errors** — no console errors, no Python exceptions, before any gate passes.

If any item fails, the gate does **not** pass — the Worker fixes it and the Manager re-checks.

---

## 5. CLARIFICATION PROTOCOL

Whenever anything is unclear or uncovered, the subagent STOPS and asks in this exact format:

```
CLARIFICATION NEEDED:

I'm unsure about: [brief description]

Options:
A) [option A]
B) [option B]
C) [option C]
D) [other — please describe]

Which would you prefer?
```

No proceeding until answered. No assumptions. No invented edge-case solutions.

---

## 6. SCOPE SUMMARY

**In scope:** browser-based pixel world, 12 LLM-driven agents, real-time movement, role specialization, relationships, trading, resource collection, activity log UI, LM Studio integration via Flask proxy.

**Out of scope:** combat, health, hunger/death, multiplayer, save/load, databases, WebSockets, accounts, mobile.

---

## 7. SUCCESS CRITERIA (GATE F)

- [ ] `server.py` starts with no errors
- [ ] `index.html` opens with no console errors
- [ ] World renders with all zones visible and labeled
- [ ] All 12 agents appear as distinct pixel sprites with names
- [ ] Agents move between zones based on LLM decisions
- [ ] Speech bubbles appear when agents talk
- [ ] Resource counts update visibly on sprites
- [ ] Activity log shows last 10 agent actions
- [ ] LM Studio connection status shows correctly
- [ ] All 12 agents run independently, never blocking each other
- [ ] Simulation runs 10+ minutes without crashing

---

*End of master plan. Proceed to specs/00-overview.md.*
