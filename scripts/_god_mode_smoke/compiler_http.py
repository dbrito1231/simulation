# Sovereign God mode Optional Phase 8 (free-prose compiler) plus the Phase 2+3+4+5+6+8 HTTP layer smoke (real server.py app).
# Split out of the original monolithic scripts/god_mode_smoke.py (pure move,
# no behavior change).
from _god_mode_smoke.helpers import *  # noqa: F401,F403


def test_compiler_dual_gate_rejects_when_disabled():
    old_compiler = se.GOD_COMPILER_ENABLED
    old_mode = se.GOD_MODE_ENABLED
    try:
        se.GOD_MODE_ENABLED = True
        se.GOD_COMPILER_ENABLED = False
        engine = _compiler_engine()
        engine.d["lm_complete"] = lambda *a, **k: _valid_compiler_json()
        resp = engine.god_compile_prose("The river runs dark.")
        assert_true(resp["compileOk"] is False, resp)
        assert_true("disabled" in resp["reason"], resp)
        assert_true(engine._god_preview_cache == {}, "a disabled compiler must never populate the preview cache")

        # GOD_MODE_ENABLED off but GOD_COMPILER_ENABLED on: still rejected --
        # this is a genuine DUAL gate, neither flag alone is sufficient.
        se.GOD_MODE_ENABLED = False
        se.GOD_COMPILER_ENABLED = True
        resp = engine.god_compile_prose("The river runs dark.")
        assert_true(resp["compileOk"] is False, resp)
        print("  OK compiler dual-gate: GOD_COMPILER_ENABLED=False rejects cleanly; "
              "GOD_MODE_ENABLED=False rejects cleanly even with the compiler flag on")
    finally:
        se.GOD_COMPILER_ENABLED = old_compiler
        se.GOD_MODE_ENABLED = old_mode


def test_compiler_rate_limit():
    old_compiler, old_mode = se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED
    try:
        se.GOD_MODE_ENABLED = True
        se.GOD_COMPILER_ENABLED = True
        engine = _compiler_engine()
        engine.d["lm_complete"] = lambda *a, **k: _valid_compiler_json()
        first = engine.god_compile_prose("The river runs dark.")
        assert_true(first["compileOk"] is True, first)
        second = engine.god_compile_prose("A second decree, immediately after.")
        assert_true(second["compileOk"] is False and "rate limited" in second["reason"], second)
        assert_true(engine._god_compiler_state["compileCount"] == 1,
                    "a rate-limited call must not bump compileCount")
        print("  OK a second compile within GOD_COMPILER_MIN_INTERVAL_SEC is rejected as rate-limited")
    finally:
        se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED = old_compiler, old_mode


def test_compiler_session_cap():
    old_compiler, old_mode = se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED
    old_interval = se.GOD_COMPILER_MIN_INTERVAL_SEC
    try:
        se.GOD_MODE_ENABLED = True
        se.GOD_COMPILER_ENABLED = True
        se.GOD_COMPILER_MIN_INTERVAL_SEC = 0.0  # isolate the session cap from the interval check
        engine = _compiler_engine()
        # Every call fails to parse -- proves the cap counts FAILURES too.
        engine.d["lm_complete"] = lambda *a, **k: "not json at all"
        for i in range(se.GOD_COMPILER_SESSION_CAP):
            resp = engine.god_compile_prose(f"decree {i}")
            assert_true(resp["compileOk"] is False, resp)
        assert_true(engine._god_compiler_state["compileCount"] == se.GOD_COMPILER_SESSION_CAP,
                    engine._god_compiler_state)
        over_budget = engine.god_compile_prose("one more, over budget")
        assert_true(over_budget["compileOk"] is False and "session cap" in over_budget["reason"], over_budget)
        assert_true(engine._god_compiler_state["compileCount"] == se.GOD_COMPILER_SESSION_CAP,
                    "an over-budget rejection must NOT bump compileCount further")
        print(f"  OK compileCount increments on every failed compile too; "
              f"at {se.GOD_COMPILER_SESSION_CAP} further compiles reject cleanly")
    finally:
        se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED = old_compiler, old_mode
        se.GOD_COMPILER_MIN_INTERVAL_SEC = old_interval


