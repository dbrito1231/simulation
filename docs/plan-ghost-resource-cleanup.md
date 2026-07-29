# Fix the `dried_fish` ghost resource

## Context

Daily Council sessions have been dominated by talk of a shortage of "dried fish" — a resource that does not exist in any meaningful sense. Investigation of the live `simulation/state.db` and four sessions of `activity.jsonl`/`llm.jsonl` confirmed:

- `resourceRegistry["dried_fish"]` exists (`{"name": "Dried Fish", "gatherZone": None, ...}`), `stockpile["dried_fish"] == 0`.
- **Nothing produces it.** No gather zone, no recipe, no structure `produces` effect, zero "produced ... dried_fish" events in any log. The working equivalent is `salted_fish` (13,115 in stockpile, made by a Salt Cellar) — `dried_fish` looks like an abandoned earlier invention.
- The Daily Council agenda builder reports it as critically low *every single session*, because of this expression at [sim_engine.py:9382](../simulation/sim_engine.py):
  ```python
  scarce = sorted(rid for rid, amount in stockpile.items() if amount <= EDIBLE_RESERVE)
  ```
  It iterates **every key ever written** to the stockpile, and a permanently-zero orphan always qualifies. It then ships verbatim to the LLM via the uncapped `Agenda:` line at [prompts.py:368](../simulation/prompts.py). This is the **only** path by which a zero stockpile value reaches an LLM prompt.

Three root causes stack up, and the approved scope fixes all three:

1. **Report site (Part A)** — the agenda has no notion of whether a resource is *obtainable*. It also misuses `EDIBLE_RESERVE`, documented at `specs/08-systems-economy.md:30` as a *per-agent food carry reserve*, as a generic village-wide threshold. Line 9382 is the only place in the repo that does this.
2. **No garbage collection (Part B)** — `_maybe_retire_custom_resource` (sim_engine.py:9356) was gutted to a no-op ("invention is intentionally unlimited"). Its safety predicate `_custom_resource_referenced` (9321) survived as live-but-unused code. `resourceRegistry` has **zero** deletion sites repo-wide, and `stockpile` keys are never popped — orphans accumulate forever.
3. **Viewer (Part C)** — `villageResourceBreakdown()` (index.html:2174) sums *only agent inventories* despite the panel being labeled "Village resources:" (index.html:1015). The stockpile's 38,964 tools / 25,029 kelp / 13,115 salted_fish / 92,430 stone_block are invisible — which is why the ghost couldn't be seen from the UI in the first place.

**Intended outcome:** councils stop discussing phantom shortages while still surfacing real ones (`gold: 0`, `rope: 0`, `water: 1`, `wood: 1`, `stone: 3` are all genuine and must keep reporting); orphaned inventions get cleaned up automatically; the viewer shows the village's actual holdings.

### Verified corrections to the initial read (do not re-litigate)

- **`_resource_price` is clean.** Ghosts get a normal base price, not max scarcity — no district stocks + not edible ⇒ `signals == 0` ⇒ `scarcity = 1.0` (sim_engine.py:4076). Do not touch the price path.
- **The other four low-stock computations are immune** (2993, 3040, 8811, 10915) — all read `districtStocks`, not `stockpile`.
- **`dried_fish` is hard-coded as a consumption sink** at sim_engine.py:4323 (`("pottery", "dried_fish")`) and documented at `specs/08-systems-economy.md:294`. Neither id is seeded anywhere. The sink uses `stock.get(r, 0)`, so retiring the registry entry does **not** break it — but the spec line is wrong and must be corrected.
- **`_get_structure_function` returns `{}` outright when `STRUCTURE_EFFECTS_ENABLED` is False** (3522). Any reference-scan routed through it goes blind with that flag off — Part B must not depend on it as-is.
- **`coin` breaks a naive obtainability test**: `gatherZone: None`, in no recipe, produced only by `_maybe_mint_coin` (4003). Needs an explicit carve-out or coin shortages silently stop reporting.
- **`MAX_CUSTOM_RESOURCES` is dead and must stay dead** — server.py:2059-2061 explicitly ignores it, and `scripts/blueprint_smoke.py:76-84` actively asserts it is not enforced.

