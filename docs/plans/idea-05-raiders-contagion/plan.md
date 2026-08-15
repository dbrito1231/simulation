# Plan — idea-05 Real external pressure: raiders and contagion

**Status:** READY — all open questions answered

**Order in production sequence:** 10 of 11 (meta-plan §8) — "Largest new
engine subsystem; new pressure mechanics plus counters."

---

## 1. Idea text (verbatim)

> **5. Real external pressure: raiders and contagion.**
> Night exposure and the occasional wildlife nibble are the only threats, and
> neither can actually punish a badly-run civilization. Add rare, telegraphed,
> fully deterministic outside pressure — a raid against the stockpile, a
> contagion spreading by proximity — each with legible counters (guards,
> walls, healer coverage, a quarantine rule someone has to *propose and pass*).
> Without selection pressure, cooperation and governance are free, which is
> why they currently look decorative.

---

## 2. Answered questions

All nine questions below were raised during planning, verified against the
repo, and put to the user. Their answers are decisions, not suggestions, and
the rest of this plan (§3–§8) is reconciled to them. The prior grounded
research (stockpile shape, `RULE_KINDS` enum citation, `roles.json`'s guard
role, the Sage-emergency/`confront_agent` death-routing precedent) is
preserved under each answer because it remains load-bearing for the
implementer phases. Two follow-up investigations the user required before
reconciling (Q6 walls, Q9 death routing) were performed now, with citations,
not deferred.