def test_compiler_successful_compile_produces_applyable_preview():
    old_compiler, old_mode = se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED
    try:
        se.GOD_MODE_ENABLED = True
        se.GOD_COMPILER_ENABLED = True
        engine = _compiler_engine()
        engine.d["lm_complete"] = lambda *a, **k: _valid_compiler_json()
        resp = engine.god_compile_prose("The river runs dark for three days and the fish flee.")
        assert_true(resp["compileOk"] is True, resp)
        assert_true(resp["previewId"] in engine._god_preview_cache, "compile must populate _god_preview_cache")
        assert_true(resp["normalizedCommand"]["kind"] == "story_event", resp)
        assert_true(resp["normalizedCommand"]["payload"]["modifiers"]["fish_yield_multiplier"] == 0.1, resp)
        # The compiler NEVER applies -- intervened must still be False.
        assert_true(engine.civilization["godState"]["intervened"] is False,
                    "god_compile_prose must never mutate world state")
        applied = engine.god_apply(resp["previewId"], "compiler-smoke-apply-1")
        assert_true(applied["ok"], applied)
        assert_true(engine.civilization["godState"]["intervened"] is True,
                    "the OPERATOR's own god_apply call is what mutates state, never the compiler")
        print("  OK a successful compile produces a real _god_preview_cache entry, applyable via normal god_apply, "
              "and never mutates state itself")
    finally:
        se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED = old_compiler, old_mode


def test_compiler_model_shape_mismatch_rejected():
    old_compiler, old_mode = se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED
    try:
        se.GOD_MODE_ENABLED = True
        se.GOD_COMPILER_ENABLED = True
        engine = _compiler_engine()
        engine.d["lm_complete"] = lambda *a, **k: json.dumps({"kind": "not_a_story"})
        resp = engine.god_compile_prose("The river runs dark.")
        assert_true(resp["compileOk"] is False, resp)
        assert_true("story_event" in resp["reason"], resp)
        assert_true(engine._god_preview_cache == {}, "a shape-mismatched draft must never reach the preview cache")
        print("  OK a model response with the wrong 'kind' is rejected BEFORE reaching the preview cache")
    finally:
        se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED = old_compiler, old_mode


def test_compiler_unknown_modifier_key_rejected():
    old_compiler, old_mode = se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED
    try:
        se.GOD_MODE_ENABLED = True
        se.GOD_COMPILER_ENABLED = True
        engine = _compiler_engine()
        bogus = json.dumps({
            "kind": "story_event",
            "payload": {
                "title": "Bogus Decree", "narration": "Something happens.",
                "visibility": "public", "modifiers": {"bogus_multiplier": 1.0},
            },
        })
        engine.d["lm_complete"] = lambda *a, **k: bogus
        resp = engine.god_compile_prose("An impossible decree.")
        assert_true(resp["compileOk"] is False, resp)
        assert_true("bogus_multiplier" in resp["reason"], resp)
        assert_true(engine._god_preview_cache == {}, "an unknown-key draft must never reach the preview cache")
        print("  OK an unknown modifier key from the model is rejected, naming the offending field, "
              "never entering the preview cache")
    finally:
        se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED = old_compiler, old_mode


def test_compiler_non_json_response_rejected():
    old_compiler, old_mode = se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED
    try:
        se.GOD_MODE_ENABLED = True
        se.GOD_COMPILER_ENABLED = True
        engine = _compiler_engine()
        engine.d["lm_complete"] = lambda *a, **k: "Certainly! Here is a story about a river: " + "x" * 300
        resp = engine.god_compile_prose("The river runs dark.")
        assert_true(resp["compileOk"] is False, resp)
        assert_true("JSON" in resp["reason"], resp)
        assert_true(len(resp["reason"]) < 300, "the raw output must be TRUNCATED in the rejection reason")
        print("  OK a non-JSON model response is rejected with a truncated raw-output reason")
    finally:
        se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED = old_compiler, old_mode