## Ordering

**A → C → B.** Part A alone removes the user-visible symptom at near-zero risk. Part C is viewer-only. Part B is the only part that deletes state, so it lands last behind the widest safety net. Each part is independently shippable *with its spec update*.

---

## Part A — the report site (`simulation/sim_engine.py`)

**A1. Constants**, beside the other Daily Council constants near sim_engine.py:1051:
```python
DAILY_COUNCIL_SCARCITY_THRESHOLD = 3   # village-wide holdings at/below this read as "low stores"
DAILY_COUNCIL_SCARCITY_TOPICS = 8      # replaces the bare [:8] literals in _daily_council_agenda
```
Set the threshold to **3**, matching today's effective value. The bug being fixed is the *obtainability* filter, not the number — decoupling from `EDIBLE_RESERVE` is the point, and holding the value constant keeps Part A to exactly one behavioral change and no prompt-size growth. Comment it as inherited-and-now-independently-tunable. Leave `EDIBLE_RESERVE` (643) and its three correct consumers (2687, 4361, 8920) untouched.

**A2. `_structure_function_for_type(self, type_)`** — new private, directly above `_get_structure_function` (3521). Returns the function dict via `projectRegistry` → `PROJECT_TEMPLATES` → `SEED_STRUCTURE_FUNCTIONS` → `LEGACY_CUSTOM_PRODUCE`, **without** the `STRUCTURE_EFFECTS_ENABLED` early-return. `_get_structure_function` then becomes a one-line wrapper (`return self._structure_function_for_type(type_) if STRUCTURE_EFFECTS_ENABLED else {}`) — behavior-identical for every existing caller.

**A3. `_resource_in_function(self, rid, fn)`** — new, beside A2. Must cover **all five** function keys that `validate_function_block` (server.py:1884-1955) accepts: `produces[].resource`, `boosts[].resources`, `stores[].resource` (consumed at 3739), `upkeep.resource` (consumed at 5869), and `unlocks[kind=transit].consumes`. The existing partial scan at 9339-9341 misses the last three.

**A4. `_resource_is_obtainable(self, rid)`** — new, after `_gather_zone_for_resource` (2915), with the other registry-lookup helpers. No equivalent exists today (checked `_gather_zone_for_resource`, `_get_zone_resources` 2919, `_resources_for_district_kind` 3526). True if **any** of:
- `self._gather_zone_for_resource(rid)` is truthy;
- `rid in self.RECIPES`, or `any(p["id"] == rid for p in c["pendingRecipes"])`;
- any structure function anywhere emits it (A2 + A3, scanning `projectRegistry` **and** the types of standing `c["structures"]`);
- `ECONOMY_ENABLED and rid == "coin" and self._mint_active()` — the mint carve-out. Comment it explicitly as the one exception to "producers must be declarative."

**A5. `_village_holdings(self, rid)`** — new, beside A4. Returns `stockpile.get(rid, 0) + sum(a["resources"].get(rid, 0) for a in self.agents)`. **Deliberately excludes `districtStocks`** — those are in-ground deposits, not stores, and already have their own prompt channel via `_format_district_stocks_for_prompt` (2993). Say so in the docstring and the spec.

**A6. Rewrite sim_engine.py:9382**:
```python
scarce = sorted(
    rid for rid in (c.get("stockpile") or {})
    if self._resource_is_obtainable(rid)
    and self._village_holdings(rid) <= DAILY_COUNCIL_SCARCITY_THRESHOLD
)
```
and swap both `[:8]` slices (the `scarce` slice at 9399 and the `active_projects` slice at 9392) to `DAILY_COUNCIL_SCARCITY_TOPICS`.