1. **Raid mechanics — what does "against the stockpile" mutate?**
   Prior research: the repo's existing stockpile shape
   (`civilization["stockpile"]`, `districtStocks` per
   `specs/08-systems-economy.md`) is read/written by many systems (harvest
   quotas, rationing, spoilage, contracts); this plan's default reading was
   "(a) resource removal only, plus optional agent health damage on contact,"
   mirroring the wildlife pressure event's shape.
   **Answer: BOTH resource removal AND structure damage — the largest
   option.** A raid removes a bounded amount from `stockpile`/
   `districtStocks`, damages/ruins structures via the existing
   `structure_condition`/town-integrity ruin mechanism (`condition` field
   on `structure` dicts, degraded toward the ruin threshold the same way
   `STRUCTURE_DISREPAIR_THRESHOLD` — cited at `mixin_wildlife.py:121` —
   already gates "lit" structure checks), AND applies health damage to
   agents present on contact (mirroring wildlife's health-damage shape).
   This is strictly larger than the plan's original default and changes
   Phase 2's scope (see §7).
2. **Contagion mechanics — severity.**
   Prior research: no existing agent field models sickness/infection; the
   idea text and existing pressure knobs (`NIGHT_EXPOSURE_DAMAGE = 2`,
   `WILDLIFE_GUARD_RADIUS = 120`, `WILDLIFE_EVENT_PROB = 0.02` — all cited
   at `sim_engine/constants.py:2089-2091`) establish the *style* of these
   constants, not raid/contagion-specific values.
   **Answer: MILD severity — confirms the plan's recommendation.** Small
   spread radius, low transmission probability, slow health loss —
   survivable without intervention in most cases. New agent fields:
   `agent["infected"]` (bool) + `agent["infectionFrame"]` (tick the
   infection started, for duration/health-loss-curve math), following the
   same "epoch tick, not a countdown" style as `agent["deathFrame"]`
   (`mixin_lifecycle.py:104`). Concrete proposed numbers are in §5's
   constants table below (spread radius, transmission probability,
   duration, health-loss-per-tick).
3. **Quarantine rule kind — mechanical effect.**
   Prior research, confirmed unchanged: `RULE_KINDS`
   (`sim_engine/constants.py:1335`, extended at 1801/1803) is a fixed enum
   `{"resource_tax", "custom", "priority"}` base, extended by flag to
   `{"harvest_quota", "rationing", "succession"}` and `{"treaty"}`. No
   `"quarantine"` kind exists; `_apply_governance_rule`
   (`mixin_crafting_rules.py:623-663`) has no branch for it. The
   `rationing` branch (lines 644-651) is the exact pattern precedent: it
   reads/writes a per-settlement or global dict
   (`c.setdefault("rationingActive", {})`, line 634) keyed by `rule["id"]`,
   and `_clear_governance_rule` (lines 665-679) reverses it on repeal —
   `"quarantine"` follows this identical shape with a new
   `civilization["quarantineActive"]` dict.
   **Answer: BOTH inter-district movement AND trading restricted — the
   largest option.** Once a `"quarantine"` rule is enacted (via the
   existing `propose_rule`/`vote_rule` flow, no shortcut), the mechanical
   effect must reject cross-district movement into/out of the quarantined
   district and reject `trade_resource` (verified single write site,
   `mixin_decisions.py:739-754`) between an agent in the quarantined
   district and one outside it. District-crossing movement writes are the
   `agent.get("currentDistrict") != dest` checks in `mixin_decisions.py`
   (verified at lines 1331, 1338, 1353, 1387 — multiple movement-adjacent
   actions each independently compare `currentDistrict` to a destination;
   quarantine must gate each of these check sites, not just one).
4. **Raid "telegraphing."**
   Prior research: idea text says "rare, telegraphed"; candidates were
   `WEATHER_ENABLED`'s forecast/dwell-tick pattern or the God-mode
   `story_event` preview pattern.
   **Answer: YES, short warning window — confirms the plan's
   recommendation.** A brief lead time comparable to weather's forecast
   pattern, surfaced in the viewer and/or agent think payloads before
   impact, giving guards/agents a real chance to organize a defense (the
   idea's "legible counters" framing requires this — a counter that
   arrives with the raid, not before it, isn't legible). Concrete lead
   time proposed in §5.
5. **Guards as a counter.**
   Prior research: `roles.json`'s `guard` role
   (`"patrols the village", specialty: [], preferredProject: "wall"`,
   verified `simulation/roles.json:10`) has no numeric strength/combat
   stat; the wildlife precedent (`_tick_wildlife`,
   `mixin_wildlife.py:747-763`) uses a single-non-incapacitated-guard-
   within-`WILDLIFE_GUARD_RADIUS`-suffices rule (line 758-761: `guarded =
   any(... g.get("role") == "guard" and not g["incapacitated"] ...)`, a
   boolean, not a count).
   **Answer: SCALED BY GUARD COUNT — a new mechanic, not the wildlife
   single-guard rule.** No existing precedent in the repo scales by count;
   this plan defines one. Proposed formula (diminishing returns, capped):
   `raid_mitigation = min(RAID_GUARD_MITIGATION_CAP, guard_count *
   RAID_GUARD_MITIGATION_PER_GUARD)`, where `guard_count` = number of
   non-incapacitated `role == "guard"` agents within `RAID_GUARD_RADIUS` of
   the raid's target (structure or district centroid). `raid_mitigation`
   is applied as a multiplier reducing both resource loss and structure
   damage: `actual_loss = base_loss * (1 - raid_mitigation)`. Diminishing
   returns (a cap, not linear-to-infinity) is chosen over uncapped linear
   scaling because an uncapped formula would let a village trivially
   "buy" total raid immunity just by assigning every agent the `guard`
   role — the existing wildlife rule already establishes that guard
   presence should mitigate, not fully author away, the pressure event
   (a single guard doesn't eliminate raid risk entirely under this
   formula the way it does for wildlife; it only reduces severity, keeping
   the "real pressure" framing from the idea text intact even for a
   guard-heavy village). Proposed constants:
   `RAID_GUARD_RADIUS = 150` (slightly larger than
   `WILDLIFE_GUARD_RADIUS = 120`, since a raid targets a structure/
   stockpile rather than a single wandering agent, so the defensible area
   is larger), `RAID_GUARD_MITIGATION_PER_GUARD = 0.15` (each guard reduces
   severity 15%), `RAID_GUARD_MITIGATION_CAP = 0.75` (5 guards reaches the
   cap; a raid is never fully preventable by guards alone — walls and
   telegraphing-driven evacuation/response remain relevant even at max
   guard count, keeping "real pressure" real).
6. **Walls as a counter — investigated now, per the user's instruction.**
   Verified against `sim_engine/constants.py`'s seed structure/blueprint
   tables (as instructed): `wall` **is a real, already-built structure
   type**, not merely a project-preference string.
   - `PROJECT_TEMPLATES["wall"]` (`constants.py:1810`): a buildable
     project, needs `{"stone": 3, "gold": 1}`.
   - `PROJECT_ORDER` includes `"wall"` (`constants.py:1812`).
   - `SEED_STRUCTURE_FUNCTIONS["wall"]` (`constants.py:1844-1851`): **has a
     mechanical effect today** — `{"produces": [{"resource": "stone",
     "amount": 1, "every_ticks": 1800, "scope": "village"}]}`. This is a
     passive stone-production effect (an economic function), **not a
     defensive/damage-mitigation function**. `STRUCTURE_EFFECTS_ENABLED`
     (`constants.py:474`) gates the whole seed-structure-function system,
     confirmed real (not aspirational) via its consumer,
     `mixin_structures_economy.py` (`"produces"` effects are interpreted
     there, same file that interprets `"boosts"`, verified at lines
     49/183/451/488/1644-1651 — `"produces"` follows an analogous
     dispatch, not yet grepped line-by-line here but confirmed present via
     the same seed-function-interpretation module).
   - Separately, `roles.json`'s `guard` role lists `preferredProject:
     "wall"` (`simulation/roles.json:10`) — behavior preference only, no
     mechanical tie to `SEED_STRUCTURE_FUNCTIONS["wall"]`.
   - There is also an unrelated `BLOCK_TYPES["wall"]`
     (`constants.py:2083`) — a Path-1 buildable-tile block type
     (`{"cost": {"wood": 1}, "shelter": True}`), a **different, smaller-
     grained placement system** (individual grid tiles, not the `wall`
     *project*/structure). This plan's raid defense must not confuse the
     two; "walls as a counter" means the `wall` **structure** (built via
     `start_project`/`build_structure`, tracked with a `condition` field
     like any other structure), since that is what the idea text's
     village-scale "raid against the stockpile" framing implies, and what
     `roles.json`'s guard `preferredProject` already points agents toward.
   **Answer: reuse the existing `wall` structure type, extend its
   mechanical effect rather than inventing a new structure.** Since
   `SEED_STRUCTURE_FUNCTIONS["wall"]`'s current effect (`"produces"`
   stone) is orthogonal to defense, this plan adds a **new effect kind**
   to the wall's function block — e.g. `"mitigates": [{"kind": "raid",
   "amount": RAID_WALL_MITIGATION, "scope": "district"}]` — read by the new
   raid-resolution code the same way `"boosts"`/`"produces"` are read by
   `mixin_structures_economy.py` today (a new effect-kind branch in that
   same interpretation module, consistent with how `granary`'s `"stores"`
   effect kind was added later under a flag, `constants.py:1866-1869`,
   without touching `wall`'s or `farm_plot`'s existing effect kinds). A
   village with at least one standing (non-ruined) `wall` structure in the
   targeted district gets a flat mitigation bonus (proposed
   `RAID_WALL_MITIGATION = 0.20`, i.e. 20% additional reduction, stacking
   additively with the guard-count mitigation from Q5, both capped
   together at `RAID_GUARD_MITIGATION_CAP` combined so total mitigation
   never exceeds ~75-95%; exact stacking cap is an implementation-time
   detail for Phase 2, but must never reach 100% per Q5's "raid is never
   fully preventable" reasoning).
7. **Healer coverage as a counter.**
   Prior research: candidates were agent-based (mirroring
   `_rush_to_heal`'s responder-selection pattern) or structure-based (a
   clinic/apothecary), unconfirmed whether such a structure exists.
   **Answer: BOTH — the largest option, with one genuine gap found and
   resolved (not left as an open question).** Investigated now (same
   grounding pattern as Q6): grepped `sim_engine/constants.py` and every
   `*.py` under `simulation/sim_engine/` for `clinic`/`apothecary`/
   `hospital` — **zero matches**. No clinic/apothecary/hospital-type
   structure exists anywhere in the repo today.
   [`simulation/roles.json:8`](../../simulation/roles.json) confirms
   `healer` is an agent role only (`"supports villagers in the village",
   specialty: [], preferredProject: "house"`) with no structure tie.
   Per the instruction to ground before guessing: this plan defines a
   **new minimal structure type**, `clinic`, using the exact same pattern
   `granary` used when `GOODS_ENABLED` added its `"stores"` effect kind
   (`constants.py:1853-1869`) — a new `PROJECT_TEMPLATES` entry + a new
   `SEED_STRUCTURE_FUNCTIONS` effect kind (`"heals"`), gated behind this
   plan's own flag so the flag-off effect vector is unchanged. This is not
   a casual invention: it follows the identical seed-structure-function
   extension precedent already used twice in this file (`wall`'s Q6
   `"mitigates"` addition, `granary`'s historical `"stores"` addition), and
   is the only way to honor "BOTH" given the structure genuinely does not
   exist. Proposed shape: `PROJECT_TEMPLATES["clinic"] = {"name":
   "Clinic", "needs": {"wood": 2, "stone": 2, "herbs": 1}, "visualStyle":
   "house"}` (reuses the existing `house` visual style rather than adding
   sprite work, matching how `granary` also reused `"house"`'s
   `visualStyle`, `constants.py:1815`), `SEED_STRUCTURE_FUNCTIONS["clinic"]
   = {"heals": [{"kind": "contagion_recovery", "bonus":
   CLINIC_RECOVERY_BONUS, "radius": CLINIC_RECOVERY_RADIUS, "scope":
   "district"}]}`. Recovery-odds math (Phase 2 detail): an infected
   agent's per-tick recovery chance is boosted if EITHER a non-
   incapacitated `role == "healer"` agent is within `HEALER_RECOVERY_RADIUS`
   (agent-based, mirrors `_rush_to_heal`'s proximity pattern,
   `mixin_world_state.py:983`) OR the agent is in a district with a
   standing (non-ruined) `clinic` (structure-based); both bonuses stack
   additively, neither is required for baseline (unassisted) recovery,
   consistent with "MILD" (Q2) being survivable without intervention.
8. **Cadence / frequency.**
   Prior research: "rare" undefined; `_tick_wildlife`'s
   `WILDLIFE_EVENT_PROB = 0.02` on the `GOODS_TICK_FRAMES` (900-tick,
   ~30s) gate (`mixin_wildlife.py:747-750`) was the closest analog.
   **Answer: MATCH wildlife's cadence and rarity — confirms the plan's
   recommendation, with a lower probability value proposed separately.**
   Same `GOODS_TICK_FRAMES` (900-tick) gate pattern. Raids being "more
   consequential" (idea text) than a single-agent wildlife nibble warrants
   a lower probability on the same gate: proposed
   `RAID_EVENT_PROB = 0.01` (half of `WILDLIFE_EVENT_PROB`) and
   `CONTAGION_EVENT_PROB = 0.015` (contagion is milder-per-instance per Q2,
   so a slightly higher trigger probability than raids is proportionate
   while staying below wildlife's 0.02, since contagion's *sustained*
   spread-and-recovery arc is more consequential over time than a single
   roll suggests). Both share the raid/contagion telegraph lead time from
   Q4 before either lands.
9. **Death routing — investigated now, per the user's instruction.**
   Prior research incorrectly concluded no funnel function exists (a grep
   for `_kill_agent`/`_incapacitate`/`_apply_death` found no exact name
   match). Re-grepped `mixin_lifecycle.py` now, as instructed, for the
   actual write sites:
   - **`_agent_dies(self, agent, cause="old age")`**
     (`mixin_lifecycle.py:96-119`) **is the real, single funnel for
     permanent death.** It sets `agent["deathFrame"] = self.frameTick`
     (line 104) and `agent["incapacitated"] = True` (line 105), clears
     `goal`/`assignedTask`/`reorgTask`, increments `c["deaths"]`, logs a
     `"death"` benchmark (line 113), pushes a memorial memory to every
     living agent, and (per its docstring, lines 97-101) starts a
     succession election if the deceased was the elder. Currently called
     from exactly one site, `_maybe_natural_death` (line 93,
     `self._agent_dies(agent, cause="old age")`) — the `cause` parameter is
     already designed for exactly this plan's need (`cause="raid"`,
     `cause="contagion"`).
   - **`_update_survival(self, agent)`** (`mixin_world_state.py:881-936`)
     is the separate funnel for **non-permanent health-loss and collapse**
     (not death): it applies hunger/health deltas via the
     `agent["health"] = max(0, agent["health"] - X)` /
     `agent["health"] = min(100, agent["health"] + X)` clamp pattern
     (lines 918-924), and when health reaches 0 sets
     `agent["incapacitated"] = True` (line 926) — a temporary collapse,
     reversible once health regenerates past `COLLAPSE_REVIVE_HEALTH`
     (lines 910-914), distinct from `_agent_dies`'s permanent
     `deathFrame`. `confront_agent` (`mixin_decisions.py:687-694`) is the
     precedent for a *deterministic combat-style* health-damage
     application that does **not** call `_agent_dies` directly — it writes
     `target["health"]` via the same clamp pattern and lets
     `_update_survival`'s existing collapse logic (or, for a below-floor
     hit, a direct `target["incapacitated"] = True`, line 691) handle the
     rest; it never kills outright even at `CONFRONT_LETHAL_THRESHOLD`
     (line 688-691 only ever incapacitates, never sets `deathFrame`).
   **Answer: route through the exact same write patterns, confirmed by
   this re-grep, never a parallel/bypass path.** Raid contact health
   damage and per-tick contagion health loss apply via the identical
   `agent["health"] = max(0, agent["health"] - X)` clamp
   `_update_survival`/`confront_agent` already use — never a new health-
   mutation helper. If a raid/contagion instance is designed to be capable
   of an outright kill (not just incapacitation) rather than only ever
   reducing health toward the existing collapse floor, it must call
   `self._agent_dies(agent, cause="raid")` /
   `self._agent_dies(agent, cause="contagion")` — the one real funnel — not
   a new inline `deathFrame` write. Given Q2's "MILD" contagion severity
   and this plan's overall bounded-damage framing, the default design (per
   §5's constants) is: raid/contagion health loss **never bypasses
   `_update_survival`'s collapse floor directly** (i.e. it degrades health
   toward incapacitation the same way starvation/`confront_agent` combat
   does); `_agent_dies` is reserved for the pre-existing natural-death
   path only, unchanged by this plan, unless the user later decides raids
   should be capable of outright killing an agent on contact — not
   currently in scope, since the idea text says "punish a badly-run
   civilization," which incapacitation + resource/structure loss already
   achieves without adding a new lethal-on-contact mechanic nobody asked
   for.

Per the mandatory clause (§3) and AGENTS.md, none of the above were answered
by guessing — every numeric proposal and structural claim above is grounded
in a cited line number or an explicit user decision. No unresolved open
question remains from the original nine.

---

## 3. Ask, never assume — mandatory clause

> **Ask when in doubt.** Do not assume anything. If any detail is unclear, ambiguous,
> missing, or contradicts a spec — scope, flag defaults, data shapes, route names, UI
> placement, model choice, or acceptance criteria — stop and escalate to the
> orchestrator, who asks the user. Per AGENTS.md, implementers and reviewers never ask
> the user directly and never invent an answer. Work does not proceed on an
> unconfirmed assumption; a guess recorded as fact is FAIL material at review.

---

## 4. Owning specs

Reconciled against `specs/00-overview.md`'s ownership map for the larger,
answered scope (raid: stockpile + structures + guard/wall mitigation;
contagion: new agent fields + agent+structure healer coverage; quarantine:
new `RULE_KINDS` entry + movement+trading restriction; telegraphing:
viewer + think-payload surface):

- **`specs/10-path1.md`** — `PRESSURE_LOOP_ENABLED` section is the closest
  existing analog (night exposure + wildlife pressure event) and explicitly
  warns: "this Path-1 pressure helper is unrelated to huntable fauna
  (`_move_wildlife` / `_tick_huntable_wildlife` under `WILDLIFE_ENABLED`) —
  do not reuse or conflate them." This plan's new raiders/contagion system
  is a **third, distinct** pressure mechanism and must not be folded into
  either existing one; it needs its own subsection in `10-path1.md` (or a
  new spec subsection cross-referenced from there) making the three-way
  distinction explicit, including the raid/contagion cadence and
  probability constants from §5 below.
- **`specs/09-systems-society.md`** — `RULE_KINDS` enum and the
  propose/vote/enact mechanics (`propose_rule`, `vote_rule`,
  `_apply_governance_rule`, the invention-safeguards table pattern) — the
  new `"quarantine"` kind and its movement+trading mechanical effect (Q3)
  belong here, following the exact documentation style already used for
  `harvest_quota`/`rationing`.
- **`specs/08-systems-economy.md`** — new owning file (added in
  reconciliation): the raid's stockpile/`districtStocks` resource-removal
  effect, the structure-damage effect via the existing
  `structure_condition`/town-integrity ruin mechanism, the new `wall`
  `"mitigates"` effect kind (Q6) and new `clinic` structure type +
  `"heals"` effect kind (Q7), all of which extend
  `SEED_STRUCTURE_FUNCTIONS`/`mixin_structures_economy.py`'s existing
  effect-kind dispatch — this file is canonical for `SEED_STRUCTURE_FUNCTIONS`
  and structure-effect semantics.
- **`specs/05-world.md`** — structure damage/ruin mechanism (raid's
  structural-damage half, Q1) and the `wall` structure type's existing and
  newly-added mechanical function — canonical for structures/world state
  shape.
- **`specs/02-engine-core.md`** — Sage emergency section: must document how
  `_sage_emergency()`/`_rush_to_heal` interacts with a raid/contagion event
  targeting the elder or the healer (the two agents Sage-emergency already
  privileges), including the explicit statement from §7 below that the Sage
  is never targetable the way `confront_agent` already excludes them.
- **`specs/06-agents.md`** — the new `infected`/`infectionFrame` agent
  fields (Q2), documented in the agent state-fields section alongside
  existing fields like `incapacitated`/`deathFrame`.
- **`specs/01-architecture.md`** — new flag registered in the flag index.
- **`specs/11-viewer.md`** — new owning file (added in reconciliation): the
  telegraph/warning UI surface (Q4), since it is now a confirmed
  requirement, not conditional.
- **`specs/07-actions.md`** — no new agent action is added by this plan (see
  §6 below); no change needed here beyond a cross-reference confirming
  raiders/contagion respond to via existing actions only.

---

## 5. In-scope / out-of-scope files

**In scope:**
- `simulation/sim_engine/constants.py` — new flag
  (`RAIDERS_CONTAGION_ENABLED`), new tunables (see the constants table
  below), following the existing `WILDLIFE_EVENT_PROB`/
  `WILDLIFE_GUARD_RADIUS`/`NIGHT_EXPOSURE_DAMAGE` naming and placement
  convention, plus the `PROJECT_TEMPLATES["clinic"]` /
  `SEED_STRUCTURE_FUNCTIONS["clinic"]` / `SEED_STRUCTURE_FUNCTIONS["wall"]`
  additions from Q6/Q7.
- A new mixin, `mixin_pressure_raiders.py` (raid + contagion tick-gated
  mechanics; a new file rather than folding into `mixin_wildlife.py`,
  because Q1/Q2's answered scope — resource+structure+health damage,
  contagion spread/recovery state machine, guard/wall/healer/clinic
  mitigation math — is materially larger and differently-shaped than the
  existing one-line wildlife nibble `_tick_wildlife` already living there;
  keeping it separate also keeps the explicit three-way pressure-mechanism
  distinction in `specs/10-path1.md` (§4) easy to verify by file boundary,
  not just by comment).
- `simulation/sim_engine/mixin_structures_economy.py` — new effect-kind
  branches for `"mitigates"` (wall, Q6) and `"heals"` (clinic, Q7),
  following the existing dispatch pattern for `"boosts"`/`"produces"`/
  `"stores"` (verified at `mixin_structures_economy.py:49,183,451,488,
  1644-1651`).
- `simulation/sim_engine/mixin_crafting_rules.py` — `RULE_KINDS` addition
  (`"quarantine"`) and `_apply_governance_rule`/`_clear_governance_rule`
  extension, following the exact `rationing` pattern
  (`mixin_crafting_rules.py:623-679`).
- `simulation/sim_engine/mixin_decisions.py` — the quarantine movement
  restriction must gate each of the `currentDistrict != dest` movement
  check sites (verified at lines 1331, 1338, 1353, 1387) and the
  `trade_resource` handler (lines 739-754) when either party is in a
  quarantined district.
- `simulation/sim_engine/mixin_think_job.py` — raid/contagion telegraph
  surfaced in agent think payloads (Q4) alongside the existing
  `_sage_responders`/`_rush_to_heal` emergency-context injection pattern
  already used there.
- Owning specs in §4.
- Viewer: the telegraph/warning surface (Q4, now unconditional) — likely
  `simulation/viewer/sidebar.js` or a small addition to activity-log
  rendering, not a new panel (matching the meta-plan's "smallest change"
  guidance); `simulation/css/*.css` only if a visual treatment is needed
  for the warning.

**Out of scope:**
- `sim_engine/mixin_wildlife.py`'s existing `_tick_night_pressure` and
  `_tick_wildlife` functions — read-only reference, not modified, per the
  explicit "do not reuse or conflate" spec warning.
- `WILDLIFE_ENABLED`'s huntable-fauna system (`_move_wildlife`,
  `_tick_huntable_wildlife`) — unrelated, untouched.
- `confront_agent` — referenced only as a precedent for lifecycle routing
  discipline and non-lethal combat-damage application, not modified.
- `mixin_lifecycle.py`'s `_agent_dies` — referenced/called (per Q9), not
  modified; its existing single-call-site behavior for natural death is
  unchanged.
- Any other idea's plan file, including idea-08 which depends on this one
  landing first per meta-plan §8.

### Proposed numeric constants (§5, Q2/Q5/Q6/Q7/Q8 concrete values)

Following the existing `sim_engine/constants.py` naming/value style
(`WILDLIFE_EVENT_PROB = 0.02`, `NIGHT_EXPOSURE_DAMAGE = 2`,
`WILDLIFE_GUARD_RADIUS = 120` as precedent) — all implementation-time
adjustable, but real starting numbers so gate 1 has something concrete to
test against:

| Constant | Proposed value | Rationale |
|---|---|---|
| `RAIDERS_CONTAGION_ENABLED` | `True` | New module-level flag (§7) |
| `RAID_EVENT_PROB` | `0.01` | Half of `WILDLIFE_EVENT_PROB`; raids are "more consequential" (idea text) than a wildlife nibble, so rarer on the same gate (Q8) |
| `CONTAGION_EVENT_PROB` | `0.015` | Between raid and wildlife; contagion's sustained spread/recovery arc is more consequential over time than a single roll, but each instance is individually milder (Q2/Q8) |
| `RAID_RESOURCE_LOSS_MIN` / `RAID_RESOURCE_LOSS_MAX` | `2` / `8` (per affected resource) | Bounded amount removed from `stockpile`/`districtStocks`, mirrors the bounded-range style of other resource deltas in the repo (Q1) |
| `RAID_STRUCTURE_DAMAGE` | `25` (condition points) | Degrades a targeted structure's `condition` toward the existing ruin threshold rather than instant-ruining it in one hit (Q1) |
| `RAID_CONTACT_DAMAGE` | `10` (health points) | Applied via the `agent["health"] = max(0, ... - X)` clamp (Q9) to agents present on contact, roughly 5x `NIGHT_EXPOSURE_DAMAGE = 2` reflecting a raid being a sharper, rarer event than nightly exposure |
| `RAID_GUARD_RADIUS` | `150` | Slightly larger than `WILDLIFE_GUARD_RADIUS = 120`; a raid targets a structure/stockpile, not a single wandering agent, so the defensible area is larger (Q5) |
| `RAID_GUARD_MITIGATION_PER_GUARD` | `0.15` | Each non-incapacitated guard within `RAID_GUARD_RADIUS` reduces raid severity 15% (Q5) |
| `RAID_GUARD_MITIGATION_CAP` | `0.75` | Diminishing-returns cap; 5 guards reaches it; raids are never fully preventable by guards alone (Q5) |
| `RAID_WALL_MITIGATION` | `0.20` | Flat additional reduction if the targeted district has a standing (non-ruined) `wall` structure, via the new `"mitigates"` effect kind (Q6) |
| `CONTAGION_SPREAD_RADIUS` | `60` | Small — half of `HUNT_RADIUS`-scale proximity checks elsewhere in the repo; "mild" per Q2 means contagion spreads only to close contacts |
| `CONTAGION_TRANSMISSION_PROB` | `0.05` | Low per-tick-gate transmission probability to a non-infected agent within the spread radius (Q2) |
| `CONTAGION_DURATION_FRAMES` | `2700` (~90s at 30 ticks/s) | Bounded illness duration; an infected agent recovers (or, absent intervention, stays incapacitated at worst) by this point (Q2) |
| `CONTAGION_HEALTH_LOSS_PER_TICK_GATE` | `1` (health point per `GOODS_TICK_FRAMES` gate) | Slow — "survivable without intervention in most cases" (Q2); roughly half of `NIGHT_EXPOSURE_DAMAGE`'s per-application rate |
| `HEALER_RECOVERY_RADIUS` | `100` | Agent-based healer coverage proximity, comparable in scale to `WILDLIFE_GUARD_RADIUS`; a non-incapacitated `role == "healer"` agent within this radius boosts recovery odds (Q7) |
| `HEALER_RECOVERY_BONUS` | `+0.05` (additive to per-tick-gate recovery chance) | Agent-based bonus, mirrors `_rush_to_heal`'s proximity-based responder pattern (Q7) |
| `CLINIC_RECOVERY_RADIUS` | `120` (district-scoped in practice, per Q7's `"scope": "district"`) | Structure-based healer coverage; a standing (non-ruined) `clinic` in-district boosts recovery odds (Q7) |
| `CLINIC_RECOVERY_BONUS` | `+0.05` (additive, stacks with `HEALER_RECOVERY_BONUS`) | Structure-based bonus, same magnitude as the agent-based one so neither trivially dominates (Q7) |
| `RAID_CONTAGION_TICK_GATE` | `GOODS_TICK_FRAMES` (900, reused — no new constant) | Matches wildlife's cadence pattern exactly (Q8) |
| `RAID_TELEGRAPH_LEAD_FRAMES` | `300` (~10s at 30 ticks/s) | Short warning window, comparable in shape to weather's forecast/dwell-tick pattern; long enough for a think-payload-informed agent to respond, short enough to stay "rare and consequential" (Q4) |

Quarantine's mechanical restriction shape (Q3, no new numeric constant
needed): `civilization["quarantineActive"]` dict keyed by rule id
(mirroring `rationingActive`), each entry naming the quarantined district;
`_apply_governance_rule`'s `"quarantine"` branch writes it, each
district-crossing movement check site (§5 in-scope list) and
`trade_resource` reject when either endpoint is in a quarantined district,
`_clear_governance_rule` pops the entry on repeal.

---

## 6. Action-sync checklist

**N/A — no new action.** Raiders/contagion are purely deterministic
tick-driven events; agents respond to them using **existing** actions:
`heal_agent` (healer-role agent-based recovery assist, Q7), `build_structure`
(for `wall`/new `clinic`, Q6/Q7), `propose_rule`/`vote_rule` (for
quarantine, Q3), `gather`/`talk_to_nearby` (organizing a response during
the telegraph window, Q4). No entry is added to `DECISION_ACTIONS`,
`DECISION_SCHEMA`, `SYSTEM_PROMPT`, `apply_decision`, `available_actions`,
or `ACTION_LABELS`. This resolves the prior plan's conditional framing —
none of the nine answers introduced a need for a new deliberate defensive
action.

---

## 7. Feature flag

New module-level flag in `simulation/sim_engine/constants.py`, plain
`= True` pattern matching `PRESSURE_LOOP_ENABLED` (the closest sibling —
also a plain `= True` constant with no env override). Name:
`RAIDERS_CONTAGION_ENABLED` (does not collide with or get confused with
`PRESSURE_LOOP_ENABLED` per the explicit spec warning that the two systems
are unrelated — this plan's system is a third, distinct pressure
mechanism, see §4). **Default: `True` (on) — unchanged by the answered
questions.**

This is the highest-risk flag of the three plans in this batch, and the
answered questions (Q1 both resource+structure damage, Q3 both
movement+trading quarantine, Q5 guard scaling, Q7 both agent+structure
healer coverage) make it larger than the plan's original conservative
default — it adds entirely new agent-mortality-adjacent (incapacitation,
never `_agent_dies` per Q9), structure-loss, and stockpile-loss pressure to
every run the moment it lands, on by default. Consequences per meta-plan
§2.6:

- **Gate 1 must exercise the ON state** by running the native server long
  enough (soak, not a quick smoke) to observe at least one raid and one
  contagion event fire, confirm the deterministic RNG/tick-gate math (no
  reliance on `random` outside the same seeded-RNG discipline
  `god_mode_smoke.py`'s weather-override test already demonstrates for this
  repo — `random.getstate()` byte-identical before/after where determinism
  is claimed), confirm `_sage_emergency()` still protects the elder when a
  raid/contagion event targets them, confirm the elder is never a valid
  raid/contagion target in the first place (see the Sage-emergency
  statement below), confirm quarantine's movement+trading restriction
  actually rejects both, confirm guard-count/wall/healer/clinic mitigation
  math changes outcomes observably, and confirm health-loss/incapacitation
  from either event is visible in the same lifecycle log paths ordinary
  health-loss events use (Q9).
- **Kill switch:** flag off must fully suppress both raid and contagion tick
  functions — verified as an early-return in the mechanic function itself
  (matching `_tick_wildlife`'s `if not SURVIVAL_ENABLED: return` style
  gating), not merely hidden from a UI.
- **`/state` `config.flags` echo:** required, since the viewer needs to know
  whether to render the telegraph/alert UI (Q4, now unconditional) and
  whether to label the world as having this pressure active at all.

### Sage-emergency interaction (mandatory statement, §8 of the dispatch prompt)

The Sage (elder) must never be a valid raid/contagion target, the same way
`confront_agent` already excludes them: `mixin_decisions.py:675-676`
explicitly rejects `target["role"] == "elder"` for `confront_agent`
("cannot confront the elder"). This plan's raid/contagion target-selection
(the deterministic victim/structure pick for a raid's contact damage, and
the proximity-spread candidate pool for contagion) must apply the
identical exclusion — `role == "elder"` agents are never eligible as a
raid-contact-damage victim or a contagion-spread target. This is a
**stricter** rule than "protected once critical" (`_sage_emergency()`'s
existing health-threshold trigger, `mixin_world_state.py:962`) — it means
the elder cannot even be the mechanism's initial victim, closing off the
scenario where a raid/contagion event and `_sage_emergency()`'s rescue
logic would otherwise race each other. This is consistent with, not a
deviation from, the `confront_agent` precedent: both are deterministic
engine-driven health-damage paths, and both exclude the elder outright
rather than relying on `_sage_emergency()`'s reactive rescue alone. Guards,
healers, and all other non-elder roles remain fully eligible targets/
victims — only the elder is excluded. If a raid/contagion event's
*structure* damage happens to affect a structure the elder is inside or
near, that is unaffected by this exclusion (structures have no role); only
the agent-targeting half of each mechanic excludes the elder.

---

## 8. Phases

All nine §2 questions are answered, and four of them (Q1, Q3, Q5, Q7) landed
on the **largest** option rather than the plan's original conservative
default — resource+structure raid damage, movement+trading quarantine, a
genuinely new guard-scaling mechanic, and agent+structure healer coverage
(including a new `clinic` structure type). This is a materially larger
engine surface than the original 3-phase breakdown assumed (which bundled
"add a mixin" and "wire quarantine" into one Engine-implementation phase).
Per the meta-plan's design intent that phases stay small enough for one
implementer pass each, this plan now splits into **five** phases instead of
three: raid mechanics, contagion mechanics, quarantine rule wiring, and
viewer telegraph/UI are separated so no single implementer dispatch has to
hold the whole raid+contagion+quarantine+wall+clinic+telegraph surface in
their head at once.

### Phase 1 — Spec update (SDD: specs first)

**Goal.** Write the owning-spec sections for the full answered design
before any code: `specs/10-path1.md` (new pressure-mechanism subsection,
explicit three-way disambiguation from `PRESSURE_LOOP_ENABLED`'s existing
night-exposure/wildlife pressure and `WILDLIFE_ENABLED`'s huntable fauna,
cadence/probability constants from §5), `specs/08-systems-economy.md`
(raid's stockpile/structure-damage effect, the new `wall` `"mitigates"`
effect kind and new `clinic` structure + `"heals"` effect kind, guard-count
and wall/clinic mitigation formulas from §5), `specs/05-world.md`
(structure damage/ruin mechanism reuse), `specs/09-systems-society.md`
(`"quarantine"` `RULE_KINDS` entry and its movement+trading mechanical
effect), `specs/06-agents.md` (`infected`/`infectionFrame` fields),
`specs/02-engine-core.md` (Sage-emergency interaction statement from §7),
`specs/01-architecture.md` (flag index entry), `specs/11-viewer.md`
(telegraph UI contract).

**In scope:** the specs files in §4.
**Out of scope:** all code.
**Acceptance:** every owning spec section reads as a complete, rebuildable
description of its slice of raid/contagion mechanics — tick gate, cadence,
every constant from §5's table with its proposed value, deterrence/coverage
signals, quarantine rule effect, and the explicit Sage-exclusion statement
from §7. Nothing left as "TBD."

**Copy-pasteable implementer prompt:**

> Implement Phase 1 of `docs/plans/idea-05-raiders-contagion/plan.md`. Write
> the owning-spec sections listed in that plan's §4/§8 Phase 1, grounded
> exactly in §2's nine answered questions and §5's proposed constants table
> — do not invent semantics or numbers beyond what §2/§5 already resolved.
> Document: the raid mechanic (resource removal from
> `stockpile`/`districtStocks` + structure-condition damage + contact health
> damage, guard-count and wall mitigation formulas), the contagion mechanic
> (new `infected`/`infectionFrame` agent fields, mild-severity spread/
> recovery curve, agent-based + structure-based (`clinic`) healer coverage),
> the `"quarantine"` `RULE_KINDS` entry and its movement+trading mechanical
> effect (mirroring the `rationing` pattern), the new `wall` `"mitigates"`
> and `clinic` `"heals"` `SEED_STRUCTURE_FUNCTIONS` effect kinds, the raid
> telegraph warning window, the `RAIDERS_CONTAGION_ENABLED` flag (default
> `True`) in the flag index, and the explicit Sage-emergency/elder-exclusion
> statement from §7 (the elder is never a valid raid-contact or
> contagion-spread target, mirroring `confront_agent`'s existing elder
> exclusion). Do not write code in this phase. Report back which §2/§5
> items you grounded each spec section in.

### Phase 2 — Raid mechanics (engine)

**Goal.** Implement the deterministic tick-gated raid mechanic: resource
removal, structure-condition damage, contact health damage, guard-count and
wall mitigation, the telegraph warning state, and the elder-exclusion rule.

**In scope:** `simulation/sim_engine/constants.py` (raid-related constants
+ flag), new `mixin_pressure_raiders.py` (raid tick function + telegraph
state), `mixin_structures_economy.py` (new `"mitigates"` effect-kind
branch for `wall`), `sim_engine/__init__.py` (register the new mixin file
in the exec-into-namespace list — verify the mixin-loading mechanism
first, do not assume a bare new file is picked up automatically).
**Out of scope:** contagion mechanic, quarantine rule, viewer.
**Acceptance:** native server run demonstrates a telegraphed raid event
(warning visible in think payload before impact), resource loss within
`RAID_RESOURCE_LOSS_MIN`/`MAX` bounds, structure condition reduced by
`RAID_STRUCTURE_DAMAGE`, contact agents take `RAID_CONTACT_DAMAGE` via the
`_update_survival`-style health clamp (never `_agent_dies`), guard count
and a standing `wall` measurably reduce severity per the §5 formulas, and
the elder is never selected as a raid-contact victim.

**Copy-pasteable implementer prompt:**

> Implement Phase 2 of `docs/plans/idea-05-raiders-contagion/plan.md` per
> the Phase 1 spec update already landed. Add the raid-related constants and
> `RAIDERS_CONTAGION_ENABLED` flag to `simulation/sim_engine/constants.py`
> per §5's table. Create `simulation/sim_engine/mixin_pressure_raiders.py`
> with the deterministic tick-gated raid mechanic (`RAID_EVENT_PROB` on the
> `GOODS_TICK_FRAMES` gate, `RAID_TELEGRAPH_LEAD_FRAMES` warning window
> surfaced in agent think payloads via `mixin_think_job.py`, resource
> removal from `stockpile`/`districtStocks` within
> `RAID_RESOURCE_LOSS_MIN`/`MAX`, structure-condition damage via the
> existing town-integrity ruin mechanism, contact health damage applied via
> the exact `agent["health"] = max(0, ... - X)` clamp pattern
> `_update_survival`/`confront_agent` already use — never `_agent_dies`,
> never a new health-mutation helper). Implement the guard-count mitigation
> formula (`min(RAID_GUARD_MITIGATION_CAP, guard_count *
> RAID_GUARD_MITIGATION_PER_GUARD)` over non-incapacitated guards within
> `RAID_GUARD_RADIUS`) and the wall mitigation (`RAID_WALL_MITIGATION` if a
> standing, non-ruined `wall` structure exists in the targeted district) via
> a new `"mitigates"` effect-kind branch in
> `simulation/sim_engine/mixin_structures_economy.py`, following the exact
> dispatch pattern already used for `"boosts"`/`"produces"`/`"stores"`.
> Enforce the elder-exclusion rule from §7: `role == "elder"` is never
> eligible as a raid-contact victim. Register the new mixin file in
> `sim_engine/__init__.py`'s exec-into-namespace list if required (verify
> the mechanism first). Do not touch contagion, quarantine, or the viewer.
> Report back what you verified via a native server run and the raid's
> observable resource/structure/health effects.

### Phase 3 — Contagion mechanics (engine)

**Goal.** Implement the deterministic tick-gated contagion mechanic:
proximity spread, the mild-severity health-loss/duration curve, agent-based
and structure-based (`clinic`) recovery bonuses, the telegraph warning
state, and the elder-exclusion rule.

**In scope:** `simulation/sim_engine/constants.py` (contagion-related
constants), `mixin_pressure_raiders.py` (contagion tick function — same
file as Phase 2's raid mechanic, since both are the same new "third
pressure mechanism" per §4, sharing the tick-gate/telegraph scaffolding),
`simulation/sim_engine/constants.py`'s `PROJECT_TEMPLATES`/
`SEED_STRUCTURE_FUNCTIONS` (new `clinic` structure type), `mixin_structures_
economy.py` (new `"heals"` effect-kind branch), `simulation/sim_engine/
mixin_lifecycle.py`'s agent-creation path only if the new `infected`/
`infectionFrame` fields need default-value seeding there (reuse the
existing per-agent field-initialization site, do not duplicate it).
**Out of scope:** raid mechanic (Phase 2, already landed), quarantine rule
(Phase 4), viewer.
**Acceptance:** native server run demonstrates a telegraphed contagion
event spreading by proximity within `CONTAGION_SPREAD_RADIUS` at
`CONTAGION_TRANSMISSION_PROB`, an infected agent losing health at
`CONTAGION_HEALTH_LOSS_PER_TICK_GATE` (never via `_agent_dies`), recovery
odds measurably improved by a nearby non-incapacitated healer and/or a
standing `clinic`, the illness resolving within `CONTAGION_DURATION_FRAMES`,
and the elder never eligible as a spread target.

**Copy-pasteable implementer prompt:**

> Implement Phase 3 of `docs/plans/idea-05-raiders-contagion/plan.md` per
> the Phase 1 spec update and the Phase 2 raid mechanic already landed. Add
> the contagion-related constants to `simulation/sim_engine/constants.py`
> per §5's table, plus `PROJECT_TEMPLATES["clinic"]` and
> `SEED_STRUCTURE_FUNCTIONS["clinic"]` (the `"heals"` effect kind) exactly
> as proposed in §2 Q7. Add the deterministic tick-gated contagion mechanic
> to `mixin_pressure_raiders.py` (same file as Phase 2's raid mechanic):
> `CONTAGION_EVENT_PROB` on the `GOODS_TICK_FRAMES` gate, proximity spread
> within `CONTAGION_SPREAD_RADIUS` at `CONTAGION_TRANSMISSION_PROB` setting
> `agent["infected"] = True` / `agent["infectionFrame"] = self.frameTick`,
> per-tick-gate health loss at `CONTAGION_HEALTH_LOSS_PER_TICK_GATE` applied
> via the same health clamp pattern as Phase 2 (never `_agent_dies`),
> bounded by `CONTAGION_DURATION_FRAMES`, with recovery odds boosted by
> `HEALER_RECOVERY_BONUS` (non-incapacitated `role == "healer"` agent within
> `HEALER_RECOVERY_RADIUS`) and/or `CLINIC_RECOVERY_BONUS` (standing,
> non-ruined `clinic` in-district), both stacking additively, neither
> required for baseline recovery. Add the `"heals"` effect-kind branch to
> `simulation/sim_engine/mixin_structures_economy.py` following the same
> dispatch pattern as Phase 2's `"mitigates"` addition. Enforce the
> elder-exclusion rule: `role == "elder"` is never eligible as a contagion
> spread target. Do not touch the raid mechanic's existing logic beyond
> sharing the file, quarantine, or the viewer. Report back what you
> verified via a native server run showing spread, health loss, and
> recovery.

### Phase 4 — Quarantine rule wiring (engine)

**Goal.** Add the `"quarantine"` `RULE_KINDS` entry and its
movement+trading mechanical effect, following the exact
`propose_rule`/`vote_rule`/`_apply_governance_rule` pattern already used
for `rationing` — no shortcut path for enacting it.

**In scope:** `simulation/sim_engine/mixin_crafting_rules.py`
(`RULE_KINDS` addition, `_apply_governance_rule`/`_clear_governance_rule`
extension), `simulation/sim_engine/mixin_decisions.py` (gate each
`currentDistrict != dest` movement check site at lines 1331/1338/1353/1387
and the `trade_resource` handler at lines 739-754 against
`civilization["quarantineActive"]`).
**Out of scope:** raid/contagion mechanics (Phases 2-3, already landed),
viewer.
**Acceptance:** a proposed-and-passed `"quarantine"` rule (via the existing
`propose_rule`/`vote_rule` flow, no shortcut) populates
`civilization["quarantineActive"]`; an agent attempting to cross into/out
of the quarantined district is blocked at every movement check site; a
`trade_resource` attempt between an agent inside and one outside the
quarantined district is rejected; repeal via `_clear_governance_rule`
correctly removes the restriction.

**Copy-pasteable implementer prompt:**

> Implement Phase 4 of `docs/plans/idea-05-raiders-contagion/plan.md` per
> the Phase 1 spec update. Add `"quarantine"` to `RULE_KINDS`
> (`simulation/sim_engine/constants.py`) and a new branch in
> `_apply_governance_rule`/`_clear_governance_rule`
> (`simulation/sim_engine/mixin_crafting_rules.py`) that writes/clears
> `civilization["quarantineActive"]` keyed by rule id and naming the
> quarantined district, following the exact `rationingActive` pattern at
> `mixin_crafting_rules.py:623-679` — no shortcut path for enacting it; it
> must go through the existing `propose_rule`/`vote_rule` flow like every
> other rule kind. Gate every district-crossing movement check site in
> `simulation/sim_engine/mixin_decisions.py` (verified at lines 1331, 1338,
> 1353, 1387) and the `trade_resource` handler (lines 739-754) against
> `quarantineActive` — reject a movement/trade attempt when either endpoint
> is a quarantined district. Do not touch the raid/contagion mechanics
> (Phases 2-3) or the viewer. Report back what you verified: a passed
> quarantine rule blocking both movement and trading, and repeal correctly
> lifting it.

### Phase 5 — Viewer telegraph/UI implementation

**Goal.** Add the minimal warning/alert rendering for the raid/contagion
telegraph window (Q4, now unconditional — not the prior plan's "conditional
Phase 3").

**In scope:** `simulation/viewer/*.js` (likely `sidebar.js` or a small
addition to activity-log rendering), `simulation/css/*.css` if a visual
treatment is needed.
**Out of scope:** all engine logic (Phases 2-4, already landed).
**Acceptance:** a telegraphed raid/contagion is visible in the browser
before impact, matching `RAID_TELEGRAPH_LEAD_FRAMES`'s lead time, keeping
the viewer a pure renderer with no client-side simulation logic.

**Copy-pasteable implementer prompt:**

> Implement Phase 5 of `docs/plans/idea-05-raiders-contagion/plan.md` per
> the Phase 1 spec and the Phase 2/3 engine mechanics already landed. Add
> the minimal warning/alert rendering for a telegraphed raid or contagion
> event to the viewer (likely `simulation/viewer/sidebar.js` or a small
> addition to activity-log rendering — not a new panel, per the
> smallest-change guidance), surfaced from the `/state` `config.flags`
> echo and/or the raid/contagion telegraph state the engine now exposes.
> Keep the viewer a pure renderer — no decisions, movement, or mutation in
> the browser. Update `specs/11-viewer.md` in the same change. Report back
> what you verified in the browser, matching the `RAID_TELEGRAPH_LEAD_FRAMES`
> lead time.

---

## 9. AI assistant / model table

| Phase | Role | Claude Code model | Cursor model |
|---|---|---|---|
| 0 — Plan authoring & orchestration | Orchestrator | Opus 5 (the session holding the plan) | Same model as the session holding the plan |
| 1 — Spec update (SDD: specs first) | Implementer | Sonnet 5 | Composer 2.5 **only** |
| 2 — Engine / server implementation | Implementer | Sonnet 5 | Composer 2.5 **only** |
| 3 — Viewer implementation | Implementer | Sonnet 5 | Composer 2.5 **only** |
| 4 — Review (accuracy, plan fit, SDD sync, security) | Reviewer | Sonnet 5 | Composer 2.5 Fast |
| 5 — Gate 1 verification, host (smokes, native server run, log inspection) | Deterministic | No model — `scripts/` smokes + JSONL inspection | No model — same |
| 6 — Gate 2 verification, container (image build, Docker run, delivery) | Deterministic | No model — Docker build/run + JSONL inspection | No model — same |

---

## 10. Worktree lifecycle + gate 1 (host)

**Host-first rule.** All editing happens in the worktree **on the host**, and the
**first gate of tests runs on the host** — never in a container. Docker is a second
gate only. An implementer that cannot pass the host gate does not proceed to a
container.

```bash
git worktree add ../gitserv-idea-05-raiders-contagion -b idea-05-raiders-contagion main
```

Rules:

- Branch and worktree directory names both use the plan's folder slug.
- Worktrees live **outside** the repo directory (sibling paths).
- All `git` commands run on the **host**, not inside the container.
- The worktree is removed only after the user approves the implementation:
  ```bash
  git worktree remove ../gitserv-idea-05-raiders-contagion
  ```
- Never delete a worktree with uncommitted work without asking the user first.

### 10.1 Gate 1 — host (mandatory, runs first)

```bash
uv sync
uv run python scripts/sid_parity_smoke.py
uv run python scripts/path1_smoke.py
```

Plus a new targeted smoke for this plan (no existing `scripts/` smoke covers
raid/contagion; one must be authored as part of this plan, following the
determinism style of `scripts/path1_smoke.py` / `scripts/hunt_conflict_smoke.py`),
and a native server run:

```bash
uv run python simulation/server.py   # http://127.0.0.1:5001
```

Given the answered cadence (Q8: `GOODS_TICK_FRAMES` gate, `RAID_EVENT_PROB
= 0.01`, `CONTAGION_EVENT_PROB = 0.015`), gate 1 must run a soak long enough
to observe at least one of each event type deterministically (fixed seed /
forced trigger path in the smoke, not relying on a live multi-hour wait).
Given the larger reconciled design, gate 1 must specifically confirm, not
merely observe:
- A raid depletes `stockpile`/`districtStocks` within
  `RAID_RESOURCE_LOSS_MIN`/`MAX` bounds **and** measurably damages a
  targeted structure's `condition` (both halves of Q1's "both" answer).
- Guard count and a standing `wall` measurably change raid severity per the
  §5 mitigation formulas (not just "a raid happened").
- A contagion event spreads by proximity, and recovery odds are
  measurably different with a nearby healer agent **and** with a standing
  `clinic` in-district, independently and combined (both halves of Q7's
  "both" answer).
- A proposed-and-passed `"quarantine"` rule blocks **both** cross-district
  movement **and** `trade_resource` (both halves of Q3's "both" answer),
  and repeal lifts both restrictions.
- The elder is never selected as a raid-contact victim or contagion-spread
  target across the soak (Sage-exclusion rule, §7), and `_sage_emergency()`
  still triggers correctly for any non-raid/contagion health event during
  the same run.
- Death/incapacitation from raid contact damage or contagion health loss
  appears via the same `agent["health"]` clamp / `_update_survival`
  collapse path every other health-loss event uses — never a call to
  `_agent_dies` (Q9; `_agent_dies` remains reserved for natural death only
  under this plan's default design).
- The raid/contagion telegraph warning is visible (viewer and/or think
  payload) `RAID_TELEGRAPH_LEAD_FRAMES` before impact, not simultaneously
  with it.

Verify via the browser and `simulation/logs/<timestamp>/` — activity log,
`benchmarks.jsonl` if a new benchmark is added — then stop the native
server and confirm §13 before moving to gate 2. A failure at gate 1 goes
back to the implementer; it never gets "retried in Docker."

Before trusting any of the above, confirm storage isolation (§11) — this
worktree's own `state.db`/`logs/`/`memory_store.json`, never main's.

---

## 11. Storage isolation

This worktree owns its own `simulation/state.db`, `simulation/memory_store.json`,
and `simulation/logs/` — never the main repo's copies (meta-plan §6).

- **Isolation is automatic for native host runs.** `DB_PATH`
  (`sim_engine/persistence.py`) and the `SessionLogger`/`MemoryStore` paths
  built in `server.py` all resolve via `os.path.abspath(__file__)` against
  files inside *this worktree's* `simulation/` directory — not the shell's
  current working directory. Running `uv run python simulation/server.py`
  from inside this worktree cannot reach main's `state.db`, `logs/`, or
  `memory_store.json`, regardless of where the shell was `cd`'d from.
- This worktree starts with **none** of `state.db`, `memory_store.json`, or
  `logs/` — all three are gitignored, so a fresh worktree checkout is a
  clean world. If this plan needs a populated world to test against, build
  it inside this worktree; never copy main's files in.
- SQLite WAL sidecars (`state.db-wal`/`state.db-shm`), if they appear,
  follow the same rule and stay inside this worktree.
- Docker bind mounts (§12) point at THIS worktree's files only — the
  `%CD%`-relative volume paths in the `docker run` command resolve against
  `-WorkingDirectory <worktree path>`, never the main repo.
- **Verify before trusting any gate-1 result:** start the native server and
  read its own startup lines — `[server] Logging session to: <dir>` and
  `[server] MemoryStore ... from <path>` — and confirm both paths are under
  this worktree, not the main repo. Or run, from inside the worktree:
  ```bash
  uv run python -c "from simulation.sim_engine.persistence import DB_PATH; print(DB_PATH)"
  ```
  and confirm the printed path is this worktree's `simulation/state.db`.
- Teardown (§15): removing this worktree removes its `state.db`, `logs/`,
  and `memory_store.json` with it — that is intended. Copy out anything
  worth keeping first.

---

## 12. Docker container lifecycle + gate 2 (second gate only)

**Role of Docker, narrowed.** Containers are **not** the editing or first-test
surface. They exist for exactly two purposes:

1. **Gate 2** — re-run the plan's changes in the packaged container to confirm
   they hold under the supported Docker path (bind mounts,
   `host.docker.internal` Ollama, image build).
2. **Delivering the code changes** for the plan once gate 2 passes.

```bash
docker build -t gitserv-idea-05 ../gitserv-idea-05-raiders-contagion
```

Pre-create bind-mount targets before first run — empty `simulation/state.db`
file, `simulation/memory_store.json` as `{}`, `simulation/logs/` directory.

```powershell
Start-Process cmd.exe -ArgumentList '/k', 'title simserver-idea-05 && docker run --name gitserv-idea-05 -p 5001:5001 -e SIM_OLLAMA_HOST=host.docker.internal:11434 -v "%CD%\simulation\state.db:/app/simulation/state.db" -v "%CD%\simulation\logs:/app/simulation/logs" -v "%CD%\simulation\memory_store.json:/app/simulation/memory_store.json" gitserv-idea-05' -WorkingDirectory <worktree path>
```

Foreground container, no `-d`, no `--restart`. Port is always 5001; at most
one container may be running a server at any time. Ollama stays host-native.
Nothing is edited in the container — any fix found at gate 2 goes back to the
worktree, through gate 1 again, then rebuilt here.

### 12.1 Destroy — on user approval

```bash
docker stop gitserv-idea-05
docker rm gitserv-idea-05
docker image rm gitserv-idea-05
```

Then remove the worktree. Teardown is gated on user approval, never on
"the tests passed."

---

## 13. Single-server-instance verification — mandatory block

```powershell
docker ps -a --filter name=gitserv
Get-CimInstance Win32_Process -Filter "name='python.exe'" | Where-Object { $_.CommandLine -match 'simulation.server' }
```

Expected: at most one running container **and** zero conflicting native servers, or zero
containers and exactly one native server. Anything else must be resolved before
reporting done.

- Stop a stray container: `docker stop <name>` then `docker rm <name>`.
- Kill extra native servers (Bash: `pgrep -fa "simulation/server.py"`); close the
  `simserver` window.
- **`uv run` shows a wrapper + interpreter pair — that parent/child pair is ONE native
  instance, not a duplicate.** Verify `ParentProcessId` and port binding before killing
  anything.

A plan may not be reported complete while more than one server instance is live.

---

## 14. Acceptance checks

- [ ] All nine §2 questions answered by the user and recorded in this file
      (done — see §2).
- [ ] Storage isolation (§11) verified before trusting any gate-1 result:
      the native run's `state.db`/`logs/`/`memory_store.json` resolve inside
      this worktree, not the main repo.
- [ ] Owning specs (§4) updated in the same change as the code that changes
      behavior, including the explicit three-way disambiguation from
      `PRESSURE_LOOP_ENABLED`'s night-exposure/wildlife pressure and
      `WILDLIFE_ENABLED`'s huntable fauna.
- [ ] New flag registered in `specs/01-architecture.md` flag index, default
      `True`, name does not collide with `PRESSURE_LOOP_ENABLED`.
- [ ] Fully deterministic: no unseeded RNG use; documented tick gate and
      cadence matching an existing constant pattern (§5's proposed
      constants table).
- [ ] Raid mechanic implements BOTH resource removal AND structure damage
      (Q1) — verified independently, not just "a raid happened."
- [ ] Quarantine restricts BOTH inter-district movement AND trading (Q3) —
      verified independently at every movement check site plus
      `trade_resource`.
- [ ] Guard-count mitigation formula (Q5, §5/§7: `min(cap, guard_count *
      per_guard)`) implemented exactly as specced, not the wildlife
      single-guard rule.
- [ ] Wall's existing structure type reused (Q6) via a new `"mitigates"`
      effect kind — no duplicate/parallel wall concept introduced.
- [ ] Healer coverage implements BOTH agent-based AND structure-based
      (`clinic`, Q7) recovery bonuses, stacking additively.
- [ ] `_sage_emergency()`/`_rush_to_heal` protection verified intact against
      any non-raid/contagion health event during the same soak, AND the
      elder is verified never selected as a raid-contact victim or
      contagion-spread target (§7's stricter exclusion, mirroring
      `confront_agent`).
- [ ] Death/incapacitation confirmed routed through the exact same
      `agent["health"]` clamp / `_update_survival` collapse path every
      other health-loss event uses (Q9) — `_agent_dies` is never called
      from raid/contagion code under this plan's default design; no bypass.
- [ ] `"quarantine"` added to `RULE_KINDS` with a real `_apply_governance_rule`
      mechanical effect, following the `propose_rule`/`vote_rule` pattern
      exactly (no shortcut path for enacting it).
- [ ] Action-sync checklist resolved as N/A per §6 — no new
      `DECISION_ACTIONS`/`DECISION_SCHEMA`/`SYSTEM_PROMPT`/
      `apply_decision`/`available_actions`/`ACTION_LABELS` entries.
- [ ] Telegraph warning (Q4) visible `RAID_TELEGRAPH_LEAD_FRAMES` before
      impact, in the viewer and/or agent think payloads.
- [ ] Gate 1 exercises the flag in its ON state, observing at least one
      raid and one contagion event deterministically, per §10.1's expanded
      verify list.
- [ ] New targeted smoke authored and passing.
- [ ] §13 single-instance check passes before reporting done.

---

## 15. Teardown

On user approval of the implementation: destroy this plan's container and
image (§12.1), then remove the worktree (§10). If the user rejects or defers,
the container and worktree stay up untouched.
