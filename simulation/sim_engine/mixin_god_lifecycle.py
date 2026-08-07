"""Phase 6g mixin: Sovereign God mode preview cache / idempotency / guidance
closure / expiry / free-prose compiler slice of SimEngine.

Extracted unchanged (pure move, no behavior change) from core.py's SimEngine
class body — the contiguous method range from `_god_preview_evict_expired`
through `god_compile_prose` (formerly core.py lines ~2137-2599). Covers: the
in-memory bounded preview cache (`_god_preview_evict_expired`,
`_god_preview_insert`), the in-memory bounded idempotency store
(`_god_requests_evict`, `_hash_request_id`, `_log_divine`), guidance closure
for providence/private-omen (`_close_providence`, `_close_omen`), the
divine-effect and bargain expiry sweeps (`_expire_divine_effects`,
`_tick_divine_bargains`), and the Sovereign God mode Optional Phase 8
free-prose compiler (`_god_compiler_prompt`, `_god_compiler_call_model`,
`_god_compiler_parse`, `_log_compiler`, `god_compile_prose`).

Exec-loaded into the shared package namespace — see simulation/sim_engine/__init__.py.
"""

# NOTE: constants.py/persistence.py/helpers.py names are NOT imported here.
# They live in the exec()-shared namespace — see simulation/sim_engine/__init__.py.