**Expected on the live save:** `dried_fish` drops. `gold` (zone `cave`), `water` (`village`), `stone` (`cave`), `rope` (in `SEED_RECIPES`, 1593) all still report.

---

## Part C — viewer (`simulation/index.html`, no server change)

`civ.stockpile` is already in the `/state` payload (sim_engine.py:16030).

**C1.** `villageResourceBreakdown()` (2174-2183): seed each `out[key]` from `(getCiv().stockpile || {})[key] || 0`, then add agent inventories on top. Keep the `n > 0` filter at 2180 so retired/zero resources still never render.

**C2.** `totalVillageResources()` (2164-2172): reimplement as a sum over `villageResourceBreakdown()` so the headline count and the chips can never disagree.

**C3.** Change detection (2763-2769): `sidebarKey` already contains `villageResourceBreakdown()`, which after C1 depends on `civ.stockpile` — the key is automatically correct. **Do not** also add `civ.stockpile` to the array; it is a ~40-key dict that changes nearly every tick and would force a sidebar re-render on every poll. Add a one-line comment recording that the breakdown is the stockpile's proxy in this key.

Expected: the panel gains tools / kelp / salted_fish / stone_block etc. The chip list will get noticeably longer — acceptable for now. Sorting/capping it would be new behavior needing its own spec line; leave it out.

---

## Part B — orphan GC (`simulation/sim_engine.py`)

**B1. Constant**, beside `BLUEPRINT_AMNESTY_FRAMES` (779):
```python
CUSTOM_RESOURCE_RETIRE_FRAMES = STALL_THRESHOLD * 120
```
~2× the amnesty window, because a resource can legitimately sit unused between approval and first build.

**B2. Harden `_custom_resource_referenced`** (9321-9354) — it stays the sole predicate, now correct:
- add `if self._resource_is_obtainable(rid): return True` at the top, making obtainability the shared spine of A and B and immunizing every gatherable/craftable/minted resource;
- replace the `_get_structure_function(pid)` call at 9338 with `_resource_in_function(rid, self._structure_function_for_type(pid))` — fixes the flag-off blindness;
- run the same scan over `{s["type"] for s in c["structures"]}`, so a standing structure whose registry entry was already archived by `_maybe_retire_blueprint` still protects its resources;
- apply `_resource_in_function` to `pendingBlueprints` too (9343-9350), replacing the partial checks;
- add `harvestQuotas` rule targets and active-project `contributed` keys.