def test_compiler_timeout_handled_cleanly():
    old_compiler, old_mode = se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED
    try:
        se.GOD_MODE_ENABLED = True
        se.GOD_COMPILER_ENABLED = True
        engine = _compiler_engine()

        def _raise_timeout(*a, **k):
            raise TimeoutError("simulated Ollama timeout")
        engine.d["lm_complete"] = _raise_timeout
        resp = engine.god_compile_prose("The river runs dark.")
        assert_true(resp["compileOk"] is False, resp)
        assert_true(engine._god_compiler_state["compileCount"] == 1,
                    "a timeout must still bump compileCount (docs/plan: regardless of success)")
        print("  OK a simulated lm_complete timeout returns a clean rejection, not an unhandled exception, "
              "and still counts against the session cap")
    finally:
        se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED = old_compiler, old_mode


def test_compiler_state_not_persisted_across_restore():
    old_compiler, old_mode = se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED
    old_db_path = se.DB_PATH
    tmpdir = tempfile.mkdtemp()
    tmp_db = str(Path(tmpdir) / "state_compiler_roundtrip.db")
    try:
        se.GOD_MODE_ENABLED = True
        se.GOD_COMPILER_ENABLED = True
        engine = _compiler_engine()
        engine.d["lm_complete"] = lambda *a, **k: _valid_compiler_json()
        resp = engine.god_compile_prose("The river runs dark.")
        assert_true(resp["compileOk"] is True, resp)
        assert_true(engine._god_compiler_state["compileCount"] == 1, engine._god_compiler_state)
        assert_true(engine._god_compiler_state["lastCompileWallTime"] > 0, engine._god_compiler_state)

        se.DB_PATH = tmp_db
        assert_true(engine.save_state(), "save_state failed")

        restored = make_engine()
        assert_true(restored.restore_state(), "restore_state failed against the just-written db")
        assert_true(restored._god_compiler_state == {"lastCompileWallTime": 0.0, "compileCount": 0},
                    restored._god_compiler_state)
        print("  OK compileCount/lastCompileWallTime do NOT survive save/restore -- in-memory only")
    finally:
        se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED = old_compiler, old_mode
        se.DB_PATH = old_db_path


def test_compiler_token_never_in_prompt_or_state():
    """The compiler method has no parameter for the token and never reads
    os.environ -- proven here by confirming TEST_TOKEN never appears in the
    prompt text lm_complete receives, nor in any compiler response field."""
    old_compiler, old_mode = se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED
    try:
        se.GOD_MODE_ENABLED = True
        se.GOD_COMPILER_ENABLED = True
        engine = _compiler_engine()
        seen_prompts = []

        def _capture(system_prompt, user_prompt, *a, **k):
            seen_prompts.append(system_prompt)
            seen_prompts.append(user_prompt)
            return _valid_compiler_json()
        engine.d["lm_complete"] = _capture
        resp = engine.god_compile_prose("The river runs dark.")
        assert_true(resp["compileOk"] is True, resp)
        assert_true(all(TEST_TOKEN not in p for p in seen_prompts),
                    "the God token must never reach the compiler prompt")
        assert_true(TEST_TOKEN not in json.dumps(resp), "the God token must never appear in the compile response")
        print("  OK the compiler prompt and response never contain the God token "
              "(god_compile_prose has no parameter for it)")
    finally:
        se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED = old_compiler, old_mode


# --- HTTP-layer tests (real server.py app + engine, imported once with the
# module-level env vars above; never persisted to the real state.db) ---