class _GodLifecycleMixin:
    """Mixin slice of SimEngine: Sovereign God mode preview cache,
    idempotency store, guidance closure, divine-effect/bargain expiry, and
    the Optional Phase 8 free-prose compiler. See module docstring for exact
    scope."""

    # --- preview cache (in-memory, bounded, never persisted) ---
    def _god_preview_evict_expired(self):
        now = time.time()
        expired = [pid for pid, rec in self._god_preview_cache.items()
                  if rec["expiresAt"] <= now]
        for pid in expired:
            del self._god_preview_cache[pid]

    def _god_preview_insert(self, record):
        self._god_preview_evict_expired()
        while len(self._god_preview_cache) >= GOD_PREVIEW_CACHE_MAX:
            oldest_id = min(self._god_preview_cache,
                            key=lambda pid: self._god_preview_cache[pid]["createdAt"])
            del self._god_preview_cache[oldest_id]
        self._god_preview_cache[record["previewId"]] = record

    # --- idempotency store (in-memory, bounded, never persisted) ---
    def _god_requests_evict(self):
        while len(self._god_requests) > GOD_REQUEST_CACHE_MAX:
            oldest_id = min(self._god_requests,
                            key=lambda rid: self._god_requests[rid]["createdAt"])
            del self._god_requests[oldest_id]

    def _hash_request_id(self, request_id):
        """Never log a raw client-supplied requestId verbatim (docs/archive/plan-sovereign-god-mode-v2.md
        Logging section: "Hash or redact request_id")."""
        if not request_id:
            return None
        return hashlib.sha256(str(request_id).encode("utf-8")).hexdigest()[:16]

    def _log_divine(self, intervention_id, request_id, kind, normalized_command,
                    outcome, status, public):
        log_fn = self.d.get("log_divine")
        if not log_fn:
            return
        try:
            log_fn(
                intervention_id=intervention_id,
                request_id=self._hash_request_id(request_id),
                frame_tick=self.frameTick,
                kind=kind,
                normalized_command=normalized_command,
                outcome=outcome,
                status=status,
                public=public,
            )
        except Exception:
            pass  # logging must never break the simulation

    # --- guidance closure (Phase 3: providence + private omens) ---
    def _close_providence(self, status):
        """Clears the single active providence slot exactly once, emitting
        one audit record (docs/archive/plan-sovereign-god-mode-v2.md "Expiry ownership" + "Cancellable"
        reversibility). Returns the closed record, or None if none was
        active. Must be called with self.lock already held. Providence
        carries no memory-write contract -- only private omens do."""
        god = self.civilization.get("godState")
        if not isinstance(god, dict):
            return None
        prov = god.get("providence")
        if not isinstance(prov, dict):
            return None
        god["providence"] = None
        self._log_divine(prov.get("id"), None, "providence", prov,
                         {"status": status}, status, public=True)
        return prov

    def _close_omen(self, key, status):
        """Clears one private omen (keyed by str(agent id)) exactly once.
        Writes the omen's text into the target's ordinary memory EXACTLY
        ONCE via _push_memory(..., kind="divine_omen") -- guarded by the
        record's own `memoryWritten` flag so expiry, revocation, and
        replacement can never double-fire it, and restore-time re-closure of
        an already-closed omen (memoryWritten already True, or the key
        already gone) is a safe no-op (docs/archive/plan-sovereign-god-mode-v2.md Phase 3 memory contract).
        Must be called with self.lock already held."""
        god = self.civilization.get("godState")
        if not isinstance(god, dict):
            return None
        omens = god.get("privateOmens")
        omen = omens.get(key) if isinstance(omens, dict) else None
        if not isinstance(omen, dict):
            return None
        if not omen.get("memoryWritten"):
            agent = self._find_agent_by_id(omen.get("targetId"))
            if agent is not None:
                self._push_memory(agent, omen.get("text", ""), kind="divine_omen")
            omen["memoryWritten"] = True
        del omens[key]
        self._log_divine(omen.get("id"), None, "private_omen", omen,
                         {"status": status}, status, public=False)
        self._sweep_whisper_campaigns()
        return omen

    # --- expiry (rule: "Expiry ownership") ---
    def _expire_divine_effects(self, restore=False):
        """Bounded scan: marks newly expired activeEvents (Phase 5 payload,
        empty today), providence, and every private omen exactly once each,
        leaving already-closed entries untouched. Called every tick
        immediately after frameTick advances (_tick_once) and once more
        after restore rehydration. Must be called with self.lock already
        held."""
        god = self.civilization.get("godState")
        if not isinstance(god, dict):
            return
        ft = self.frameTick
        events = god.get("activeEvents")
        if isinstance(events, list) and events:
            for event in events[:GOD_ACTIVE_EVENTS_CAP]:
                if not isinstance(event, dict) or event.get("status") != "active":
                    continue
                expires_frame = event.get("expiresFrame")
                if isinstance(expires_frame, int) and ft >= expires_frame:
                    status = "restore-closed" if restore else "expired"
                    if event.get("kind") == "weather_override":
                        # Phase 6: closes the override and hands off to the
                        # natural cycle's successor state via the SAME path
                        # cancel uses (_close_weather_override) -- never the
                        # story_event closer, which has no weather semantics.
                        self._close_weather_override(event, status)
                        if not restore:
                            self._push_activity(
                                "A divine weather override ends -- the sky "
                                "returns to its natural course.")
                    else:
                        # Shared with god_cancel's story_event branch: closes the
                        # event exactly once and, if it still owns the current
                        # providence slot, closes that too through the SAME
                        # _close_providence path (docs/archive/plan-sovereign-god-mode-v2.md Phase 5: expiry
                        # closes every sub-effect of an event exactly once).
                        self._close_story_event(event, status)
                        if not restore and event.get("visibility", "public") == "public":
                            self._push_activity(f'A divine story fades: "{event.get("title")}"')

        prov = god.get("providence")
        if isinstance(prov, dict):
            expires_frame = prov.get("expiresFrame")
            if isinstance(expires_frame, int) and ft >= expires_frame:
                status = "restore-closed" if restore else "expired"
                self._close_providence(status)
                if not restore:
                    self._push_activity("A divine providence fades from the village's thoughts.")

        omens = god.get("privateOmens")
        if isinstance(omens, dict) and omens:
            for key in list(omens.keys()):
                omen = omens.get(key)
                if not isinstance(omen, dict):
                    continue
                expires_frame = omen.get("expiresFrame")
                if isinstance(expires_frame, int) and ft >= expires_frame:
                    status = "restore-closed" if restore else "expired"
                    self._close_omen(key, status)

        self._sweep_whisper_campaigns(restore=restore)

        sampling = god.get("agentSampling")
        if isinstance(sampling, dict) and sampling:
            for key in list(sampling.keys()):
                rec = sampling.get(key)
                if not isinstance(rec, dict):
                    continue
                expires_frame = rec.get("expiresFrame")
                if isinstance(expires_frame, int) and ft >= expires_frame:
                    status = "restore-closed" if restore else "expired"
                    self._close_agent_sampling(key, status)

        masks = god.get("contextMasks")
        if isinstance(masks, dict) and masks:
            for key in list(masks.keys()):
                rec = masks.get(key)
                if not isinstance(rec, dict):
                    continue
                expires_frame = rec.get("expiresFrame")
                if isinstance(expires_frame, int) and ft >= expires_frame:
                    status = "restore-closed" if restore else "expired"
                    self._close_context_mask(key, status)

        self._expire_decision_gates(restore=restore)
        self._tick_divine_bargains(restore=restore)

        anointments = god.get("anointments")
        if isinstance(anointments, dict) and anointments:
            for key in list(anointments.keys()):
                rec = anointments.get(key)
                if not isinstance(rec, dict):
                    continue
                expires_frame = rec.get("expiresFrame")
                if isinstance(expires_frame, int) and ft >= expires_frame:
                    status = "restore-closed" if restore else "expired"
                    self._close_anointment(key, status)

        forges = god.get("identityForges")
        if isinstance(forges, dict) and forges:
            for key in list(forges.keys()):
                rec = forges.get(key)
                if not isinstance(rec, dict):
                    continue
                expires_frame = rec.get("expiresFrame")
                if isinstance(expires_frame, int) and ft >= expires_frame:
                    status = "restore-closed" if restore else "expired"
                    self._close_identity_forge(key, status)

        zones = god.get("architectZones")
        if isinstance(zones, list) and zones:
            for zone in list(zones):
                if not isinstance(zone, dict) or zone.get("status") != "active":
                    continue
                expires_frame = zone.get("expiresFrame")
                if isinstance(expires_frame, int) and ft >= expires_frame:
                    status = "restore-closed" if restore else "expired"
                    self._close_architect_zone(zone.get("id"), status)
                    if not restore and zone.get("kind") == "paint":
                        self._push_activity("A divine architect zone fades — painted terrain reverts.")

        self._sweep_crowd_compulsions(restore=restore)
        self._sweep_dream_broadcasts(restore=restore)

    def _tick_divine_bargains(self, restore=False):
        """Settle open Merovingian bargains on tick. Lock held."""
        if restore or not GOD_MODE_ENABLED:
            return
        god = self.civilization.get("godState") or {}
        bushes = god.get("burningBush") or {}
        if not isinstance(bushes, dict):
            return
        ft = self.frameTick
        for key, bush in list(bushes.items()):
            if not isinstance(bush, dict):
                continue
            bargain = bush.get("bargain")
            if not isinstance(bargain, dict) or bargain.get("status") != "open":
                continue
            try:
                target_id = int(key)
            except (TypeError, ValueError):
                continue
            fail_pred = bargain.get("failurePredicate")
            if isinstance(fail_pred, dict) and self._evaluate_god_bargain_predicate(
                    fail_pred, target_id):
                self._settle_bargain(key, bush, "failure", "failure_predicate")
                continue
            succ_pred = bargain.get("successPredicate")
            if isinstance(succ_pred, dict) and self._evaluate_god_bargain_predicate(
                    succ_pred, target_id):
                self._settle_bargain(key, bush, "success", "predicate")
                continue
            expires = bargain.get("expiresFrame")
            if isinstance(expires, int) and ft >= expires:
                self._settle_bargain(key, bush, "failure", "expiry")

    # --- Sovereign God mode (docs/archive/plan-sovereign-god-mode-v2.md, Optional
    # Phase 8: free-prose story compiler) ---
    # This section NEVER mutates world state and NEVER calls god_apply --
    # god_compile_prose's only side effect is populating the SAME
    # _god_preview_cache slot god_preview uses, via a direct call to
    # god_preview itself (self.lock is a threading.RLock -- see __init__ --
    # so a nested acquire from an already-locked caller is safe). The
    # operator still has to explicitly Apply the resulting previewId through
    # the normal /control/god/apply route, which revalidates it exactly like
    # every other preview. The compiler is also never given SIM_GOD_TOKEN --
    # server.py's route handler checks the token BEFORE calling this method,
    # and this method has no parameter for it and never reads os.environ.
    def _god_compiler_prompt(self, prose):
        """Strict system+user prompt for the compiler model. Lists every
        known modifier key + bound and the three known primitive kinds
        inline, with two worked few-shot examples -- a small model needs the
        shape SHOWN, not merely described (docs/archive/plan-sovereign-god-mode-v2.md "Optional Phase 8")."""
        modifier_lines = "\n".join(
            f'  - "{k}": number in [{lo}, {hi}]' for k, (lo, hi) in GOD_MODIFIER_RANGES.items())
        system = (
            "You are a strict JSON compiler for a village-simulation divine story "
            "event. You NEVER write prose commentary. You output ONLY one JSON "
            "object, nothing before or after it, matching EXACTLY this shape:\n"
            '{"kind": "story_event", "payload": {\n'
            f'  "title": "<string, max {GOD_EVENT_TITLE_MAX_CHARS} chars>",\n'
            f'  "narration": "<string, max {GOD_TEXT_MAX_CHARS} chars>",\n'
            '  "visibility": "public",\n'
            f'  "durationFrames": <integer, {GOD_GUIDANCE_MIN_DURATION_FRAMES}-{GOD_GUIDANCE_MAX_DURATION_FRAMES}>,\n'
            '  "modifiers": {<zero or more of the allowed keys below>},\n'
            '  "primitives": [<zero or more allowed primitive objects below, usually empty>]\n'
            "}}\n\n"
            f"Allowed modifier keys (payload.modifiers), each optional, at most once each:\n{modifier_lines}\n\n"
            "Allowed primitive kinds (payload.primitives[].kind), each optional and usually omitted "
            "because you are not given real resource/agent/structure ids to target safely:\n"
            '  - "agent_vitals": {"targetId": <int>, "healthDelta": <number>, "hungerDelta": <number>}\n'
            '  - "grant_resource": {"resourceId": "<string>", "amount": <positive int>, "target": "stockpile"}\n'
            '  - "structure_condition": {"structureId": <int>, "delta": <number>}\n\n'
            "NEVER invent a modifier key outside the allowed list above. NEVER invent a resource id, "
            "agent id, or structure id. If the prose gives you no real id to target, omit \"primitives\" "
            "entirely (use only \"modifiers\" and \"narration\" to convey the effect)."
        )
        examples = (
            "Example 1\n"
            'Prose: "The river runs dark for three days and the fish flee."\n'
            "Output: "
            '{"kind": "story_event", "payload": {"title": "The Black River", '
            '"narration": "The river runs dark and the fish flee the shallows.", '
            '"visibility": "public", "durationFrames": 8100, '
            '"modifiers": {"fish_yield_multiplier": 0.1}, "primitives": []}}\n\n'
            "Example 2\n"
            'Prose: "A gentle mercy rain falls, easing hunger across the land for a while."\n'
            "Output: "
            '{"kind": "story_event", "payload": {"title": "Merciful Rain", '
            '"narration": "A gentle rain falls and the village\'s hunger eases.", '
            '"visibility": "public", "durationFrames": 5400, '
            '"modifiers": {"hunger_drain_multiplier": 0.5, "starvation_damage_multiplier": 0.2}, '
            '"primitives": []}}\n'
        )
        user = f"{examples}\nNow compile this prose into ONE JSON object of the same shape:\n{prose}"
        return system, user

    def _god_compiler_call_model(self, system_prompt, user_prompt):
        """Call the compiler model and return (raw_text_or_None, latency_ms).
        Never raises -- any lm_complete failure (including a simulated
        timeout in tests) degrades to (None, latency_ms) so the caller can
        reject cleanly instead of propagating an unhandled exception."""
        lm_fn = self.d.get("lm_complete")
        started = time.time()
        if lm_fn is None:
            return None, 0
        try:
            # Model routing (docs/archive/plan-sovereign-god-mode-v2.md "Optional
            # Phase 8" + "Model routing and implementation ownership"):
            # sim-fast ALREADY serves PIANO/background cognition, and past
            # sim-fast contention increased PIANO module drops. This phase
            # deliberately does NOT default a new LLM path onto sim-fast --
            # it routes to sim-smart (the same tier every agent decision
            # already uses) instead. That is a genuinely blocking
            # lm_complete call on sim-smart's own slot pool, not a new
            # background-cognition workload; see specs/03-cognition.md for
            # exactly which pool this participates in (it is NOT
            # MAX_CONCURRENT_LLM's PIANO pool). The plan is explicit that
            # this routing choice still needs its own A/B contention
            # measurement before GOD_COMPILER_ENABLED ships live -- see
            # GOD_COMPILER_ENABLED's definition and specs/12-ops.md.
            raw_text = lm_fn(system_prompt, user_prompt, model="sim-smart",
                             max_tokens=400, temperature=0.3,
                             timeout=GOD_COMPILER_TIMEOUT_SEC)
        except Exception:
            raw_text = None
        latency_ms = int((time.time() - started) * 1000)
        return raw_text, latency_ms

    def _god_compiler_parse(self, raw_text):
        """Parse the compiler model's raw text into the exact {kind,
        payload} shape the prompt demands. Returns (payload_dict, None) on
        success or (None, reason) on any parse/shape failure -- `reason` is
        short, secret-free, and safe to return over HTTP / write to
        compiler.jsonl. Tolerates a fenced code block the same way this
        codebase's other LLM-output parsing does."""
        if not isinstance(raw_text, str):
            return None, "compiler produced no text output"
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`").strip()
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()
        try:
            parsed = json.loads(cleaned)
        except (ValueError, TypeError):
            return None, f"could not parse compiler output as JSON: {raw_text[:200]!r}"
        if not isinstance(parsed, dict):
            return None, f"compiler output was not a JSON object: {raw_text[:200]!r}"
        if parsed.get("kind") != "story_event":
            return None, f"compiler output kind must be 'story_event', got {parsed.get('kind')!r}"
        payload = parsed.get("payload")
        if not isinstance(payload, dict):
            return None, "compiler output payload must be an object"
        return payload, None

    def _log_compiler(self, prose, model, latency_ms, status, reason=None, preview_id=None):
        """One record per compile attempt (docs/archive/plan-sovereign-god-mode-v2.md Logging section:
        "request text, model, latency, and result"). NEVER receives or logs
        SIM_GOD_TOKEN -- this method has no such parameter. Swallows any
        logging failure, matching every other divine log call in this
        module."""
        log_fn = self.d.get("log_compiler")
        if not log_fn:
            return
        try:
            log_fn(prose=prose, model=model, latency_ms=latency_ms,
                  status=status, reason=reason, preview_id=preview_id)
        except Exception:
            pass

    def god_compile_prose(self, prose):
        """Turn free operator prose into a DRAFT typed story_event preview.
        NEVER mutates world state; NEVER calls god_apply. Dual-gated on
        GOD_MODE_ENABLED AND GOD_COMPILER_ENABLED (docs/archive/plan-sovereign-god-mode-v2.md: "the
        contention gate is not cleared by shipping" -- both flags must be
        explicitly on). Enforces its own rate limit and session cap,
        distinct from agent cognition's MAX_CONCURRENT_LLM pool."""
        with self.lock:
            if not (GOD_MODE_ENABLED and GOD_COMPILER_ENABLED):
                return {"compileOk": False, "reason": "god compiler disabled"}

            prose_norm, reason = self._normalize_divine_text(
                prose, max_chars=GOD_COMPILER_PROSE_MAX_CHARS,
                max_bytes=GOD_COMPILER_PROSE_MAX_BYTES)
            if reason:
                return {"compileOk": False, "reason": f"prose {reason}"}

            state = self._god_compiler_state
            now = time.time()
            if state["compileCount"] >= GOD_COMPILER_SESSION_CAP:
                return {"compileOk": False,
                        "reason": (f"compiler session cap ({GOD_COMPILER_SESSION_CAP}) reached "
                                  "for this process lifetime")}
            if now - state["lastCompileWallTime"] < GOD_COMPILER_MIN_INTERVAL_SEC:
                return {"compileOk": False,
                        "reason": f"rate limited: at most one compile per {GOD_COMPILER_MIN_INTERVAL_SEC:.0f}s"}

            # Bump BEFORE the model call so a hung/failed/rejected compile
            # still counts against the session cap and interval (docs/archive/plan-sovereign-god-mode-v2.md:
            # "Bump session count regardless of success").
            state["lastCompileWallTime"] = now
            state["compileCount"] += 1

            model_id = "sim-smart"
            system_prompt, user_prompt = self._god_compiler_prompt(prose_norm)
            raw_text, latency_ms = self._god_compiler_call_model(system_prompt, user_prompt)

            if not raw_text:
                fail_reason = "the compiler model produced no output (timeout or empty response)"
                self._log_compiler(prose_norm, model_id, latency_ms, "rejected", fail_reason)
                return {"compileOk": False, "reason": fail_reason}

            payload, parse_reason = self._god_compiler_parse(raw_text)
            if parse_reason:
                self._log_compiler(prose_norm, model_id, latency_ms, "rejected", parse_reason)
                return {"compileOk": False, "reason": parse_reason}

            normalized, validate_reason = self._validate_god_story_event(payload)
            if validate_reason:
                self._log_compiler(prose_norm, model_id, latency_ms, "rejected", validate_reason)
                return {"compileOk": False, "reason": validate_reason}

            # Preview-only: reuse the SAME entry point every other kind uses,
            # so the compiled draft lands in the identical
            # _god_preview_cache slot, under the identical digest/
            # fingerprint/reversibility machinery -- and this method never
            # touches _god_preview_cache directly or calls god_apply.
            preview = self.god_preview({"kind": "story_event", "payload": normalized["payload"]})
            if not preview.get("ok"):
                # Should be unreachable -- `normalized` already passed the
                # SAME validator god_preview re-runs -- but never let a
                # compiler-side bug surface as an unhandled exception.
                fail_reason = preview.get("reason") or "preview rejected the compiled draft"
                self._log_compiler(prose_norm, model_id, latency_ms, "rejected", fail_reason)
                return {"compileOk": False, "reason": fail_reason}

            self._log_compiler(prose_norm, model_id, latency_ms, "draft",
                               None, preview.get("previewId"))
            return {
                "compileOk": True,
                "previewId": preview["previewId"],
                "commandDigest": preview["commandDigest"],
                "previewOutcome": preview.get("previewOutcome"),
                "normalizedCommand": preview.get("normalizedCommand"),
                "reversibilityClass": preview.get("reversibilityClass"),
                "expiresAt": preview.get("expiresAt"),
            }