**B3. Stamp the recipe path.** sim_engine.py:6466 registers a resource on recipe approval but — unlike the blueprint path at 11651 — never writes `customResourceAddedFrame`. Add the stamp, mirroring 11651. (Belt and braces: B4's stamp-on-first-sight covers it anyway, and B2 protects recipe outputs via `rid in self.RECIPES` regardless.)

**B4. Rewrite `_maybe_retire_custom_resource`** (9356-9362) as a reference-based prune with **no cap**, modeled line-for-line on `_maybe_amnesty_rejected_blueprints` (9223-9244):

```
for rid in list(c["resourceRegistry"]):
    skip if rid in BASE_RESOURCES or rid in CRAFTED_RESOURCES
    skip if self._custom_resource_referenced(rid)
    added = frames.get(rid)
    if added is None: frames[rid] = self.frameTick; continue      # stamp-on-first-sight
    if self.frameTick - added < CUSTOM_RESOURCE_RETIRE_FRAMES: continue
    name = c["resourceRegistry"][rid].get("name", rid)
    del c["resourceRegistry"][rid]
    c["stockpile"].pop(rid, None)                                  # the old body's gap
    for stocks in c["districtStocks"].values(): stocks.pop(rid, None)
    frames.pop(rid, None)
    self._push_activity(f"The idea of {name} has faded from the village — nothing made or used it")
```

Iterate a `list()` copy. Keep the call site at 13377 unchanged. `_custom_resource_count` (8050) and its `MAX_CUSTOM_RESOURCES` plumbing stay untouched and unenforced.

**Note the stamp-on-first-sight consequence:** the live save's `dried_fish` starts its clock at the first tick after deploy and retires ~40 min later, not instantly. This matches the amnesty precedent and is correct — call it out when reporting done so it doesn't read as a failed fix.

**B5. Persistence — nothing to do, but record why.** `customResourceAddedFrame` is already `setdefault`ed on restore (13628) and `_serialize_state` (13520) dumps the civ dict wholesale, so deletions persist and are never resurrected (restore re-seeds only BASE/CRAFTED). **No tombstone list** — a retired id *should* be re-inventable, exactly like an amnestied blueprint id. State this in the spec so a future reader doesn't add one.

### Part B risk register
- **Could the GC delete something still needed?** After B2, only via a reference path none of the eight checks cover. The two known non-declarative producers are the mint (handled in A4) and the `("pottery", "dried_fish")` comfort sink (intentionally *not* protected — retiring those is the point). Before landing, grep the 16 `stockpile"][` write sites to confirm no other engine-level producer exists (4240/4242 structure produces, 4017-4018 mint, 15409/15424 god grant, 9088 refund; the rest are debits).
- **God mode:** `grant_resource` requires the id to be in the live registry (`specs/08-systems-economy.md:466-471`). Retiring an id makes a previously valid grant fail with "unknown resource id" — correct, but document it.
- **Ordering coupling:** if B shipped without A, the agenda would keep naming `dried_fish` for the whole grace window.

---

## Spec updates (same change — SDD rule)

| File | Anchor | Edit |
|---|---|---|
| `specs/09-systems-society.md` | 85-90 (agenda paragraph) | The limitations topic reports a resource only if *obtainable* (gather zone / recipe / structure `produces` / active mint), measured against **village-wide holdings** (stockpile + agent inventories; district ground stocks excluded — they have their own prompt channel). Name `DAILY_COUNCIL_SCARCITY_THRESHOLD` and `DAILY_COUNCIL_SCARCITY_TOPICS`; state that `EDIBLE_RESERVE` is *not* used here. |
| `specs/09-systems-society.md` | 169 (invention safeguards) | Correct the stale row: `MAX_CUSTOM_RESOURCES = 10` is **not enforced** (server.py:2059-2061) — invention is unlimited by policy. Add a new row for orphan retirement: `CUSTOM_RESOURCE_RETIRE_FRAMES`, `_maybe_retire_custom_resource` prunes registry + stockpile + districtStocks for any custom resource unreferenced for the full window; no cap, stamp-on-first-sight clock, retired ids re-inventable (no tombstone). Cross-reference row 168. |
| `specs/08-systems-economy.md` | 30 (constants table) | Scope `EDIBLE_RESERVE`'s description explicitly to `EDIBLE_RESOURCES` and per-agent carry, so it can't be borrowed again. |
| `specs/08-systems-economy.md` | 294-295 (comfort consumption) | `pottery`/`dried_fish` are *opportunistic* sink ids with no seeded producer; the sink fires only if an invention supplies them, and either id may be pruned by the orphan GC. |
| `specs/08-systems-economy.md` | ~466-471 (God-mode resource gate) | One sentence: a retired id fails the known-resource gate until re-invented. |
| `specs/11-viewer.md` | ~185-190 (Civilization panel) | New text — "Village resources" = `civ.stockpile` **plus** every agent inventory, filtered to `n > 0`, coloured from `resourceRegistry`, change-detected via `villageResourceBreakdown()` inside `sidebarKey` (stockpile intentionally not in the key directly). This filter is currently undocumented entirely. |
| `specs/02-engine-core.md` | ~30 | Verify only — `_maybe_retire_custom_resource` is already listed in the periodic batch and stays there. |
| `specs/01-architecture.md` | ~116 (flag index) | Verify only — no new flags, constants only; the flag count must be unchanged. |

---

## Verification

No test suite, no linter. Run in this order (all deterministic, no Ollama needed):

1. **`uv run python scripts/daily_council_smoke.py`** — builds a real engine and asserts on `council["agenda"]` (lines 118-147). This is where Part A's regression belongs. **Extend it**: before the `_tick_once()` at line 121, inject `resourceRegistry["ghost_fish"] = {"name": "Ghost Fish", "gatherZone": None, ...}`, `stockpile["ghost_fish"] = 0`, `stockpile["gold"] = 0`; then assert the `limitations` detail contains `gold` and does **not** contain `ghost_fish`. Add a second case: an agent holding 50 of an obtainable resource keeps it off the list (covers A5).
2. **`uv run python scripts/blueprint_smoke.py`** — already exercises `resourceRegistry` and `_custom_resource_count` (87-91) and asserts `MAX_CUSTOM_RESOURCES` is *not* enforced (76-84). Must still pass unchanged — that is the guard against accidentally reviving the cap. **Extend for Part B**: approve a blueprint with a new resource, assert `_custom_resource_referenced` is True while the `projectRegistry` entry exists; delete that entry, advance `frameTick` past `CUSTOM_RESOURCE_RETIRE_FRAMES` twice (once to stamp, once to retire), call `_maybe_retire_custom_resource`, assert the id is gone from `resourceRegistry`, `stockpile`, and every `districtStocks` bucket. Add a negative case: a resource with a `gatherZone` is never retired.
3. **`uv run python scripts/sid_parity_smoke.py`** and **`uv run python scripts/path1_smoke.py`** — broad engine smokes; these catch any signature break from the A2 `_get_structure_function` refactor.
4. **`uv run python scripts/daily_council_regression.py`** — runs the above across both `DAILY_COUNCIL_ENABLED` values in fresh subprocesses with temp `state.db`. Run last, as the gate.
5. **`uv run python scripts/daily_council_state_probe.py`** — confirms the `/state` projection still serializes.

**Live confirmation against the real ghost (port 5001, real `state.db`):**

- Baseline:
  ```bash
  curl -s http://127.0.0.1:5001/state | uv run python -c "import json,sys; c=json.load(sys.stdin)['civilization']; print(c['stockpile'].get('dried_fish'), 'dried_fish' in c['resourceRegistry'])"
  ```
  Expect `0 True`.
- **Back up `state.db` before restarting** — autosave runs every 10s and Part B deletes live state. *The user should take this backup, not the implementer.*
- Restart the server per CLAUDE.md (own visible titled `cmd` window; kill any prior instance; verify a single instance at the end — remembering that `uv run` legitimately shows two `python.exe` in a parent/child pair).
- **Part A:** at the next convene, `civilization.dailyCouncil.agenda`'s `limitations` item must no longer contain dried fish, while `gold`/`water`/`stone`/`rope` remain if still low. `daily_council_state_probe.py --url http://127.0.0.1:5001/state` captures the live agenda to `scripts/out/daily_council_state.json` for a diffable artifact.
- **Part B:** after ~40 min uptime (`CUSTOM_RESOURCE_RETIRE_FRAMES`), re-run the curl → expect `None False`, plus an activity line "The idea of Dried Fish has faded from the village". Not instant, by design.
- **Part C:** reload the viewer; "Village resources:" should now show tools / kelp / salted_fish / stone_block and a much larger total.

## Critical files

- `simulation/sim_engine.py` — Parts A and B
- `simulation/index.html` — Part C
- `specs/09-systems-society.md`, `specs/08-systems-economy.md`, `specs/11-viewer.md` — spec updates
- `scripts/daily_council_smoke.py`, `scripts/blueprint_smoke.py` — regression coverage

Per CLAUDE.md's model policy, all three parts are dispatched to `implementer` subagents (Sonnet), one part per dispatch, reviewed between parts.
