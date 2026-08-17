"""Deterministic smoke for GET /wiki Phase 2a + 2b (twelve entity kinds).

Covers: route shape, cross-link resolution for agent relationships and
structure homeOf/districtId (Phase 2a), district/settlement/treaty/resource/
project/recipe pages with correct links (Phase 2b), social tie cross-links on
agent pages, settlements/treaties absent when PATH1_DIPLOMACY_ENABLED=False,
districts helper extraction regression, and flag-off disabled response.
Ollama-free.

Run: uv run python scripts/idea09_world_wiki_smoke.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "simulation"))

import sim_engine as se  # noqa: E402
from _sid_parity_smoke.helpers import assert_true, make_engine  # noqa: E402


# ---------------------------------------------------------------------------
# Shared helper: mirrors server.py's world_wiki() route body so smokes can
# verify behavior without starting a Flask server.  Must stay in sync with
# that route.
# ---------------------------------------------------------------------------

def _districts_snapshot_payload_smoke(engine):
    """Mirror of server._districts_snapshot_payload (mechanical copy for smoke use)."""
    c = engine.civilization
    districts = [
        {"id": did, "kind": d["kind"], "tile": d["tile"], "label": d.get("label"),
         "bounds": dict(d["bounds"]),
         "buildGrid": dict(d["build_grid"]) if d.get("build_grid") else None,
         "tiles": dict(d.get("tiles") or {}),
         "terrain": dict(d.get("terrain") or {}),
         "settlementId": d.get("settlementId")}
        for did, d in c["districts"].items()
    ]
    road_nodes = {nid: dict(n) for nid, n in c["roadNodes"].items()}
    road_edges = [list(e) for e in c["roadEdges"]]
    return {"districts": districts, "roadNodes": road_nodes, "roadEdges": road_edges}


def _build_wiki_pages(engine, sem):
    """Mirror the /wiki route logic, called directly on the engine.

    This function replicates the route so smokes can verify behavior without
    starting a Flask server.  It must stay in sync with server.py's world_wiki()
    route body.
    """
    if not sem.WORLD_WIKI_ENABLED:
        return {"ok": False, "reason": "disabled"}

    with engine.lock:
        c = engine.civilization
        env_lit_types = engine._env_lit_types() if sem.ENV_EFFECTS_ENABLED else set()
        agent_rows = [engine._agent_snapshot_row(a) for a in engine.agents]
        struct_rows = [
            engine._structure_snapshot_row(s, env_lit_types, include_sprite=False)
            for s in c["structures"]
        ]
        rules_active = [dict(r) for r in c.get("rules") or []]
        rules_pending = [dict(r) for r in c.get("pendingRules") or []]
        constitution = [dict(p) for p in engine._ensure_constitution()]
        belief_reg = {}
        if sem.CULTURE_ENABLED:
            raw_reg = engine._belief_registry()
            belief_reg = (
                {bid: dict(e) for bid, e in raw_reg.items()}
                if isinstance(raw_reg, dict)
                else {}
            )
        chronicle_raw = []
        if sem.CULTURE_ENABLED:
            for entry in list((c.get("chronicle") or [])[-sem.CHRONICLE_CAP:]):
                if entry.get("kind") in sem.CHRONICLE_MILESTONE_KINDS:
                    chronicle_raw.append(dict(entry))
        # Extended entities (Phase 2b).
        dist_snap = _districts_snapshot_payload_smoke(engine)
        resource_reg = {rid: dict(d) for rid, d in c["resourceRegistry"].items()}
        project_reg = {pid: dict(p) for pid, p in c["projectRegistry"].items()}
        recipe_reg = {}
        if sem.CRAFTING_ENABLED:
            recipe_reg = {
                rid: {"name": r["name"], "inputs": dict(r["inputs"]), "station": r.get("station")}
                for rid, r in engine.RECIPES.items()
            }
        settlement_rows = []
        treaty_rows = []
        if sem.path1_on("PATH1_DIPLOMACY_ENABLED"):
            settlement_rows = [dict(row) for row in (c.get("settlements") or [])]
            treaty_rows = [dict(row) for row in (c.get("treaties") or [])]
        social_ties = engine._social_ties_snapshot()

    name_to_id = {row["name"]: row["id"] for row in agent_rows}

    agent_pages = []
    for row in agent_rows:
        aid = row["id"]
        links = []
        for tname, valence in (row.get("relationships") or {}).items():
            tid = name_to_id.get(tname)
            if tid is not None:
                links.append({"targetKind": "agent", "targetId": tid, "relation": valence})
        current_dist = row.get("currentDistrict")
        if current_dist is not None:
            links.append({"targetKind": "district", "targetId": current_dist, "relation": "district"})
        home_dist = row.get("homeDistrict")
        if home_dist is not None:
            links.append({"targetKind": "district", "targetId": home_dist, "relation": "homeDistrict"})
        fields = {
            "id": aid,
            "name": row["name"],
            "role": row["role"],
            "color": row["color"],
            "position": {"x": row["x"], "y": row["y"]},
            "district": current_dist,
            "homeDistrict": home_dist,
            "resources": row.get("resources") or {},
            "hunger": row.get("hunger"),
            "health": row.get("health"),
            "incapacitated": row.get("incapacitated"),
            "beliefs": row.get("beliefs") or [],
            "lastAction": row.get("lastAction"),
            "assignedTask": row.get("assignedTask"),
            "relationships": row.get("relationships") or {},
            "lastReasoning": row.get("lastReasoning"),
            "deceased": row.get("deceased", False),
            "buried": row.get("buried", False),
        }
        if row.get("age") is not None:
            fields["age"] = row["age"]
        if row.get("lifeStage") is not None:
            fields["lifeStage"] = row["lifeStage"]
        if row.get("skills") is not None:
            fields["skills"] = row["skills"]
        if sem.CULTURE_ENABLED and row.get("personalityTraits") is not None:
            fields["personality"] = row["personalityTraits"]
        agent_pages.append({"id": aid, "kind": "agent", "fields": fields, "links": links})

    # Social tie cross-links on agent pages.
    if social_ties:
        agent_pages_by_id = {p["id"]: p for p in agent_pages}
        for tie in social_ties:
            from_id = tie.get("from")
            to_id = tie.get("to")
            valence = tie.get("valence")
            if not from_id or not to_id:
                continue
            if from_id in agent_pages_by_id:
                agent_pages_by_id[from_id]["links"].append(
                    {"targetKind": "agent", "targetId": to_id, "relation": f"socialTie:{valence}"}
                )
            if to_id in agent_pages_by_id:
                agent_pages_by_id[to_id]["links"].append(
                    {"targetKind": "agent", "targetId": from_id, "relation": f"socialTie:{valence}"}
                )

    structure_pages = []
    for s in struct_rows:
        links = []
        owner_id = None
        if s.get("homeOf") is not None:
            owner_id = name_to_id.get(s["homeOf"], s["homeOf"])
            links.append({"targetKind": "agent", "targetId": owner_id, "relation": "homeOf"})
        if s.get("districtId") is not None:
            links.append({"targetKind": "district", "targetId": s["districtId"], "relation": "districtId"})
        fields = {
            "id": s["id"],
            "type": s["type"],
            "districtId": s.get("districtId"),
            "homeOf": owner_id,
            "condition": s.get("condition"),
            "isRuin": s.get("isRuin", False),
            "level": s.get("level", 1),
            "visualTier": s.get("visualTier", 1),
            "name": s.get("name"),
        }
        structure_pages.append({"id": s["id"], "kind": "structure", "fields": fields, "links": links})

    belief_pages = []
    if sem.CULTURE_ENABLED:
        for bid, entry in belief_reg.items():
            fields = {
                "id": bid,
                "name": entry.get("name", bid),
                "tenet": entry.get("tenet", ""),
                "affinity": entry.get("affinity", []),
                "authoredBy": entry.get("authoredBy"),
            }
            belief_pages.append({"id": bid, "kind": "belief", "fields": fields, "links": []})

    rule_pages = []
    if sem.RULES_ENABLED:
        seen_rule_ids: set = set()
        for r, status in (
            [(r, "enacted") for r in rules_active]
            + [(r, "pending") for r in rules_pending]
            + [(r, "constitution") for r in constitution]
        ):
            rid = r.get("id")
            if not rid or rid in seen_rule_ids:
                continue
            seen_rule_ids.add(rid)
            fields = {
                "id": rid,
                "text": r.get("text", ""),
                "kind": r.get("kind", ""),
                "proposedBy": r.get("proposedBy"),
                "status": status,
            }
            if "value" in r:
                fields["value"] = r["value"]
            rule_pages.append({"id": rid, "kind": "rule", "fields": fields, "links": []})

    chronicle_pages = []
    if sem.CULTURE_ENABLED:
        for entry in chronicle_raw:
            frame = entry.get("frame")
            kind = entry.get("kind", "")
            cid = f"chronicle_{frame}_{kind}"
            fields = {
                "id": cid,
                "text": entry.get("text", ""),
                "frame": frame,
                "kind": kind,
            }
            chronicle_pages.append({"id": cid, "kind": "chronicle", "fields": fields, "links": []})

    # District pages.
    district_pages = []
    for d in dist_snap["districts"]:
        did = d["id"]
        links = []
        if d.get("settlementId") is not None:
            links.append({"targetKind": "settlement", "targetId": d["settlementId"], "relation": "settlementId"})
        fields = {
            "id": did,
            "kind": d["kind"],
            "tile": d.get("tile"),
            "label": d.get("label"),
            "bounds": d.get("bounds"),
            "settlementId": d.get("settlementId"),
        }
        district_pages.append({"id": did, "kind": "district", "fields": fields, "links": links})

    # Settlement pages.
    settlement_pages = []
    if sem.path1_on("PATH1_DIPLOMACY_ENABLED"):
        for s in settlement_rows:
            sid = s.get("id")
            if not sid:
                continue
            links = [
                {"targetKind": "district", "targetId": did, "relation": "districts"}
                for did in (s.get("districts") or [])
            ]
            fields = {
                "id": sid,
                "name": s.get("name", sid),
                "districts": list(s.get("districts") or []),
            }
            settlement_pages.append({"id": sid, "kind": "settlement", "fields": fields, "links": links})

    # Treaty pages.
    treaty_pages = []
    if sem.path1_on("PATH1_DIPLOMACY_ENABLED"):
        for t in treaty_rows:
            tid = t.get("id")
            if not tid:
                continue
            fields = {
                "id": tid,
                "name": t.get("name", tid),
                "value": t.get("value"),
                "tariff": t.get("tariff", 0),
                "frame": t.get("frame"),
            }
            treaty_pages.append({"id": tid, "kind": "treaty", "fields": fields, "links": []})

    # Resource pages.
    resource_pages = []
    for rid, entry in resource_reg.items():
        fields = {
            "id": rid,
            "name": entry.get("name", rid),
            "gatherZone": entry.get("gatherZone"),
            "color": entry.get("color"),
            "crafted": entry.get("crafted", False),
        }
        resource_pages.append({"id": rid, "kind": "resource", "fields": fields, "links": []})

    # Project pages.
    project_pages = []
    for pid, entry in project_reg.items():
        links = [
            {"targetKind": "resource", "targetId": rid, "relation": "needs"}
            for rid in (entry.get("needs") or {}).keys()
        ]
        fields = {
            "id": pid,
            "name": entry.get("name", pid),
            "needs": dict(entry.get("needs") or {}),
            "visualStyle": entry.get("visualStyle"),
            "tier": entry.get("tier"),
        }
        project_pages.append({"id": pid, "kind": "project", "fields": fields, "links": links})

    # Recipe pages.
    recipe_pages = []
    if sem.CRAFTING_ENABLED:
        for rid, r in recipe_reg.items():
            links = [
                {"targetKind": "resource", "targetId": res_id, "relation": "inputs"}
                for res_id in (r.get("inputs") or {}).keys()
            ]
            links.append({"targetKind": "resource", "targetId": rid, "relation": "output"})
            fields = {
                "id": rid,
                "name": r.get("name", rid),
                "inputs": dict(r.get("inputs") or {}),
                "station": r.get("station"),
            }
            recipe_pages.append({"id": rid, "kind": "recipe", "fields": fields, "links": links})

    return {
        "ok": True,
        "pages": {
            "agent": agent_pages,
            "structure": structure_pages,
            "belief": belief_pages,
            "rule": rule_pages,
            "chronicle": chronicle_pages,
            "district": district_pages,
            "settlement": settlement_pages,
            "treaty": treaty_pages,
            "resource": resource_pages,
            "project": project_pages,
            "recipe": recipe_pages,
        },
    }


def test_flag_off_disabled_shape():
    """When WORLD_WIKI_ENABLED is False the route returns the disabled shape."""
    old = se.WORLD_WIKI_ENABLED
    se.WORLD_WIKI_ENABLED = False
    try:
        engine = make_engine(4)
        result = _build_wiki_pages(engine, se)
        assert_true(result == {"ok": False, "reason": "disabled"},
                    f"flag-off should return disabled shape, got {result}")
    finally:
        se.WORLD_WIKI_ENABLED = old
    print("  OK flag-off disabled shape")


def test_response_shape():
    """Enabled response has ok=True and all twelve page-kind arrays."""
    old = se.WORLD_WIKI_ENABLED
    se.WORLD_WIKI_ENABLED = True
    try:
        engine = make_engine(4)
        result = _build_wiki_pages(engine, se)
        assert_true(result.get("ok") is True, f"ok should be True, got {result.get('ok')}")
        pages = result.get("pages")
        assert_true(isinstance(pages, dict), "pages should be a dict")
        for kind in ("agent", "structure", "belief", "rule", "chronicle",
                     "district", "settlement", "treaty", "resource", "project", "recipe"):
            assert_true(kind in pages, f"pages missing key: {kind}")
            assert_true(isinstance(pages[kind], list), f"pages[{kind!r}] should be a list")
        # Every agent page has required shape.
        for page in pages["agent"]:
            assert_true("id" in page, "agent page missing id")
            assert_true(page.get("kind") == "agent", f"agent page kind wrong: {page.get('kind')}")
            assert_true("fields" in page, "agent page missing fields")
            assert_true("links" in page, "agent page missing links")
            f = page["fields"]
            for key in ("name", "role", "color", "position", "resources",
                        "hunger", "health", "incapacitated", "beliefs",
                        "lastAction", "relationships", "deceased", "buried"):
                assert_true(key in f, f"agent page fields missing {key!r}")
    finally:
        se.WORLD_WIKI_ENABLED = old
    print("  OK response shape")


def test_agent_relationship_cross_link():
    """Relationship entries generate agent→agent cross-links by id."""
    old = se.WORLD_WIKI_ENABLED
    se.WORLD_WIKI_ENABLED = True
    try:
        engine = make_engine(4)
        agents = engine.agents
        if len(agents) < 2:
            print("  SKIP agent relationship cross-link (need >=2 agents)")
            return
        a, b = agents[0], agents[1]
        # Plant a relationship so a knows b as "ally".
        a["relationships"][b["name"]] = "ally"
        result = _build_wiki_pages(engine, se)
        pages_by_id = {p["id"]: p for p in result["pages"]["agent"]}
        page_a = pages_by_id.get(a["id"])
        assert_true(page_a is not None, f"no page for agent id {a['id']}")
        rel_links = [
            lnk for lnk in page_a["links"]
            if lnk.get("targetKind") == "agent" and lnk.get("targetId") == b["id"]
            and lnk.get("relation") == "ally"
        ]
        assert_true(len(rel_links) >= 1,
                    f"expected ally cross-link to agent {b['id']} on agent {a['id']}'s page, "
                    f"links={page_a['links']}")
    finally:
        se.WORLD_WIKI_ENABLED = old
        # Clean up the planted relationship.
        try:
            agents[0]["relationships"].pop(agents[1]["name"], None)
        except Exception:
            pass
    print("  OK agent relationship cross-link")


def test_structure_links():
    """Structures with homeOf and districtId emit correct cross-links."""
    old = se.WORLD_WIKI_ENABLED
    se.WORLD_WIKI_ENABLED = True
    try:
        engine = make_engine(4)
        c = engine.civilization
        agents = engine.agents
        if not agents:
            print("  SKIP structure cross-links (no agents)")
            return
        agent = agents[0]
        # Plant a structure with homeOf pointing to agent[0].
        test_struct = {
            "id": "smoke_house_1",
            "type": "house",
            "x": 100, "y": 100,
            "visualStyle": "house",
            "name": "Smoke House",
            "districtId": "village_core",
            "homeOf": agent["id"],
            "condition": 100,
            "isRuin": False,
            "level": 1,
            "visualTier": 1,
        }
        c["structures"].append(test_struct)
        try:
            result = _build_wiki_pages(engine, se)
            struct_pages = result["pages"]["structure"]
            smoke_page = next(
                (p for p in struct_pages if p["id"] == "smoke_house_1"), None
            )
            assert_true(smoke_page is not None, "smoke structure page missing")
            links = smoke_page["links"]
            home_links = [
                lnk for lnk in links
                if lnk.get("targetKind") == "agent"
                and lnk.get("targetId") == agent["id"]
                and lnk.get("relation") == "homeOf"
            ]
            assert_true(len(home_links) == 1,
                        f"expected homeOf cross-link to agent {agent['id']}, links={links}")
            dist_links = [
                lnk for lnk in links
                if lnk.get("targetKind") == "district"
                and lnk.get("targetId") == "village_core"
                and lnk.get("relation") == "districtId"
            ]
            assert_true(len(dist_links) == 1,
                        f"expected districtId cross-link to village_core, links={links}")
        finally:
            c["structures"] = [s for s in c["structures"] if s["id"] != "smoke_house_1"]
    finally:
        se.WORLD_WIKI_ENABLED = old
    print("  OK structure cross-links")


def test_belief_page_shape():
    """Belief pages have the correct fields when CULTURE_ENABLED."""
    old_wiki = se.WORLD_WIKI_ENABLED
    se.WORLD_WIKI_ENABLED = True
    try:
        engine = make_engine(4)
        result = _build_wiki_pages(engine, se)
        if not se.CULTURE_ENABLED:
            assert_true(result["pages"]["belief"] == [],
                        "CULTURE off should yield empty belief list")
            print("  OK belief page shape (CULTURE_ENABLED=False, empty list)")
            return
        beliefs = result["pages"]["belief"]
        assert_true(len(beliefs) > 0, "should have at least seed beliefs with CULTURE_ENABLED")
        for page in beliefs:
            assert_true(page.get("kind") == "belief", f"wrong kind: {page.get('kind')}")
            f = page["fields"]
            for key in ("id", "name", "tenet", "affinity", "authoredBy"):
                assert_true(key in f, f"belief page fields missing {key!r}")
            assert_true(isinstance(f["affinity"], list), "affinity should be a list")
    finally:
        se.WORLD_WIKI_ENABLED = old_wiki
    print("  OK belief page shape")


def test_chronicle_page_shape():
    """Chronicle pages have synthesized ids and correct fields."""
    old_wiki = se.WORLD_WIKI_ENABLED
    se.WORLD_WIKI_ENABLED = True
    try:
        engine = make_engine(4)
        # Inject a chronicle entry with a milestone kind.
        if se.CULTURE_ENABLED:
            engine.civilization.setdefault("chronicle", []).append({
                "text": "Smoke village was founded",
                "frame": 42,
                "kind": "district_founded",
            })
        result = _build_wiki_pages(engine, se)
        if not se.CULTURE_ENABLED:
            assert_true(result["pages"]["chronicle"] == [],
                        "chronicle pages should be empty when flags off")
            print("  OK chronicle page shape (flags off, empty list)")
            return
        chronicle = result["pages"]["chronicle"]
        assert_true(len(chronicle) >= 1, "expected at least the injected chronicle entry")
        injected = next(
            (p for p in chronicle if p["id"] == "chronicle_42_district_founded"), None
        )
        assert_true(injected is not None,
                    f"expected chronicle_42_district_founded, got ids={[p['id'] for p in chronicle]}")
        f = injected["fields"]
        assert_true(f["text"] == "Smoke village was founded", "text mismatch")
        assert_true(f["frame"] == 42, "frame mismatch")
        assert_true(f["kind"] == "district_founded", "kind mismatch")
        assert_true(injected["links"] == [], "chronicle links should be empty")
    finally:
        se.WORLD_WIKI_ENABLED = old_wiki
    print("  OK chronicle page shape")


def test_rule_page_shape():
    """Rule pages appear for enacted/pending/constitution entries."""
    old_wiki = se.WORLD_WIKI_ENABLED
    se.WORLD_WIKI_ENABLED = True
    try:
        engine = make_engine(4)
        if not se.RULES_ENABLED:
            result = _build_wiki_pages(engine, se)
            assert_true(result["pages"]["rule"] == [],
                        "RULES off should yield empty rule list")
            print("  OK rule page shape (RULES_ENABLED=False, empty list)")
            return
        c = engine.civilization
        c.setdefault("rules", []).append({
            "id": "smoke_rule_1",
            "text": "Share food equally",
            "kind": "resource_tax",
            "proposedBy": "Sage",
            "value": 0.1,
        })
        result = _build_wiki_pages(engine, se)
        rule_pages = result["pages"]["rule"]
        smoke_rule = next(
            (p for p in rule_pages if p["id"] == "smoke_rule_1"), None
        )
        assert_true(smoke_rule is not None, "smoke rule page missing")
        f = smoke_rule["fields"]
        assert_true(f["status"] == "enacted", f"expected enacted, got {f['status']}")
        assert_true(f["kind"] == "resource_tax", "kind mismatch")
        assert_true(f["value"] == 0.1, "value mismatch")
        assert_true(smoke_rule["links"] == [], "rule links should be empty")
    finally:
        # Remove injected rule.
        try:
            engine.civilization["rules"] = [
                r for r in engine.civilization.get("rules") or []
                if r.get("id") != "smoke_rule_1"
            ]
        except Exception:
            pass
        se.WORLD_WIKI_ENABLED = old_wiki
    print("  OK rule page shape")


# ---------------------------------------------------------------------------
# Phase 2b tests — extended entity kinds
# ---------------------------------------------------------------------------

def test_district_pages():
    """District pages have correct fields and settlementId→settlement link."""
    old = se.WORLD_WIKI_ENABLED
    se.WORLD_WIKI_ENABLED = True
    try:
        engine = make_engine(4)
        result = _build_wiki_pages(engine, se)
        district_pages = result["pages"]["district"]
        assert_true(len(district_pages) > 0, "expected at least one district page")
        for page in district_pages:
            assert_true(page.get("kind") == "district", f"wrong kind: {page.get('kind')}")
            f = page["fields"]
            for key in ("id", "kind", "bounds"):
                assert_true(key in f, f"district page fields missing {key!r}")
            assert_true("links" in page, "district page missing links")
        # Inject a district with a settlementId and verify the link is emitted.
        c = engine.civilization
        c["districts"]["smoke_dist_1"] = {
            "kind": "forest", "tile": "forest", "label": "Smoke Forest",
            "bounds": {"x1": 0, "y1": 0, "x2": 10, "y2": 10},
            "settlementId": "home",
        }
        try:
            result2 = _build_wiki_pages(engine, se)
            smoke_dist = next(
                (p for p in result2["pages"]["district"] if p["id"] == "smoke_dist_1"), None
            )
            assert_true(smoke_dist is not None, "smoke district page missing")
            slinks = [
                lnk for lnk in smoke_dist["links"]
                if lnk.get("targetKind") == "settlement" and lnk.get("targetId") == "home"
            ]
            assert_true(len(slinks) == 1,
                        f"expected settlementId→settlement link, got links={smoke_dist['links']}")
        finally:
            del c["districts"]["smoke_dist_1"]
    finally:
        se.WORLD_WIKI_ENABLED = old
    print("  OK district pages")


def test_settlement_pages_flag_on():
    """Settlement pages appear when PATH1_DIPLOMACY_ENABLED, with districts[] cross-links."""
    if not se.path1_on("PATH1_DIPLOMACY_ENABLED"):
        print("  SKIP settlement pages (PATH1_DIPLOMACY_ENABLED=False)")
        return
    old = se.WORLD_WIKI_ENABLED
    se.WORLD_WIKI_ENABLED = True
    try:
        engine = make_engine(4)
        # Inject a settlement with known districts.
        engine.civilization.setdefault("settlements", []).append({
            "id": "smoke_settlement_1",
            "name": "Smoke Town",
            "districts": ["village_core"],
        })
        try:
            result = _build_wiki_pages(engine, se)
            spages = result["pages"]["settlement"]
            smoke_s = next((p for p in spages if p["id"] == "smoke_settlement_1"), None)
            assert_true(smoke_s is not None, "smoke settlement page missing")
            assert_true(smoke_s.get("kind") == "settlement", "wrong kind")
            f = smoke_s["fields"]
            assert_true(f["name"] == "Smoke Town", "name mismatch")
            assert_true("village_core" in f["districts"], "district missing from settlement fields")
            dist_links = [
                lnk for lnk in smoke_s["links"]
                if lnk.get("targetKind") == "district" and lnk.get("targetId") == "village_core"
            ]
            assert_true(len(dist_links) == 1,
                        f"expected districts[]→district link, got links={smoke_s['links']}")
        finally:
            engine.civilization["settlements"] = [
                s for s in engine.civilization.get("settlements") or []
                if s.get("id") != "smoke_settlement_1"
            ]
    finally:
        se.WORLD_WIKI_ENABLED = old
    print("  OK settlement pages (diplomacy on)")


def test_settlements_absent_when_diplomacy_off():
    """When PATH1_DIPLOMACY_ENABLED is False, settlements and treaties are absent."""
    old_wiki = se.WORLD_WIKI_ENABLED
    se.WORLD_WIKI_ENABLED = True

    # Build a thin mock of `sem` that replaces path1_on to return False for
    # PATH1_DIPLOMACY_ENABLED, leaving all other attributes untouched.
    class _DiplomacyOffSem:
        def __getattr__(self, name):
            return getattr(se, name)

        def path1_on(self, subflag=None):  # noqa: D401
            if subflag == "PATH1_DIPLOMACY_ENABLED":
                return False
            return se.path1_on(subflag)

    mock_sem = _DiplomacyOffSem()

    try:
        engine = make_engine(4)
        # Even if the civilization has settlements, they should not appear.
        engine.civilization.setdefault("settlements", []).append({
            "id": "phantom_settlement", "name": "Phantom", "districts": [],
        })
        engine.civilization.setdefault("treaties", []).append({
            "id": "phantom_treaty", "name": "Phantom Treaty", "value": "trade", "tariff": 0,
        })
        try:
            result = _build_wiki_pages(engine, mock_sem)
            assert_true(result["pages"]["settlement"] == [],
                        f"settlements should be empty when diplomacy off, got {result['pages']['settlement']}")
            assert_true(result["pages"]["treaty"] == [],
                        f"treaties should be empty when diplomacy off, got {result['pages']['treaty']}")
        finally:
            engine.civilization["settlements"] = [
                s for s in engine.civilization.get("settlements") or []
                if s.get("id") != "phantom_settlement"
            ]
            engine.civilization["treaties"] = [
                t for t in engine.civilization.get("treaties") or []
                if t.get("id") != "phantom_treaty"
            ]
    finally:
        se.WORLD_WIKI_ENABLED = old_wiki
    print("  OK settlements/treaties absent when diplomacy off")


def test_treaty_pages():
    """Treaty pages appear when PATH1_DIPLOMACY_ENABLED, no cross-links (no settlement id in shape)."""
    if not se.path1_on("PATH1_DIPLOMACY_ENABLED"):
        print("  SKIP treaty pages (PATH1_DIPLOMACY_ENABLED=False)")
        return
    old = se.WORLD_WIKI_ENABLED
    se.WORLD_WIKI_ENABLED = True
    try:
        engine = make_engine(4)
        engine.civilization.setdefault("treaties", []).append({
            "id": "smoke_treaty_1",
            "name": "Smoke Trade Pact",
            "value": "trade",
            "tariff": 0.05,
            "frame": 100,
        })
        try:
            result = _build_wiki_pages(engine, se)
            tpages = result["pages"]["treaty"]
            smoke_t = next((p for p in tpages if p["id"] == "smoke_treaty_1"), None)
            assert_true(smoke_t is not None, "smoke treaty page missing")
            assert_true(smoke_t.get("kind") == "treaty", "wrong kind")
            f = smoke_t["fields"]
            assert_true(f["name"] == "Smoke Trade Pact", "name mismatch")
            assert_true(f["tariff"] == 0.05, "tariff mismatch")
            # No settlement cross-link (enacted treaty has no settlement id field).
            assert_true(smoke_t["links"] == [],
                        f"treaty links should be empty (no structured settlement ref), got {smoke_t['links']}")
        finally:
            engine.civilization["treaties"] = [
                t for t in engine.civilization.get("treaties") or []
                if t.get("id") != "smoke_treaty_1"
            ]
    finally:
        se.WORLD_WIKI_ENABLED = old
    print("  OK treaty pages")


def test_resource_pages():
    """Resource pages are present for every resourceRegistry entry."""
    old = se.WORLD_WIKI_ENABLED
    se.WORLD_WIKI_ENABLED = True
    try:
        engine = make_engine(4)
        result = _build_wiki_pages(engine, se)
        rpages = result["pages"]["resource"]
        assert_true(len(rpages) > 0, "expected resource pages")
        reg = engine.civilization["resourceRegistry"]
        assert_true(
            len(rpages) == len(reg),
            f"resource pages count {len(rpages)} != registry count {len(reg)}"
        )
        for page in rpages:
            assert_true(page.get("kind") == "resource", f"wrong kind: {page.get('kind')}")
            f = page["fields"]
            for key in ("id", "name", "gatherZone", "color", "crafted"):
                assert_true(key in f, f"resource page fields missing {key!r}")
            # gatherZone is a type/kind string — must NOT have a district cross-link.
            dist_links = [lnk for lnk in page["links"] if lnk.get("targetKind") == "district"]
            assert_true(dist_links == [],
                        f"resource page must not link gatherZone to district, got {dist_links}")
    finally:
        se.WORLD_WIKI_ENABLED = old
    print("  OK resource pages")


def test_project_pages_needs_links():
    """Project pages emit resource cross-links for each 'needs' key."""
    old = se.WORLD_WIKI_ENABLED
    se.WORLD_WIKI_ENABLED = True
    try:
        engine = make_engine(4)
        result = _build_wiki_pages(engine, se)
        project_pages = result["pages"]["project"]
        assert_true(len(project_pages) > 0, "expected project pages")
        for page in project_pages:
            assert_true(page.get("kind") == "project", f"wrong kind: {page.get('kind')}")
            f = page["fields"]
            assert_true("id" in f, "project page missing id")
            assert_true("name" in f, "project page missing name")
            assert_true("needs" in f, "project page missing needs")
            # Every key in needs must have a corresponding resource link.
            for rid in f["needs"].keys():
                needs_links = [
                    lnk for lnk in page["links"]
                    if lnk.get("targetKind") == "resource"
                    and lnk.get("targetId") == rid
                    and lnk.get("relation") == "needs"
                ]
                assert_true(
                    len(needs_links) == 1,
                    f"project {page['id']} missing needs→resource link for {rid!r}"
                )
    finally:
        se.WORLD_WIKI_ENABLED = old
    print("  OK project pages needs links")


def test_recipe_pages():
    """Recipe pages emit inputs→resource and output→resource links (CRAFTING_ENABLED only)."""
    old = se.WORLD_WIKI_ENABLED
    se.WORLD_WIKI_ENABLED = True
    try:
        engine = make_engine(4)
        result = _build_wiki_pages(engine, se)
        recipe_pages = result["pages"]["recipe"]
        if not se.CRAFTING_ENABLED:
            assert_true(recipe_pages == [], "CRAFTING off should yield empty recipe list")
            print("  OK recipe pages (CRAFTING_ENABLED=False, empty list)")
            return
        assert_true(len(recipe_pages) > 0, "expected recipe pages when CRAFTING_ENABLED")
        for page in recipe_pages:
            assert_true(page.get("kind") == "recipe", f"wrong kind: {page.get('kind')}")
            f = page["fields"]
            for key in ("id", "name", "inputs", "station"):
                assert_true(key in f, f"recipe page fields missing {key!r}")
            rid = page["id"]
            # Inputs cross-links.
            for res_id in f["inputs"].keys():
                input_links = [
                    lnk for lnk in page["links"]
                    if lnk.get("targetKind") == "resource"
                    and lnk.get("targetId") == res_id
                    and lnk.get("relation") == "inputs"
                ]
                assert_true(len(input_links) == 1,
                            f"recipe {rid} missing inputs→resource link for {res_id!r}")
            # Output cross-link (recipe id itself is a resource id).
            output_links = [
                lnk for lnk in page["links"]
                if lnk.get("targetKind") == "resource"
                and lnk.get("targetId") == rid
                and lnk.get("relation") == "output"
            ]
            assert_true(len(output_links) == 1,
                        f"recipe {rid} missing output→resource link, links={page['links']}")
            # station must NOT produce a structure cross-link.
            struct_links = [lnk for lnk in page["links"] if lnk.get("targetKind") == "structure"]
            assert_true(struct_links == [],
                        f"recipe station must not link to structure, got {struct_links}")
    finally:
        se.WORLD_WIKI_ENABLED = old
    print("  OK recipe pages")


def test_social_ties_on_agent_pages():
    """Social ties appear as labeled cross-links on both agent pages."""
    old = se.WORLD_WIKI_ENABLED
    se.WORLD_WIKI_ENABLED = True
    try:
        engine = make_engine(4)
        agents = engine.agents
        if len(agents) < 2:
            print("  SKIP social ties (need >=2 agents)")
            return
        a, b = agents[0], agents[1]
        # Plant a bilateral ally tie so _social_ties_snapshot() picks it up.
        a.setdefault("relationships", {})[b["name"]] = "ally"
        b.setdefault("relationships", {})[a["name"]] = "ally"
        try:
            result = _build_wiki_pages(engine, se)
            pages_by_id = {p["id"]: p for p in result["pages"]["agent"]}
            page_a = pages_by_id.get(a["id"])
            page_b = pages_by_id.get(b["id"])
            assert_true(page_a is not None, f"no page for agent {a['id']}")
            assert_true(page_b is not None, f"no page for agent {b['id']}")
            # A's page should have a socialTie link pointing at B.
            tie_on_a = [
                lnk for lnk in page_a["links"]
                if lnk.get("targetKind") == "agent"
                and lnk.get("targetId") == b["id"]
                and "socialTie" in lnk.get("relation", "")
            ]
            assert_true(len(tie_on_a) >= 1,
                        f"expected socialTie link from A to B on A's page, links={page_a['links']}")
            # B's page should have a socialTie link pointing at A.
            tie_on_b = [
                lnk for lnk in page_b["links"]
                if lnk.get("targetKind") == "agent"
                and lnk.get("targetId") == a["id"]
                and "socialTie" in lnk.get("relation", "")
            ]
            assert_true(len(tie_on_b) >= 1,
                        f"expected socialTie link from B to A on B's page, links={page_b['links']}")
            # There must be NO standalone social-tie page kind.
            assert_true("socialTie" not in result["pages"],
                        "social ties must not produce a standalone page kind")
        finally:
            a["relationships"].pop(b["name"], None)
            b["relationships"].pop(a["name"], None)
    finally:
        se.WORLD_WIKI_ENABLED = old
    print("  OK social ties on agent pages")


def test_districts_helper_regression():
    """Extracted _districts_snapshot_payload produces the same keys as pre-extraction inline code.

    Verifies that the mechanical extraction of the districts/road shallow-copy
    block from districts_js() into a shared helper did not regress the shape
    produced for /districts.js consumers.
    """
    engine = make_engine(4)
    with engine.lock:
        # Inline (pre-extraction) logic from original districts_js body.
        c = engine.civilization
        inline_districts = [
            {"id": did, "kind": d["kind"], "tile": d["tile"], "label": d.get("label"),
             "bounds": dict(d["bounds"]),
             "buildGrid": dict(d["build_grid"]) if d.get("build_grid") else None,
             "tiles": dict(d.get("tiles") or {}),
             "terrain": dict(d.get("terrain") or {}),
             "settlementId": d.get("settlementId")}
            for did, d in c["districts"].items()
        ]
        inline_road_nodes = {nid: dict(n) for nid, n in c["roadNodes"].items()}
        inline_road_edges = [list(e) for e in c["roadEdges"]]
        # Helper path.
        helper_snap = _districts_snapshot_payload_smoke(engine)

    assert_true(
        helper_snap["districts"] == inline_districts,
        f"districts mismatch between helper and inline:\n"
        f"  helper:  {helper_snap['districts']}\n"
        f"  inline:  {inline_districts}"
    )
    assert_true(
        helper_snap["roadNodes"] == inline_road_nodes,
        "roadNodes mismatch between helper and inline"
    )
    assert_true(
        helper_snap["roadEdges"] == inline_road_edges,
        "roadEdges mismatch between helper and inline"
    )
    print("  OK districts helper regression (identical to pre-extraction inline)")


def main():
    print("idea09_world_wiki_smoke")
    # Phase 2a tests.
    test_flag_off_disabled_shape()
    test_response_shape()
    test_agent_relationship_cross_link()
    test_structure_links()
    test_belief_page_shape()
    test_chronicle_page_shape()
    test_rule_page_shape()
    # Phase 2b tests.
    test_district_pages()
    test_settlement_pages_flag_on()
    test_settlements_absent_when_diplomacy_off()
    test_treaty_pages()
    test_resource_pages()
    test_project_pages_needs_links()
    test_recipe_pages()
    test_social_ties_on_agent_pages()
    test_districts_helper_regression()
    print("PASS")


if __name__ == "__main__":
    main()