def run_http_tests():
    import server  # noqa: E402  (heavy import: real engine, real state.db READ only)

    assert_true(server.GOD_TOKEN == TEST_TOKEN, "server did not pick up SIM_GOD_TOKEN at import")
    assert_true(server.GOD_ROUTES_ACTIVE is True,
                "server did not activate god routes with flag+token+auth all set")

    client = server.app.test_client()
    headers_ok = {"X-God-Token": TEST_TOKEN}
    headers_wrong = {"X-God-Token": "not-the-token"}

    resp = client.get("/control/god/capabilities", headers=headers_ok)
    assert_true(resp.status_code == 200, resp.status_code)
    body = resp.get_json()
    assert_true(body["ok"] and body["godModeEnabled"] and body["tokenConfigured"], body)
    assert_true(TEST_TOKEN not in resp.get_data(as_text=True), "token leaked into a response body")
    print("  OK GET /control/god/capabilities with a valid token")

    for label, headers in (("no header", {}), ("wrong token", headers_wrong)):
        resp = client.get("/control/god/capabilities", headers=headers)
        assert_true(resp.status_code == 401, (label, resp.status_code))
        assert_true(resp.get_json() == {"error": "unauthorized"}, (label, resp.get_json()))
    print("  OK missing/wrong token -> uniform 401 {'error': 'unauthorized'} (auth required)")

    # When GOD_AUTH_REQUIRED is False, tokenless requests succeed.
    old_auth = se.GOD_AUTH_REQUIRED
    se.GOD_AUTH_REQUIRED = False
    try:
        resp = client.get("/control/god/capabilities")
        assert_true(resp.status_code == 200 and resp.get_json()["ok"], resp.get_json())
        print("  OK GOD_AUTH_REQUIRED off -> tokenless GET /control/god/capabilities returns 200")
    finally:
        se.GOD_AUTH_REQUIRED = old_auth

    # Simulate "flag on, auth required, token unset" the same way a real
    # misconfigured startup would compute it -- monkeypatch the already-imported
    # module's plain globals (server.py reads GOD_TOKEN/GOD_ROUTES_ACTIVE once at
    # import, exactly like a real restart would) rather than re-importing.
    old_token, old_active = server.GOD_TOKEN, server.GOD_ROUTES_ACTIVE
    server.GOD_TOKEN, server.GOD_ROUTES_ACTIVE = "", False
    try:
        resp = client.get("/control/god/capabilities", headers=headers_ok)
        assert_true(resp.status_code == 401 and resp.get_json() == {"error": "unauthorized"},
                    (resp.status_code, resp.get_json()))
        print("  OK flag-on-with-auth-required-and-no-token leaves routes disabled "
              "even with a well-formed header")
    finally:
        server.GOD_TOKEN, server.GOD_ROUTES_ACTIVE = old_token, old_active

    # Request-body size limit.
    oversized = b"{\"kind\": \"proclamation\", \"payload\": {\"text\": \"" + b"a" * 9000 + b"\"}}"
    resp = client.post("/control/god/preview", data=oversized,
                       content_type="application/json", headers=headers_ok)
    assert_true(resp.status_code == 413, resp.status_code)
    print("  OK oversized request body -> 413, never reaches JSON parsing")

    # Full preview -> apply -> sight -> cancel flow through the real routes,
    # against the real (but never-persisted-by-this-script) server.engine.
    before_intervened = server.engine.civilization["godState"]["intervened"]
    resp = client.post("/control/god/preview", json=_proclamation_envelope(
        "The Divine Console smoke test speaks."), headers=headers_ok)
    assert_true(resp.status_code == 200, resp.status_code)
    preview_body = resp.get_json()
    assert_true(preview_body["ok"], preview_body)

    resp = client.post("/control/god/apply",
                       json={"previewId": preview_body["previewId"], "requestId": "http-smoke-1"},
                       headers=headers_ok)
    assert_true(resp.status_code == 200, resp.status_code)
    apply_body = resp.get_json()
    assert_true(apply_body["ok"], apply_body)
    assert_true(TEST_TOKEN not in resp.get_data(as_text=True), "token leaked into an apply response")
    assert_true(server.engine.civilization["godState"]["intervened"] is True,
                "HTTP apply did not mutate the real in-process engine")

    resp = client.get("/control/god/sight", headers=headers_ok)
    assert_true(resp.status_code == 200 and resp.get_json()["ok"], resp.get_json())

    resp = client.post("/control/god/cancel", json={"targetId": "none"}, headers=headers_ok)
    assert_true(resp.status_code == 200 and resp.get_json()["cancelled"] is False, resp.get_json())
    print(f"  OK full HTTP preview->apply->sight->cancel flow "
          f"(intervened {before_intervened} -> True)")

    # /state must carry config.flags.GOD_MODE_ENABLED and a 'god' key, and
    # the token must never appear in the raw snapshot dump.
    snapshot_dump = json.dumps(server.engine.snapshot())
    assert_true(TEST_TOKEN not in snapshot_dump, "token leaked into /state")
    snap = server.engine.snapshot()
    assert_true(snap["config"]["flags"]["GOD_MODE_ENABLED"] is True, snap["config"]["flags"])
    assert_true("god" in snap and "intervened" in snap["god"], snap.get("god"))
    print("  OK /state exposes GOD_MODE_ENABLED + a bounded 'god' key, never the token")

    # divine.jsonl got a record for the applied proclamation, and never
    # contains the token.
    with open(server.session_logger.divine_path, encoding="utf-8") as fh:
        lines = [json.loads(ln) for ln in fh if ln.strip()]
    applied_records = [r for r in lines if r.get("status") == "applied"]
    assert_true(applied_records, "no 'applied' record written to divine.jsonl")
    divine_text = open(server.session_logger.divine_path, encoding="utf-8").read()
    assert_true(TEST_TOKEN not in divine_text, "token leaked into divine.jsonl")
    assert_true("http-smoke-1" not in divine_text, "raw requestId was logged instead of a hash")
    print(f"  OK divine.jsonl recorded {len(applied_records)} applied record(s), "
          f"no token/raw-requestId leakage")

    # Phase 3 over HTTP: providence appears in /state's god key; a private
    # omen never does, through the real routes (not just the engine directly).
    resp = client.post("/control/god/preview", json=_providence_envelope(
        "The HTTP smoke providence speaks.", duration=1000), headers=headers_ok)
    preview_body = resp.get_json()
    assert_true(preview_body["ok"], preview_body)
    resp = client.post("/control/god/apply",
                       json={"previewId": preview_body["previewId"], "requestId": "http-smoke-providence"},
                       headers=headers_ok)
    assert_true(resp.get_json()["ok"], resp.get_json())
    snap = server.engine.snapshot()
    assert_true(snap["god"]["providence"]["text"] == "The HTTP smoke providence speaks.", snap["god"])

    real_target_id = server.engine.agents[0]["id"]
    resp = client.post("/control/god/preview", json=_omen_envelope(
        real_target_id, "The HTTP smoke omen -- must stay private.", duration=1000), headers=headers_ok)
    preview_body = resp.get_json()
    assert_true(preview_body["ok"], preview_body)
    resp = client.post("/control/god/apply",
                       json={"previewId": preview_body["previewId"], "requestId": "http-smoke-omen"},
                       headers=headers_ok)
    assert_true(resp.get_json()["ok"], resp.get_json())
    state_dump = json.dumps(server.engine.snapshot())
    assert_true("must stay private" not in state_dump, "private omen leaked into /state over HTTP")
    resp = client.get("/control/god/sight", headers=headers_ok)
    assert_true("must stay private" in resp.get_data(as_text=True),
                "sight should expose the omen (authenticated route)")
    # Revoke it so this HTTP smoke run leaves no lingering guidance behind in
    # the real in-process engine for whatever runs next.
    revoke_id = None
    for a in server.engine.god_sight()["agents"]:
        if a["id"] == real_target_id and a.get("omen"):
            revoke_id = server.engine.civilization["godState"]["privateOmens"][str(real_target_id)]["id"]
    if revoke_id:
        resp = client.post("/control/god/preview", json=_revoke_envelope(revoke_id), headers=headers_ok)
        preview_body = resp.get_json()
        client.post("/control/god/apply",
                   json={"previewId": preview_body["previewId"], "requestId": "http-smoke-revoke-cleanup"},
                   headers=headers_ok)
    print("  OK providence appears in /state's 'god' key over HTTP; "
          "a private omen never does, only through /control/god/sight")

    # Optional Phase 8 over HTTP: capabilities.compiler.enabled reflects the
    # dual gate; the /control/god/compile route works end to end when both
    # flags are on, and compiler.jsonl never contains the token.
    resp = client.get("/control/god/capabilities", headers=headers_ok)
    caps = resp.get_json()
    assert_true("compiler" in caps and caps["compiler"]["enabled"] is False,
                "capabilities.compiler.enabled must be False while GOD_COMPILER_ENABLED is off")

    old_compiler_flag = se.GOD_COMPILER_ENABLED
    se.GOD_COMPILER_ENABLED = True
    old_lm_complete = server.engine.d.get("lm_complete")
    try:
        resp = client.get("/control/god/capabilities", headers=headers_ok)
        caps = resp.get_json()
        assert_true(caps["compiler"]["enabled"] is True,
                    "capabilities.compiler.enabled must be True once GOD_COMPILER_ENABLED is on")

        server.engine.d["lm_complete"] = lambda *a, **k: _valid_compiler_json()
        resp = client.post("/control/god/compile", json={"prose": "The river runs dark for three days."},
                           headers=headers_ok)
        assert_true(resp.status_code == 200, resp.status_code)
        compile_body = resp.get_json()
        assert_true(compile_body.get("compileOk") is True, compile_body)
        assert_true(TEST_TOKEN not in resp.get_data(as_text=True), "token leaked into a compile response")
        print("  OK GET /control/god/capabilities reflects the compiler dual gate; "
              "POST /control/god/compile produces a real previewable draft")

        # Unauthorized compile attempts get the same uniform 401.
        resp = client.post("/control/god/compile", json={"prose": "x"}, headers={})
        assert_true(resp.status_code == 401 and resp.get_json() == {"error": "unauthorized"}, resp.get_json())
        print("  OK POST /control/god/compile without a valid token -> uniform 401")

        with open(server.session_logger.compiler_path, encoding="utf-8") as fh:
            compiler_lines = [json.loads(ln) for ln in fh if ln.strip()]
        assert_true(compiler_lines, "no record written to compiler.jsonl")
        compiler_text = open(server.session_logger.compiler_path, encoding="utf-8").read()
        assert_true(TEST_TOKEN not in compiler_text, "token leaked into compiler.jsonl")
        draft_records = [r for r in compiler_lines if r.get("status") == "draft"]
        assert_true(draft_records, "no 'draft' record written to compiler.jsonl")
        print(f"  OK compiler.jsonl recorded {len(compiler_lines)} record(s) "
              f"({len(draft_records)} draft), no token leakage")
    finally:
        se.GOD_COMPILER_ENABLED = old_compiler_flag
        if old_lm_complete is not None:
            server.engine.d["lm_complete"] = old_lm_complete

    # /control/reset password gate (never calls the real engine.reset()).
    assert_true(server.RESET_PASSWORD == "reset",
                "expected default RESET_PASSWORD when SIM_RESET_PASSWORD unset")
    tick_before = server.engine.frameTick
    reset_calls = []
    orig_reset = server.engine.reset

    def _track_reset(roster_size=None):
        reset_calls.append(roster_size)
        return None

    server.engine.reset = _track_reset
    try:
        resp = client.post("/control/reset", json={"password": "wrong-password"})
        assert_true(resp.status_code == 401, resp.status_code)
        assert_true(resp.get_json() == {"ok": False, "error": "unauthorized"}, resp.get_json())
        assert_true(not reset_calls, "wrong password must not call engine.reset()")
        assert_true(server.engine.frameTick == tick_before, "world unchanged on wrong password")

        resp = client.post("/control/reset", json={})
        assert_true(resp.status_code == 401 and not reset_calls,
                    (resp.status_code, reset_calls))

        resp = client.post("/control/reset",
                           json={"password": server.RESET_PASSWORD, "agents": 10})
        assert_true(resp.status_code == 200, resp.status_code)
        body = resp.get_json()
        assert_true(body.get("ok") is True, body)
        assert_true(reset_calls == [10], reset_calls)
    finally:
        server.engine.reset = orig_reset
    print("  OK POST /control/reset password gate (wrong/missing -> 401, correct -> reset)")
