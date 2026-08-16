"""Phase 6d mixin: Daily Council + village-growth slice of SimEngine.

Extracted unchanged (pure move, no behavior change) from core.py's SimEngine
class body — the contiguous method range from `_daily_council_living`
through `_maybe_advance_rules` (formerly core.py lines ~6195-7853). Covers:
the Daily Council Assembly (scheduled whole-village council — agenda,
seating, phases, speak/propose/vote actions, ballot resolution/ratification,
digest, transcript pruning/adjournment), the Phase D invention council
(proposal recording, verdicts, dissolution) and its invention-demand
backstop, stuck-project relocation, structure reorganization, district
founding, and the rules-proposal backstop (`_maybe_advance_rules`).

Exec-loaded into the shared package namespace — see simulation/sim_engine/__init__.py.
"""

# NOTE: constants.py/persistence.py/helpers.py names are NOT imported here.
# They live in the exec()-shared namespace — see simulation/sim_engine/__init__.py.


class _CouncilGrowthMixin:
    """Mixin slice of SimEngine: Daily Council Assembly, invention council,
    stuck-project relocation, structure reorganization, district founding,
    and the rules backstop. See module docstring for exact scope."""

    # --- Daily Council Assembly (scheduled, whole-village council) ---
    def _daily_council_living(self, settlement_id=None):
        """Living roster, optionally scoped to one normalized settlement id."""
        living = [a for a in self.agents if a.get("deathFrame") is None]
        if FACTION_SPLIT_ENABLED and settlement_id:
            living = [
                agent for agent in living
                if self._settlement_id_for_agent(agent) == settlement_id
            ]
        return living

    def _daily_council_agenda(self):
        c = self.civilization
        succession = c.get("pendingSuccession")
        succession_sid = (
            (succession.get("settlementId") or self._primary_settlement_id())
            if FACTION_SPLIT_ENABLED and isinstance(succession, dict) else None
        )
        elder = next((a for a in self.agents if a.get("role") == "elder"), None)
        agenda_sid = None
        if FACTION_SPLIT_ENABLED:
            agenda_sid = succession_sid or (
                self._settlement_id_for_agent(elder)
                if elder else self._primary_settlement_id()
            )
        active_projects = [
            f"{did}: {p.get('name') or p.get('type')}"
            for did, p in sorted((c.get("districtProjects") or {}).items())
            if p
        ]
        stalled = [
            item for item in active_projects
            if self.frameTick - int((c.get("districtLastContribution") or {}).get(
                item.split(":", 1)[0], self.frameTick)) >= STALL_THRESHOLD
        ]
        scarce = sorted(
            rid for rid in (c.get("stockpile") or {})
            if self._resource_is_obtainable(rid)
            and self._village_holdings(rid) <= DAILY_COUNCIL_SCARCITY_THRESHOLD
        )
        agenda = [
            {
                "topic": "world_status",
                "detail": (f"{self._current_era_name()}, technology tier "
                           f"{self._village_tech_tier()}, "
                           f"{len(self._daily_council_living())} living villagers"),
            },
            {
                "topic": "projects",
                "detail": ("Ongoing: " + ", ".join(active_projects[:DAILY_COUNCIL_SCARCITY_TOPICS]))
                if active_projects else "No ongoing district projects",
            },
            {
                "topic": "limitations",
                "detail": (
                    ("Stalled: " + ", ".join(stalled[:4]) + ". ") if stalled else ""
                ) + (("Low village stores: " + ", ".join(scarce[:DAILY_COUNCIL_SCARCITY_TOPICS]))
                     if scarce else "No acute village-store shortage recorded"),
            },
            {
                "topic": "rules",
                "detail": (", ".join(r.get("name") or r.get("id", "rule")
                                    for r in (self._rules_for_settlement(agenda_sid) if FACTION_SPLIT_ENABLED
                                              else (c.get("rules") or []))[:8])
                           or "No active village rules"),
            },
            {
                "topic": "ideas_and_proposals",
                "detail": "Improvements, modifications, new projects, and new rules",
            },
            {
                "topic": "feelings_about_evolution",
                "detail": "How villagers feel about the village's path and progress",
            },
        ]
        if self._invention_required():
            agenda.append({
                "topic": "invention_required",
                "detail": "Known productive options are exhausted; a new blueprint is needed",
            })
        if LIFECYCLE_ENABLED and isinstance(succession, dict):
            candidates = [
                str(name) for name in (succession.get("candidates") or [])
                if isinstance(name, str) and name
            ]
            if candidates:
                agenda.append({
                    "topic": "leadership_vacancy",
                    "detail": (
                        "The village has no elder. Compare the candidates and choose "
                        f"a successor: {', '.join(candidates)}"
                    ),
                    "candidates": candidates,
                    "electionId": succession.get("electionId"),
                })
        return agenda

    def _assign_council_seats(self, attendees=None, allow_headless=False):
        """Return stable world-coordinate seats, with the living elder at head."""
        if attendees is None:
            attendees = [a for a in self._daily_council_living()
                         if not a.get("incapacitated")]
        elder = next((a for a in attendees if a.get("role") == "elder"), None)
        if elder is None and not allow_headless:
            # A Daily Council cannot deliberate or ratify a verdict without
            # its living, available elder. Succession/emergency systems own
            # restoring leadership; seating must never create a headless
            # active assembly in the meantime.
            return []
        ordered = ([elder] if elder else []) + sorted(
            (a for a in attendees if a is not elder),
            key=lambda a: (str(a.get("role") or ""), str(a.get("name") or "")),
        )
        bounds = (self.civilization.get("districts", {}).get("village_core") or
                  STARTER_DISTRICTS["village_core"])["bounds"]
        center_x = (bounds["x1"] + bounds["x2"]) / 2
        center_y = (bounds["y1"] + bounds["y2"]) / 2
        radius = min(220.0, max(105.0, 48.0 + 12.0 * len(ordered)))
        seats = []
        for idx, agent in enumerate(ordered):
            angle = -math.pi / 2 + (2 * math.pi * idx / max(1, len(ordered)))
            seats.append({
                "name": agent["name"], "role": agent["role"], "seatIndex": idx,
                "x": round(center_x + math.cos(angle) * radius, 2),
                "y": round(center_y + math.sin(angle) * radius, 2),
                "isHead": bool(idx == 0 and elder is not None),
            })
        return seats

    def _stamp_council_event(self, entry):
        """Attach frame + wall-clock time to either council transcript line."""
        out = dict(entry)
        out.setdefault("frame", self.frameTick)
        out.setdefault("ts", datetime.now(timezone.utc).isoformat())
        return out

    def _append_council_transcript(self, entry):
        """Append once to the live council and, for Daily Council, its RAM audit."""
        daily = self.civilization.get("dailyCouncil")
        if daily:
            event = self._stamp_council_event(entry)
            daily.setdefault("transcript", []).append(event)
            who = (event.get("who") or event.get("proposer") or
                   event.get("elder") or event.get("voter"))
            text = event.get("text")
            if text is None:
                text = event.get("message")
            if text is None and event.get("outcome") is not None:
                text = event.get("outcome")
            self.council_transcript_rows.append({
                "meeting_id": daily.get("meetingId", daily.get("day")),
                "who": str(who) if who is not None else None,
                "type": str(event.get("type") or "event"),
                "text": str(text or ""),
                "feeling": (str(event.get("feeling"))[:120]
                            if event.get("feeling") is not None else None),
                "frame_tick": int(event.get("frame", self.frameTick)),
                "ts": str(event.get("ts") or ""),
            })
            return event
        council = self._council_active()
        if council:
            event = self._stamp_council_event(entry)
            council.setdefault("transcript", []).append(event)
            return event
        return None

    def _set_daily_council_phase(self, council, phase):
        if council.get("phase") == phase:
            return
        council["phase"] = phase
        council["phaseFrame"] = self.frameTick
        self._append_council_transcript({
            "type": "phase", "text": f"Council entered {phase}", "phase": phase,
        })
        self._sync_daily_council_turns(council)

    def _sync_daily_council_turns(self, council):
        """Issue only the one-shot council turns relevant to the current phase."""
        attendees = set(council.get("attendees") or [])
        phase = council.get("phase")
        eligible = set()
        if phase == "convening":
            eligible.update(attendees)
        elif phase == "discussion":
            order = council.get("speakingOrder") or []
            idx = int(council.get("nextSpeakerIdx") or 0)
            total = len(order) * int(council.get("maxRounds") or 0)
            if order and idx < total:
                eligible.add(order[idx % len(order)])
        elif phase == "proposal" and not council.get("ballot"):
            eligible.update(attendees)
        elif phase == "voting" and council.get("ballot"):
            votes = council["ballot"].get("votes") or {}
            eligible.update(name for name in attendees if name not in votes)
        elif phase == "verdict" and council.get("verdict") \
                and not council.get("elderVerdictSpoken"):
            elder = next(
                (s.get("name") for s in council.get("seats") or [] if s.get("isHead")),
                None,
            )
            if elder:
                eligible.add(elder)
        for actor in self.agents:
            actor["councilTurn"] = actor["name"] in eligible
            if actor["councilTurn"]:
                actor["thinkTimer"] = min(int(actor.get("thinkTimer") or 1), 1)

    def _maybe_convene_daily_council(self):
        if (not DAILY_COUNCIL_ENABLED or self.civilization.get("dailyCouncil")
                or self.civilization.get("councilActive")):
            return False
        succession = self.civilization.get("pendingSuccession")
        succession_sid = (
            (succession.get("settlementId") or self._primary_settlement_id())
            if FACTION_SPLIT_ENABLED and isinstance(succession, dict) else None
        )
        living = self._daily_council_living(succession_sid)
        succession_emergency = bool(
            LIFECYCLE_ENABLED
            and isinstance(succession, dict)
            and not any(a.get("role") == "elder" for a in living)
        )
        if not succession_emergency:
            living = self._daily_council_living()
            succession_sid = None
        attendees = [a for a in living if not a.get("incapacitated")]
        excused = sorted(a["name"] for a in living if a.get("incapacitated"))
        if len(living) < DAILY_COUNCIL_MIN_LIVING and not succession_emergency:
            return False
        if not attendees:
            return False
        if not any(a.get("role") == "elder" for a in attendees) \
                and not succession_emergency:
            return False
        seats = self._assign_council_seats(
            attendees, allow_headless=succession_emergency,
        )
        if not seats:
            return False
        elder = next((s for s in seats if s["isHead"]), None)
        ordered_names = [s["name"] for s in seats]
        succession_ballot = None
        if succession_emergency:
            candidates = list(succession.get("candidates") or [])
            succession_ballot = {
                "kind": "succession",
                "id": succession.get("electionId"),
                "title": "Choose the next village elder",
                "proposedBy": "the village",
                "candidates": candidates,
                "votes": {},
                "quorum": len(ordered_names) // 2 + 1,
            }
        council = {
            "trigger": "succession" if succession_emergency else "daily",
            "phase": "convening",
            "phaseFrame": self.frameTick,
            "day": self.frameTick // DAY_FRAMES,
            # Frame is unique even when an emergency council shares an
            # in-world day with a previously adjourned scheduled council.
            "meetingId": self.frameTick,
            "frame": self.frameTick,
            "ts": datetime.now(timezone.utc).isoformat(),
            "seats": seats,
            "attendees": ordered_names,
            "excused": excused,
            "agenda": self._daily_council_agenda(),
            "round": 0,
            "maxRounds": DAILY_COUNCIL_DISCUSSION_ROUNDS,
            "speakingOrder": ordered_names,
            "nextSpeakerIdx": 0,
            "transcript": [],
            "ballot": succession_ballot,
            "verdict": None,
        }
        if succession_emergency and FACTION_SPLIT_ENABLED:
            council["settlementId"] = succession_sid
        self.civilization["dailyCouncil"] = council
        for seat in seats:
            agent = self._find_agent(seat["name"])
            if not agent:
                continue
            agent["goal"] = None
            agent["assignedTask"] = None
            agent["councilTurn"] = True
            agent["waypoints"] = []
            agent["targetX"], agent["targetY"] = seat["x"], seat["y"]
            agent["idleFrames"] = 0
        self._append_council_transcript({
            "type": "convene",
            "who": elder["name"] if elder else "the village",
            "text": (f"Daily Council day {council['day']} convened with "
                     f"{len(ordered_names)} attendees"),
        })
        if succession_ballot:
            self._append_council_transcript({
                "type": "succession_ballot",
                "who": "the village",
                "text": (
                    "Leadership is vacant; candidates are "
                    + ", ".join(succession_ballot["candidates"])
                ),
                "candidates": list(succession_ballot["candidates"]),
                "electionId": succession_ballot["id"],
            })
        self._push_activity(
            ("Emergency succession council" if succession_emergency else "Daily Council")
            + f" convenes: {len(ordered_names)} attend"
            + (f"; excused: {', '.join(excused)}" if excused else "")
        )
        self._sync_daily_council_turns(council)
        return True

    def _refresh_daily_council_roster(self, council):
        """Remove deaths/collapses and seat any newly available living member."""
        settlement_id = (
            council.get("settlementId") or self._primary_settlement_id()
            if FACTION_SPLIT_ENABLED and council.get("trigger") == "succession" else None
        )
        living = self._daily_council_living(settlement_id)
        available = [a for a in living if not a.get("incapacitated")]
        excused = sorted(a["name"] for a in living if a.get("incapacitated"))
        allow_headless = (
            council.get("trigger") == "succession"
            and not any(a.get("role") == "elder" for a in living)
        )
        new_seats = self._assign_council_seats(
            available, allow_headless=allow_headless,
        )
        new_names = [s["name"] for s in new_seats]
        old_names = set(council.get("attendees") or [])
        if new_names != council.get("attendees") or excused != council.get("excused"):
            removed = sorted(old_names - set(new_names))
            added = sorted(set(new_names) - old_names)
            council["attendees"] = new_names
            council["excused"] = excused
            council["seats"] = new_seats
            council["speakingOrder"] = new_names
            max_turns = len(new_names) * council.get(
                "maxRounds", DAILY_COUNCIL_DISCUSSION_ROUNDS)
            council["nextSpeakerIdx"] = min(council.get("nextSpeakerIdx", 0), max_turns)
            if council.get("ballot") is not None:
                ballot = council["ballot"]
                ballot["votes"] = {
                    name: vote for name, vote in (ballot.get("votes") or {}).items()
                    if name in new_names
                }
                ballot["quorum"] = len(new_names) // 2 + 1
            self._append_council_transcript({
                "type": "attendance",
                "text": (f"Roster updated; added {', '.join(added) or 'none'}; "
                         f"removed {', '.join(removed) or 'none'}; "
                         f"excused {', '.join(excused) or 'none'}"),
            })
        seat_by_name = {s["name"]: s for s in new_seats}
        for agent in self.agents:
            if agent["name"] not in seat_by_name:
                if agent.get("councilTurn"):
                    agent["councilTurn"] = False
                continue
            seat = seat_by_name[agent["name"]]
            agent["goal"] = None
            agent["assignedTask"] = None
            agent["waypoints"] = []
            agent["targetX"], agent["targetY"] = seat["x"], seat["y"]
        self._sync_daily_council_turns(council)

    def _daily_council_all_seated(self, council):
        for seat in council.get("seats") or []:
            agent = self._find_agent(seat["name"])
            if not agent or math.hypot(agent["x"] - seat["x"],
                                       agent["y"] - seat["y"]) > 5.0:
                return False
        return True

    def _daily_council_tally(self, council):
        ballot = council.get("ballot") or {}
        votes = ballot.get("votes") or {}
        if ballot.get("kind") == "succession":
            choices = list(ballot.get("candidates") or [])
            return {
                **{choice: sum(1 for vote in votes.values() if vote == choice)
                   for choice in choices},
                "abstain": sum(1 for vote in votes.values() if vote == "abstain"),
            }
        return {choice: sum(1 for vote in votes.values() if vote == choice)
                for choice in ("yes", "no", "abstain")}

    def _daily_council_reject(self, agent, action, reason):
        agent["lastCouncilRejection"] = {
            "action": action, "reason": reason, "frame": self.frameTick,
        }
        return f"{agent['name']} cannot {action.replace('_', ' ')}: {reason}"

    def _daily_council_actor(self, agent, action, phases):
        council = self.civilization.get("dailyCouncil")
        if not DAILY_COUNCIL_ENABLED or not council:
            return None, self._daily_council_reject(agent, action, "no Daily Council is active")
        if agent.get("deathFrame") is not None or agent.get("incapacitated") \
                or agent["name"] not in (council.get("attendees") or []):
            return None, self._daily_council_reject(agent, action, "actor is not an attendee")
        seat = next(
            (s for s in council.get("seats") or [] if s.get("name") == agent["name"]),
            None,
        )
        if not seat or math.hypot(agent["x"] - seat["x"], agent["y"] - seat["y"]) > 5.0:
            return None, self._daily_council_reject(agent, action, "actor is not seated")
        if council.get("phase") not in phases:
            return None, self._daily_council_reject(
                agent, action, f"invalid during {council.get('phase')} phase",
            )
        return council, None

    def _council_speak(self, agent, decision):
        council, rejection = self._daily_council_actor(
            agent, "council_speak", {"discussion", "verdict"},
        )
        if rejection:
            return rejection
        phase = council["phase"]
        if phase == "discussion":
            order = council.get("speakingOrder") or []
            idx = int(council.get("nextSpeakerIdx") or 0)
            expected = order[idx % len(order)] if order else None
            if expected != agent["name"]:
                return self._daily_council_reject(
                    agent, "council_speak", f"waiting for {expected or 'the next speaker'}",
                )
        else:
            head = next(
                (s.get("name") for s in council.get("seats") or [] if s.get("isHead")),
                None,
            )
            if head != agent["name"] or not council.get("verdict"):
                return self._daily_council_reject(
                    agent, "council_speak", "only the elder may announce a ready verdict",
                )
        message = str(decision.get("message") or "").strip()
        if not message:
            return self._daily_council_reject(agent, "council_speak", "message is required")
        feeling = str(decision.get("feeling") or "thoughtful").strip()[:80]
        topic = str(decision.get("topic") or "world_status").strip()[:80]
        message = message[:500]
        agent["message"] = message
        agent["messageTimer"] = 180
        agent["lastSpokeFrame"] = self.frameTick
        self._append_council_transcript({
            "type": "verdict_speech" if phase == "verdict" else "speak",
            "who": agent["name"], "text": message, "feeling": feeling, "topic": topic,
        })
        if phase == "discussion":
            council["nextSpeakerIdx"] = int(council.get("nextSpeakerIdx") or 0) + 1
            count = max(1, len(council.get("speakingOrder") or []))
            council["round"] = min(
                council.get("maxRounds", DAILY_COUNCIL_DISCUSSION_ROUNDS),
                council["nextSpeakerIdx"] // count,
            )
        else:
            council["elderVerdictSpoken"] = True
            if council.get("verdict") is not None:
                council["verdict"]["elderStatement"] = message
        agent["lastCouncilRejection"] = None
        self._sync_daily_council_turns(council)
        return f"{agent['name']} spoke to the Daily Council"

    def _council_propose(self, agent, decision):
        council, rejection = self._daily_council_actor(
            agent, "council_propose", {"proposal"},
        )
        if rejection:
            return rejection
        if council.get("ballot"):
            return self._daily_council_reject(agent, "council_propose", "a ballot is already open")
        kind = decision.get("kind")
        if kind == "rule":
            rule = decision.get("rule")
            if not self._validate_rule(rule, agent=agent):
                return self._daily_council_reject(agent, "council_propose", "invalid rule proposal")
            self._propose_rule(agent, {"rule": rule})
            stable_id = rule["id"]
            title = rule["name"]
        elif kind == "blueprint":
            blueprint = decision.get("blueprint")
            ok, reason = self._validate_blueprint(blueprint)
            if not ok:
                return self._daily_council_reject(
                    agent, "council_propose", f"invalid blueprint proposal ({reason})",
                )
            self.apply_decision(agent, {
                "action": "propose_blueprint", "blueprint": blueprint,
                "reasoning": decision.get("reasoning") or "Daily Council proposal",
            })
            stable_id = blueprint["id"]
            title = blueprint["name"]
            if not any(p.get("id") == stable_id for p in self.civilization["pendingBlueprints"]):
                return self._daily_council_reject(
                    agent, "council_propose", "blueprint did not enter the pending registry",
                )
        elif kind == "idea":
            title = str(decision.get("title") or "").strip()[:120]
            detail = str(decision.get("detail") or "").strip()[:500]
            if not title or not detail:
                return self._daily_council_reject(
                    agent, "council_propose", "idea requires title and detail",
                )
            stable_id = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:24]
            stable_id = stable_id or f"idea_{self.frameTick}"
        else:
            return self._daily_council_reject(
                agent, "council_propose", "kind must be rule, blueprint, or idea",
            )
        council["ballot"] = {
            "kind": kind, "id": stable_id, "title": title,
            "proposedBy": agent["name"], "votes": {agent["name"]: "yes"},
            "quorum": len(council.get("attendees") or []) // 2 + 1,
        }
        self._append_council_transcript({
            "type": "proposal", "who": agent["name"], "text": title,
            "kind": kind, "proposalId": stable_id,
        })
        agent["lastCouncilRejection"] = None
        self._set_daily_council_phase(council, "voting")
        return f'{agent["name"]} proposed council {kind} "{title}"'

    def _council_vote(self, agent, decision):
        council, rejection = self._daily_council_actor(
            agent, "council_vote", {"voting"},
        )
        if rejection:
            return rejection
        ballot = council.get("ballot")
        if not ballot:
            return self._daily_council_reject(agent, "council_vote", "no ballot is open")
        if agent["name"] in (ballot.get("votes") or {}):
            return self._daily_council_reject(agent, "council_vote", "vote already recorded")
        if ballot.get("kind") == "succession":
            candidate = decision.get("candidate")
            vote = "abstain" if decision.get("vote") == "abstain" else candidate
            if vote != "abstain" and vote not in (ballot.get("candidates") or []):
                return self._daily_council_reject(
                    agent, "council_vote",
                    "candidate must name a current succession candidate or vote abstain",
                )
        else:
            vote = decision.get("vote")
            if vote not in ("yes", "no", "abstain"):
                return self._daily_council_reject(
                    agent, "council_vote", "vote must be yes, no, or abstain",
                )
        ballot.setdefault("votes", {})[agent["name"]] = vote
        if ballot.get("kind") == "rule":
            pending = self._find_pending_ballot(ballot.get("id"), voter=agent)
            if pending:
                pending.setdefault("votes", {})[agent["name"]] = vote
                self._tally_and_maybe_enact(pending)
        self._append_council_transcript({
            "type": "vote", "who": agent["name"], "text": vote,
            "proposalId": ballot.get("id"),
            "candidate": vote if ballot.get("kind") == "succession"
                         and vote != "abstain" else None,
        })
        agent["lastCouncilRejection"] = None
        self._sync_daily_council_turns(council)
        return f'{agent["name"]} voted {vote} on "{ballot.get("title")}"'

    def _resolve_daily_council_ballot(self, council, reason):
        ballot = council.get("ballot")
        if not ballot:
            return None
        tally = self._daily_council_tally(council)
        if ballot.get("kind") == "succession":
            settlement_id = (
                council.get("settlementId") or self._primary_settlement_id()
                if FACTION_SPLIT_ENABLED else None
            )
            candidates = [
                self._find_agent(name) for name in (ballot.get("candidates") or [])
            ]
            candidates = [
                candidate for candidate in candidates
                if candidate and candidate.get("deathFrame") is None
                and not candidate.get("incapacitated")
                and (not settlement_id
                     or self._settlement_id_for_agent(candidate) == settlement_id)
            ]
            if not candidates:
                return None
            high = max(tally.get(candidate["name"], 0) for candidate in candidates)
            tied = [
                candidate for candidate in candidates
                if tally.get(candidate["name"], 0) == high
            ]
            chosen = min(tied, key=lambda candidate: candidate["id"])
            tie_break = len(tied) > 1
            outcome = (
                f"{chosen['name']} chosen by stable seniority after a "
                f"{'no-vote tie' if high == 0 else 'vote tie'}"
                if tie_break else f"{chosen['name']} chosen with {high} village vote"
                + ("" if high == 1 else "s")
            )
            council["verdict"] = {
                "winner": chosen["name"], "tally": tally, "elderRuling": None,
                "outcome": outcome, "reason": reason,
                "tieBreak": "lowest stable agent id" if tie_break else None,
            }
            ratification = self._ratify_daily_council_ballot(
                council, chosen["name"], tie_break=tie_break,
            )
            council["verdict"]["ratification"] = ratification
            self._refresh_daily_council_roster(council)
            self._append_council_transcript({
                "type": "verdict", "who": "the village",
                "text": f"{chosen['name']} is declared the new village elder: {outcome}",
                "outcome": outcome, "tally": tally, "ratification": ratification,
            })
            return council["verdict"]
        quorum = int(ballot.get("quorum") or (len(council.get("attendees") or []) // 2 + 1))
        elder = next((s["name"] for s in council.get("seats") or [] if s.get("isHead")), None)
        if tally["yes"] >= quorum:
            winner, outcome = "yes", "approved by majority"
        elif tally["no"] >= quorum:
            winner, outcome = "no", "rejected by majority"
        elif tally["yes"] == tally["no"]:
            elder_vote = (ballot.get("votes") or {}).get(elder)
            winner = elder_vote if elder_vote in ("yes", "no") else "no"
            outcome = f"{winner} by elder tie-break"
        else:
            # A non-tied plurality is not a majority of the whole seated
            # village. Abstentions therefore cannot lower the enactment
            # threshold. Exact ties use the explicit elder exception above.
            winner = "no"
            outcome = "rejected: no whole-village majority"
        council["verdict"] = {
            "winner": winner, "tally": tally, "elderRuling": elder,
            "outcome": outcome, "reason": reason,
        }
        ratification = self._ratify_daily_council_ballot(
            council, winner, tie_break=(tally["yes"] == tally["no"]),
        )
        council["verdict"]["ratification"] = ratification
        self._append_council_transcript({
            "type": "verdict", "who": elder,
            "text": f"{ballot.get('title') or ballot.get('id')}: {outcome}",
            "outcome": outcome, "tally": tally, "ratification": ratification,
        })
        return council["verdict"]

    def _ratify_daily_council_ballot(self, council, winner, tie_break=False):
        """Apply an approved/rejected ballot only through existing real paths."""
        ballot = council.get("ballot") or {}
        kind = ballot.get("kind")
        elder_name = next(
            (s.get("name") for s in council.get("seats") or [] if s.get("isHead")),
            None,
        )
        elder = self._find_agent(elder_name) if elder_name else None
        if kind == "idea":
            return "advisory idea recorded"
        if kind == "succession":
            election_id = ballot.get("id")
            winner_rule = next((
                rule for rule in self.civilization.get("pendingRules") or []
                if rule.get("kind") == "succession"
                and rule.get("electionId") == election_id
                and rule.get("candidateName") == winner
            ), None)
            if winner_rule is None:
                return "succession unresolved: winning ballot is no longer valid"
            self._enact_succession_winner(winner_rule)
            promoted = self._find_agent(winner)
            return (
                "succession enacted through village election"
                if promoted and promoted.get("role") == "elder"
                else "succession restart required: winner unavailable"
            )
        if kind == "rule":
            c = self.civilization
            elder_sid = self._settlement_id_for_agent(elder) if elder and FACTION_SPLIT_ENABLED else None
            pending = self._find_pending_ballot(ballot.get("id"), voter=elder)
            if pending is None:
                rules_scan = self._rules_for_settlement(elder_sid) if elder_sid else c.get("rules") or []
                enacted = any(r.get("id") == ballot.get("id") for r in rules_scan)
                return "rule enacted through tally" if enacted else "rule ballot already resolved"
            pending["votes"] = dict(ballot.get("votes") or {})
            if tie_break and elder_name:
                # The elder has an ordinary personal vote and, on an exact
                # tie, one explicit ratification vote. The existing tally/
                # enact path remains the sole code allowed to mutate rules.
                pending["votes"]["__elder_ratification__"] = winner
            result = self._tally_and_maybe_enact(pending)
            if result == "pending" and winner == "no":
                # No whole-village majority means this assembly proposal is
                # closed without becoming a lingering ordinary-rule ballot.
                # Active rules remain untouched; only the unratified pending
                # proposal is discarded.
                _, pending_list, scope_sid = self._governance_scope_lists(pending)
                pending_list[:] = [r for r in pending_list if r.get("id") != ballot.get("id")]
                return "rule rejected without whole-village majority"
            return f"rule {result} through existing tally path"
        if kind == "blueprint":
            if elder is None:
                return "blueprint unresolved: no seated elder"
            pending = next(
                (p for p in self.civilization["pendingBlueprints"] if p.get("id") == ballot.get("id")),
                None,
            )
            if pending is None:
                return "blueprint was no longer pending"
            if winner == "no":
                self.apply_decision(elder, {
                    "action": "reject_blueprint", "target": ballot.get("id"),
                    "reasoning": "Ratifying the Daily Council rejection.",
                })
                return "blueprint rejected through elder review path"
            if SAGE_REVIEW_ENABLED and pending.get("sageReview") == "pending":
                self.apply_decision(elder, {
                    "action": "sage_review_blueprint", "target": ballot.get("id"),
                    "sage_decision": "approve",
                    "message": "The seated council verified the proposal by majority.",
                    "reasoning": "Council geography and resource review.",
                })
            self.apply_decision(elder, {
                "action": "approve_blueprint", "target": ballot.get("id"),
                "reasoning": "Ratifying the Daily Council majority.",
            })
            approved = ballot.get("id") in self.civilization["projectRegistry"]
            return ("blueprint approved through sage/elder paths" if approved
                    else "blueprint remained pending after elder path")
        return "unknown ballot kind"

    def _daily_council_digest(self, council):
        ballot = council.get("ballot")
        verdict = council.get("verdict")
        feelings = []
        for event in council.get("transcript") or []:
            feeling = str(event.get("feeling") or "").strip()
            if feeling and feeling not in feelings:
                feelings.append(feeling[:40])
        proposals = []
        if ballot:
            proposals.append({
                "title": str(ballot.get("title") or ballot.get("id") or "proposal")[:120],
                "kind": ballot.get("kind") or "idea",
                "outcome": ((verdict or {}).get("outcome") or "unresolved")[:120],
            })
        return {
            "meetingId": council.get("meetingId", council.get("day")),
            "day": council.get("day"),
            "frame": council.get("frame"),
            "ts": council.get("ts"),
            "topics": [str(a.get("topic") or "")[:80]
                       for a in (council.get("agenda") or [])[:8]],
            "proposals": proposals,
            "verdict": ({
                "winner": verdict.get("winner"),
                "outcome": verdict.get("outcome"),
            } if verdict else None),
            "mood": ", ".join(feelings[:4]) if feelings else "not recorded",
        }

    def _prune_daily_council_transcripts(self):
        meeting_ids = sorted({
            r.get("meeting_id") for r in self.council_transcript_rows
            if isinstance(r.get("meeting_id"), int)
        }, reverse=True)
        keep = set(meeting_ids[:DAILY_COUNCIL_TRANSCRIPT_RETENTION_MEETINGS])
        self.council_transcript_rows = [
            r for r in self.council_transcript_rows if r.get("meeting_id") in keep
        ]

    def _adjourn_daily_council(self, reason="completed"):
        council = self.civilization.get("dailyCouncil")
        if not council:
            return False
        if council.get("phase") != "adjourned":
            council["phase"] = "adjourned"
            council["phaseFrame"] = self.frameTick
        self._append_council_transcript({
            "type": "adjourn", "text": reason,
            "outcome": (council.get("verdict") or {}).get("outcome")
                       or "adjourned without resolution",
        })
        for agent in self.agents:
            if agent.get("councilTurn"):
                agent["councilTurn"] = False
                agent["thinkTimer"] = min(agent.get("thinkTimer", 1), 1)
        outcome = (council.get("verdict") or {}).get(
            "outcome", "adjourned without resolution")
        record = {
            "meetingId": council.get("meetingId", council.get("day")),
            "frame": council.get("frame"), "start_frame": council.get("frame"),
            "end_frame": self.frameTick, "ts": datetime.now(timezone.utc).isoformat(),
            "started_ts": council.get("ts"),
            "trigger": ("succession" if council.get("trigger") == "succession"
                        else "daily_council"),
            "day": council.get("day"), "attendees": list(council.get("attendees") or []),
            "excused": list(council.get("excused") or []),
            "agenda": json.loads(json.dumps(council.get("agenda") or [])),
            "ballot": json.loads(json.dumps(council.get("ballot"), default=str)),
            "verdict": json.loads(json.dumps(council.get("verdict"), default=str)),
            "outcome": outcome,
            "transcript": json.loads(json.dumps(council.get("transcript") or [],
                                                 default=str)),
        }
        log = self.civilization.setdefault("councilLog", [])
        log.insert(0, record)
        del log[DAILY_COUNCIL_LOG_CAP:]
        digests = self.civilization.setdefault("councilDigests", [])
        digests.insert(0, self._daily_council_digest(council))
        del digests[DAILY_COUNCIL_DIGEST_CAP:]
        self._prune_daily_council_transcripts()
        self.civilization["dailyCouncil"] = None
        self._push_activity(f"Daily Council adjourns: {outcome}")
        return True

    def _maybe_advance_daily_council(self):
        if not DAILY_COUNCIL_ENABLED:
            return
        council = self.civilization.get("dailyCouncil")
        if not council:
            return
        self._refresh_daily_council_roster(council)
        if len(self._daily_council_living()) < DAILY_COUNCIL_MIN_LIVING \
                and council.get("trigger") != "succession":
            self._adjourn_daily_council("too few living villagers")
            return
        if not any(seat.get("isHead") for seat in council.get("seats") or []) \
                and council.get("trigger") != "succession":
            self._adjourn_daily_council("no living, available elder")
            return
        if self.frameTick - council.get("frame", self.frameTick) >= \
                DAILY_COUNCIL_SESSION_TTL_FRAMES:
            self._adjourn_daily_council("session TTL expired")
            return
        phase = council.get("phase")
        phase_age = self.frameTick - council.get("phaseFrame", council.get("frame", self.frameTick))
        expired = phase_age >= DAILY_COUNCIL_PHASE_TTL_FRAMES
        if phase == "convening":
            if self._daily_council_all_seated(council) or expired:
                self._set_daily_council_phase(council, "discussion")
        elif phase == "discussion":
            total_turns = len(council.get("speakingOrder") or []) * council.get(
                "maxRounds", DAILY_COUNCIL_DISCUSSION_ROUNDS)
            council["round"] = min(
                council.get("maxRounds", DAILY_COUNCIL_DISCUSSION_ROUNDS),
                council.get("nextSpeakerIdx", 0) // max(1, len(council.get("speakingOrder") or [])),
            )
            if council.get("nextSpeakerIdx", 0) >= total_turns or expired:
                self._set_daily_council_phase(council, "proposal")
        elif phase == "proposal":
            if council.get("ballot"):
                self._set_daily_council_phase(council, "voting")
            elif expired:
                self._set_daily_council_phase(council, "verdict")
        elif phase == "voting":
            ballot = council.get("ballot") or {}
            tally = self._daily_council_tally(council)
            all_voted = len(ballot.get("votes") or {}) >= len(council.get("attendees") or [])
            if ballot.get("kind") == "succession":
                ready = all_voted or expired
            else:
                quorum = int(ballot.get("quorum") or 1)
                ready = tally["yes"] >= quorum or tally["no"] >= quorum \
                    or all_voted or expired
            if ready:
                verdict = self._resolve_daily_council_ballot(
                    council, "phase TTL" if expired else "vote complete")
                if verdict is not None:
                    self._set_daily_council_phase(council, "verdict")
        elif phase == "verdict":
            if council.get("elderVerdictSpoken") or expired:
                self._set_daily_council_phase(council, "adjourned")
        elif phase == "adjourned":
            self._adjourn_daily_council("completed")

    # --- Phase D invention council (diegetic LLM-council; TECH_TREE_ENABLED) ---
    def _council_active(self):
        if not TECH_TREE_ENABLED:
            return None
        return self.civilization.get("councilActive")

    def _record_council_proposal(self, agent, bp, decision):
        """A propose_blueprint that lands while a council is in session becomes
        part of the debate record, and appears in-world as a speech bubble
        (staged debate: existing message/bubble mechanics only)."""
        c = self.civilization
        council = c.get("councilActive")
        if not council:
            return
        council.setdefault("proposals", []).append({
            "proposer": agent["name"], "id": bp["id"], "name": bp["name"],
            "needs": dict(bp["needs"]),
            "function_summary": self._function_summary(bp.get("function")),
        })
        if not decision.get("message"):
            agent["message"] = f"I propose the {bp['name']}!"
            agent["messageTimer"] = 240
        self._push_activity(
            f"Council: {agent['name']} lays the {bp['name']} before the elder "
            f"({len(council['proposals'])} proposal(s) on the table)")
        self._append_council_transcript({
            "type": "proposal",
            "frame": self.frameTick,
            "proposer": agent["name"],
            "blueprint_id": bp["id"],
            "blueprint_name": bp["name"],
            "function_summary": self._function_summary(bp.get("function")),
            "needs": dict(bp.get("needs") or {}),
            "message": decision.get("message") or agent.get("message"),
            "reasoning": str(decision.get("reasoning") or "")[:500],
        })

    def _clear_invention_retry_flags(self, council):
        """Clear the per-agent one-shot invention-retry guard (see
        _think_job's same-window retry) for every proposer once their
        council session ends -- verdict or TTL dissolve -- so the flag
        doesn't carry over and silently block a retry in a future council."""
        if not council:
            return
        for name in council.get("proposers") or []:
            member = self._find_agent(name)
            if member:
                member["inventionRetryUsed"] = False

    def _council_reject_pending(self, elder, target_id, reason):
        """Reject one pending blueprint as part of a comparative verdict:
        pops it, records the rejection (amnesty clock included), and routes
        the reason back to the proposer's next prompt -- the same feedback
        loop a standalone reject_blueprint uses, plus the per-candidate
        reason the council pattern requires."""
        c = self.civilization
        idx = next((i for i, p in enumerate(c["pendingBlueprints"])
                    if p["id"] == target_id), -1)
        if idx == -1:
            return None
        bp = c["pendingBlueprints"].pop(idx)
        c["rejectedBlueprintIds"].add(bp["id"])
        c.setdefault("rejectedBlueprintFrames", {})[bp["id"]] = self.frameTick
        proposer = self._find_agent(bp.get("proposedBy"))
        if proposer:
            proposer["lastBlueprintRejection"] = {
                "reason": f"the elder chose another design: {reason}",
                "frame": self.frameTick}
        return bp

    def _record_council_verdict(self, elder, approved_bp, decision):
        """Conclude a comparative judgment: process the optional
        verdict.rejections map (reject-the-rest-with-reasons in the same
        decision), log the comparison as a village event, persist the debate
        to councilLog (the viewer's Council panel), and stage the verdict
        as a longer-lived elder speech bubble."""
        c = self.civilization
        council = c.get("councilActive")
        verdict = decision.get("verdict")
        rejections = {}
        if isinstance(verdict, dict) and isinstance(verdict.get("rejections"), dict):
            for rid, reason in verdict["rejections"].items():
                if rid == approved_bp["id"]:
                    continue
                reason = str(reason or "the approved design served the village better")[:160]
                if self._council_reject_pending(elder, rid, reason):
                    rejections[rid] = reason
        if not council and not rejections:
            return  # plain single-blueprint approval: not a council event
        # Build the debate record. Prefer the live council's proposal list;
        # fall back to what we know (approved + rejected candidates).
        proposals = list((council or {}).get("proposals") or [])
        known_ids = {p["id"] for p in proposals}
        for bp in [approved_bp]:
            if bp["id"] not in known_ids:
                proposals.append({
                    "proposer": bp.get("proposedBy", "?"), "id": bp["id"],
                    "name": bp["name"], "needs": dict(bp.get("needs") or {}),
                    "function_summary": self._function_summary(bp.get("function")),
                })
        loser_names = [rid for rid in rejections]
        outcome = f"{approved_bp['name']} approved"
        if loser_names:
            outcome += f"; {len(loser_names)} rejected"
            first_reason = rejections[loser_names[0]]
            self._push_activity(
                f"Elder {elder['name']} chose the {approved_bp['name']} over "
                f"{', '.join(loser_names)}: {first_reason}")
        end_frame = self.frameTick
        start_frame = (council or {}).get("frame")
        transcript = list((council or {}).get("transcript") or [])
        losers_part = f" over {', '.join(loser_names)}" if loser_names else ""
        if not decision.get("message"):
            elder["message"] = (f"The council has spoken: we build the "
                                f"{approved_bp['name']}{losers_part}!")
        elder["messageTimer"] = 480
        transcript.append(self._stamp_council_event({
            "type": "verdict",
            "elder": elder["name"],
            "approved_id": approved_bp["id"],
            "approved_name": approved_bp["name"],
            "rejections": dict(rejections),
            "message": decision.get("message") or elder.get("message"),
            "reasoning": str(decision.get("reasoning") or "")[:500],
        }))
        record = {
            "frame": start_frame or end_frame,
            "end_frame": end_frame,
            "start_frame": start_frame,
            "ts": datetime.now(timezone.utc).isoformat(),
            "started_ts": (council or {}).get("ts"),
            "trigger": (council or {}).get("trigger") or "elder_review",
            "proposers": list((council or {}).get("proposers") or []),
            "proposals": proposals,
            "verdict": {"approved_id": approved_bp["id"],
                        "reasons_per_candidate": rejections},
            "outcome": outcome,
            "transcript": transcript,
        }
        log = c.setdefault("councilLog", [])
        log.insert(0, record)
        del log[COUNCIL_LOG_CAP:]
        if council:
            self._clear_invention_retry_flags(council)
            c["councilActive"] = None

    def _maybe_dissolve_council(self):
        """A council whose verdict never lands (elder offline, proposals all
        invalid) dissolves after COUNCIL_TTL_FRAMES -- the deterministic
        escape from a stuck councilActive state. Pending proposals stay in
        pendingBlueprints for the normal (non-comparative) review path."""
        if not TECH_TREE_ENABLED:
            return
        c = self.civilization
        council = c.get("councilActive")
        if not council:
            return
        if self.frameTick - council.get("frame", 0) < COUNCIL_TTL_FRAMES:
            return
        end_frame = self.frameTick
        transcript = list(council.get("transcript") or [])
        transcript.append(self._stamp_council_event({
            "type": "dissolve",
            "message": "dissolved without a verdict",
        }))
        record = {
            "frame": council.get("frame", end_frame),
            "end_frame": end_frame,
            "start_frame": council.get("frame"),
            "ts": datetime.now(timezone.utc).isoformat(),
            "started_ts": council.get("ts"),
            "trigger": council.get("trigger") or "invention_backstop",
            "proposers": list(council.get("proposers") or []),
            "proposals": list(council.get("proposals") or []),
            "verdict": None,
            "outcome": "dissolved without a verdict",
            "transcript": transcript,
        }
        log = c.setdefault("councilLog", [])
        log.insert(0, record)
        del log[COUNCIL_LOG_CAP:]
        self._clear_invention_retry_flags(council)
        c["councilActive"] = None
        self._push_activity("The invention council disperses without a verdict")

    # --- invention-demand backstop (#5.2) ---
    def _maybe_invention_backstop(self):
        """Deterministic elder backstop, same tick-gated _maybe_* shape as
        _maybe_advance_rules/_maybe_start_idle_district_project: once
        _invention_required() has held true for INVENTION_BACKSTOP_STREAK
        consecutive elder turns (the streak is tracked in
        civilization["inventionRequiredStreak"], incremented in
        _schedule_think whenever the elder is dispatched to think, reset on
        every non-required turn or successful propose_blueprint) and no
        blueprint is currently pending, direct the most-idle villager to
        invent one -- and flag that villager's next think as an
        invention-only turn (slim, proposal-focused prompt; see
        _build_think_payload / server build_invention_prompt). After
        INVENTION_ELDER_TAKEOVER delegations with no valid proposal landing
        (counted in civilization["inventionBackstopFires"], reset on every
        accepted proposal), or when no villager is free to task, the elder
        takes the invention-only turn himself. The blueprint's actual content
        still comes from the LLM either way."""
        c = self.civilization
        if c.get("inventionRequiredStreak", 0) < INVENTION_BACKSTOP_STREAK:
            return
        if c["pendingBlueprints"]:
            return
        if TECH_TREE_ENABLED and c.get("councilActive"):
            return  # a council is already deliberating
        elder = next((a for a in self.agents if a["role"] == "elder" and not a["incapacitated"]), None)
        if not elder:
            return
        if elder.get("deathFrame") is not None:
            return
        if DAILY_COUNCIL_ENABLED:
            # Daily Council subsumes both the legacy fan-out and single-
            # villager delegation. Repeated failures still reach the existing
            # deterministic elder-self-draft escape.
            c["inventionRequiredStreak"] = 0
            fires = c.get("inventionBackstopFires", 0)
            if fires >= INVENTION_ELDER_TAKEOVER:
                c["inventionBackstopFires"] = 0
                elder["inventionTurn"] = True
                self._push_activity(
                    f"Elder {elder['name']} will draft the new blueprint himself.")
            else:
                c["inventionBackstopFires"] = fires + 1
            return
        idle = [a for a in self._idle_agents_for_elder()
                if a["name"] != elder["name"] and not a.get("inventionTurn")]
        if c.get("inventionBackstopFires", 0) >= INVENTION_ELDER_TAKEOVER or not idle:
            c["inventionRequiredStreak"] = 0
            c["inventionBackstopFires"] = 0
            elder["inventionTurn"] = True
            self._push_activity(f"Elder {elder['name']} will draft the new blueprint himself.")
            return
        c["inventionRequiredStreak"] = 0
        c["inventionBackstopFires"] = c.get("inventionBackstopFires", 0) + 1
        if TECH_TREE_ENABLED and len(idle) >= 2:
            # Invention COUNCIL (plan Part 6): 2-3 idle villagers get parallel
            # invention-only turns (each REPLACES that villager's next normal
            # think turn -- no added LLM call volume) and walk to the elder;
            # the elder judges the proposals comparatively when they land.
            members = idle[:INVENTION_COUNCIL_SIZE]
            names = [m["name"] for m in members]
            for m in members:
                m["inventionTurn"] = True
                m["goal"] = None
                m["assignedTask"] = "bring the council a new structure blueprint (propose_blueprint)"
                m["lastTaskedFrame"] = self.frameTick
                self._set_agent_target_to_agent(m, elder["name"])
            c["councilActive"] = {
                "frame": self.frameTick,
                "ts": datetime.now(timezone.utc).isoformat(),
                "trigger": "invention_backstop",
                "proposers": names,
                "proposals": [],
                "transcript": [],
            }
            elder["message"] = f"The council convenes! {', '.join(names)}, bring me your inventions."
            elder["messageTimer"] = 360
            self._append_council_transcript({
                "type": "convene",
                "frame": self.frameTick,
                "elder": elder["name"],
                "proposers": names,
                "message": elder["message"],
            })
            self._push_communication(
                "directive", elder["name"], "everyone",
                f"Invention council: {', '.join(names)} will each draft a blueprint")
            self._push_activity(
                f"Elder {elder['name']} convenes an invention council — "
                f"{', '.join(names)} will each draft a proposal")
            return
        # Legacy single-delegation path (flag off, or only one villager idle --
        # the council never fans out in that case, per the cost guard).
        target = idle[0]
        target["inventionTurn"] = True
        self.apply_decision(elder, {
            "action": "assign_task", "target": target["name"],
            "message": "propose a new structure blueprint -- the village needs a new invention!",
            "reasoning": "All known structures are built and no invention is pending; "
                         "directing the village to invent something new."})
        self._push_activity(f"Elder {elder['name']} demands invention: every known structure is already built.")

    # --- stuck-project relocation backstop ---
    def _maybe_relocate_stuck_project(self):
        """A project active in a district whose build grid has filled up can
        never complete: build_structure fails with "no room left to build"
        forever, the project squats on one of the MAX_CONCURRENT_PROJECTS
        slots, and everything contributed to it is lost. Move such a project
        (contributions included) to a same-kind district that has a free spot
        and no active build. If none exists, do nothing this gate --
        _kind_at_capacity will be true, _maybe_found_district opens new land,
        and a later gate completes the move."""
        c = self.civilization
        for district_id in self._active_project_districts():
            if self._find_structure_spot(district_id) is not None:
                continue
            project = c["districtProjects"][district_id]
            kind = c["districts"][district_id]["kind"]
            dest = next((did for did in self._buildable_district_ids()
                         if did != district_id
                         and c["districts"][did]["kind"] == kind
                         and not c["districtProjects"].get(did)
                         and self._find_structure_spot(did) is not None), None)
            if not dest:
                continue
            project["districtId"] = dest
            c["districtProjects"][dest] = project
            c["districtProjects"][district_id] = None
            c["districtLastContribution"][dest] = self.frameTick
            self._touch_kind_activity(kind)
            self._push_activity(
                f"The {project['name']} build moves to {dest} — {district_id} has no land left")

    # --- agent-driven structure reorganization (fixes footprint overlaps) ---
    def _find_relocation_spot(self, structure):
        """Size-aware relocation destination for `structure`: prefer a free
        spot in its own district (excluding itself from the collision check,
        since it's the thing being moved), else another buildable district of
        the same kind with no active project (mirrors
        _maybe_relocate_stuck_project's same-kind-district fallback).
        Returns (district_id, x, y) or None."""
        footprint = self._structure_footprint(structure)
        own_district = structure.get("districtId")
        if own_district:
            spot = self._find_structure_spot(own_district, footprint=footprint,
                                             ignore_id=structure.get("id"))
            if spot:
                return own_district, spot["x"], spot["y"]
        c = self.civilization
        kind = c["districts"].get(own_district, {}).get("kind") if own_district else None
        if not kind:
            return None
        for did in self._buildable_district_ids():
            if did == own_district:
                continue
            if c["districts"][did]["kind"] != kind:
                continue
            if c["districtProjects"].get(did):
                continue
            spot = self._find_structure_spot(did, footprint=footprint, ignore_id=structure.get("id"))
            if spot:
                return did, spot["x"], spot["y"]
        return None

    def _enqueue_reorg_for_overlaps(self, structure, preferred_agent=None):
        """Enqueue (at most) one relocation task for the smaller of `structure`
        and any structure it overlaps. Ruins are kept in the collision check
        (still occupy their footprint visually) so they can still be the
        mover or the displacer. If a destination can't be found, emit a
        single throttled activity nudge and leave the overlap for the next
        gate/founding cycle to resolve."""
        c = self.civilization
        tasked_ids = {t["structureId"] for t in c["reorgTasks"]}
        if structure.get("id") in tasked_ids:
            return
        for other in c["structures"]:
            if other.get("id") == structure.get("id") or other.get("id") in tasked_ids:
                continue
            if not self._structures_overlapping(structure, other):
                continue
            w1, h1 = self._structure_footprint(structure)
            w2, h2 = self._structure_footprint(other)
            area1, area2 = w1 * h1, w2 * h2
            if area1 < area2:
                mover, displacer = structure, other
            elif area2 < area1:
                mover, displacer = other, structure
            else:
                # Tie: relocate the higher id (the newer/duplicate one).
                mover, displacer = (structure, other) if structure["id"] > other["id"] \
                    else (other, structure)
            mover_name = mover.get("name") or mover.get("type")
            dest = self._find_relocation_spot(mover)
            if not dest:
                if self.frameTick - c.get("lastReorgNoRoomFrame", 0) >= REORG_NO_ROOM_NUDGE_FRAMES:
                    c["lastReorgNoRoomFrame"] = self.frameTick
                    self._push_activity(
                        f"No room to relocate the {mover_name} -- it stays crowded for now")
                continue
            to_district, to_x, to_y = dest
            task = {
                "structureId": mover["id"], "toDistrict": to_district,
                "toX": to_x, "toY": to_y,
                "displacedBy": displacer.get("name") or displacer.get("type"),
                "assignedTo": None, "workLeft": 3, "createdFrame": self.frameTick,
            }
            c["reorgTasks"].append(task)
            if (preferred_agent is not None and preferred_agent.get("role") != "elder"
                    and not preferred_agent.get("incapacitated")
                    and not preferred_agent.get("reorgTask")
                    and preferred_agent.get("deathFrame") is None):
                task["assignedTo"] = preferred_agent["name"]
                preferred_agent["reorgTask"] = mover["id"]
                self._push_activity(
                    f"{preferred_agent['name']} sets out to relocate the {mover_name} — "
                    f"the {task['displacedBy']} has outgrown its plot")
            return  # one task enqueued per call

    def _maybe_reorganize_structures(self):
        """Periodic backstop (~every REORG_CHECK_FRAMES): keeps at most one
        reorg task in flight -- reassigns a task whose agent died/collapsed,
        assigns an unassigned task (preferring the builder, else the nearest
        able agent), and, when no task is pending, scans all structure pairs
        for a footprint overlap to enqueue. The elder and any current
        sage-emergency responder are never assigned -- Sage priority stays
        absolute."""
        c = self.civilization
        if self.frameTick - c.get("lastReorgCheckFrame", 0) < REORG_CHECK_FRAMES:
            return
        c["lastReorgCheckFrame"] = self.frameTick
        em_target = self._sage_emergency()
        protected = self._sage_responders(em_target) if em_target else set()

        def unavailable(a):
            return (a["role"] == "elder" or a["incapacitated"]
                    or a.get("deathFrame") is not None or a["name"] in protected)

        tasks = c["reorgTasks"]
        if tasks:
            task = tasks[0]
            assignee = self._find_agent(task["assignedTo"]) if task.get("assignedTo") else None
            if task.get("assignedTo") and (not assignee or assignee["incapacitated"]
                                            or assignee.get("deathFrame") is not None):
                if assignee:
                    assignee["reorgTask"] = None
                task["assignedTo"] = None
            if not task.get("assignedTo"):
                structure = next((s for s in c["structures"]
                                  if s.get("id") == task["structureId"]), None)
                if not structure:
                    tasks.remove(task)
                    return
                candidate = next((a for a in self.agents if a["role"] == "builder"
                                  and not unavailable(a) and not a.get("reorgTask")), None)
                if not candidate:
                    nearest, nearest_d = None, float("inf")
                    for a in self.agents:
                        if unavailable(a) or a.get("reorgTask"):
                            continue
                        dd = _dist(a["x"], a["y"], structure.get("x", 0), structure.get("y", 0))
                        if dd < nearest_d:
                            nearest_d, nearest = dd, a
                    candidate = nearest
                if candidate:
                    task["assignedTo"] = candidate["name"]
                    candidate["reorgTask"] = structure["id"]
                    name = structure.get("name") or structure.get("type")
                    self._push_activity(
                        f"{candidate['name']} sets out to relocate the {name} — "
                        f"the {task['displacedBy']} has outgrown its plot")
            return
        # No task in flight: scan for the first overlapping pair and enqueue.
        structures = c["structures"]
        for i, s1 in enumerate(structures):
            for s2 in structures[i + 1:]:
                if self._structures_overlapping(s1, s2):
                    self._enqueue_reorg_for_overlaps(s1)
                    return

    def _step_reorg(self, agent):
        """Deterministic reorg stepping, modeled on _rush_to_heal: walk to the
        tasked structure, work a fixed timer, then rewrite its position once
        a destination is (re-)confirmed still free. Never lets a reorg-tasked
        agent fall through to LLM thinking -- the per-agent tick loop calls
        this instead of dispatching a think job while agent['reorgTask'] is set."""
        c = self.civilization
        structure_id = agent.get("reorgTask")
        task = next((t for t in c["reorgTasks"] if t["structureId"] == structure_id), None)
        structure = next((s for s in c["structures"] if s.get("id") == structure_id), None)
        if not task or not structure:
            if task:
                c["reorgTasks"].remove(task)
            agent["reorgTask"] = None
            return
        if agent["incapacitated"]:
            task["assignedTo"] = None
            agent["reorgTask"] = None
            return
        fw, fh = self._structure_footprint(structure)
        sx = structure.get("x", 0) + fw / 2
        sy = structure.get("y", 0) + fh / 2
        if _dist(agent["x"], agent["y"], sx, sy) > 80:
            agent["targetX"] = sx
            agent["targetY"] = sy
            agent["waypoints"] = []
            return
        task["workLeft"] = task.get("workLeft", 3) - 1
        if task["workLeft"] > 0:
            return
        # Work complete -- re-validate the destination (something else may
        # have claimed the slot since the task was enqueued).
        to_district = task["toDistrict"]
        spot = self._find_structure_spot(to_district, footprint=(fw, fh),
                                         ignore_id=structure.get("id"))
        if spot:
            tx, ty = spot["x"], spot["y"]
        else:
            dest = self._find_relocation_spot(structure)
            if not dest:
                name = structure.get("name") or structure.get("type")
                self._push_activity(
                    f"{agent['name']} finds no room to relocate the {name} -- giving up for now")
                c["reorgTasks"].remove(task)
                agent["reorgTask"] = None
                return
            to_district, tx, ty = dest
        structure["x"] = tx
        structure["y"] = ty
        structure["districtId"] = to_district
        name = structure.get("name") or structure.get("type")
        self._push_activity(
            f"{agent['name']} relocated the {name} to make room for the {task['displacedBy']}")
        self._log_benchmark("structure_relocated", structure["id"],
                            {"structure": structure.get("type"), "district": to_district})
        c["reorgTasks"].remove(task)
        agent["reorgTask"] = None
        c["lastReorgFrame"] = self.frameTick

    # --- district founding (the open-world mechanism) ---
    def _district_counts_as_full(self, district_id):
        c = self.civilization
        if c["districtProjects"].get(district_id) and \
                self._project_squatting_past_abandon_threshold(district_id):
            return True
        d = c["districts"][district_id]
        if d.get("build_grid"):
            return self._district_structure_count(district_id) >= d["build_grid"]["cap"]
        return False

    def _kind_at_capacity(self, kind):
        ids = [did for did, d in self.civilization["districts"].items()
               if d["kind"] == kind and d.get("build_grid")]
        if not ids:
            return False
        return all(self._district_counts_as_full(did) for did in ids)

    def _claim_frontier_plot(self):
        for plot in self.civilization["frontierPlots"]:
            if not plot["claimed"]:
                return plot
        return None

    def _claim_adjacent_frontier_pair(self):
        """Return (ocean_plot, beach_plot) for two unclaimed plots sharing an edge.

        Prefers horizontal pairs with lower x = ocean (west water), higher x = beach
        (east sand), matching starter coast geometry. Vertical fallback: lower y =
        ocean (north water), higher y = beach (south sand).
        """
        unclaimed = [p for p in self.civilization["frontierPlots"] if not p["claimed"]]
        for left in unclaimed:
            for right in unclaimed:
                if left is right:
                    continue
                if (left["y1"] == right["y1"] and left["y2"] == right["y2"]
                        and left["x2"] == right["x1"]):
                    return left, right
                if (left["y1"] == right["y1"] and left["y2"] == right["y2"]
                        and right["x2"] == left["x1"]):
                    return right, left
        for north in unclaimed:
            for south in unclaimed:
                if north is south:
                    continue
                if (north["x1"] == south["x1"] and north["x2"] == south["x2"]
                        and north["y2"] == south["y1"]):
                    return north, south
                if (north["x1"] == south["x1"] and north["x2"] == south["x2"]
                        and south["y2"] == north["y1"]):
                    return south, north
        return None, None

    def _found_district(self, kind, tmpl, plot, *, shore_gate=None):
        c = self.civilization
        n = sum(1 for d in c["districts"].values() if d["kind"] == kind) + 1
        did = f"{kind}_{n}"
        while did in c["districts"]:
            n += 1
            did = f"{kind}_{n}"
        grid_t = tmpl.get("grid")
        bounds = {"x1": plot["x1"], "y1": plot["y1"], "x2": plot["x2"], "y2": plot["y2"]}
        entry_node = shore_gate or f"{did}_gate"
        cx, cy = (bounds["x1"] + bounds["x2"]) / 2, (bounds["y1"] + bounds["y2"]) / 2
        nearest = None if shore_gate else self._nearest_road_node(cx, cy)
        if "label" in tmpl:
            label = tmpl["label"]
        else:
            label = f"{kind.upper()} {n}"
        build_grid = None
        if grid_t:
            build_grid = {
                "x0": bounds["x1"] + 20, "y0": bounds["y1"] + 40,
                "cols": grid_t["cols"], "dx": grid_t["dx"], "dy": grid_t["dy"], "cap": grid_t["cap"],
            }
        c["districts"][did] = {
            "kind": kind, "tile": tmpl["tile"], "label": label,
            "bounds": bounds, "build_grid": build_grid, "entryNode": entry_node,
        }
        c["districtProjects"][did] = None
        c["districtLastContribution"][did] = self.frameTick
        plot["claimed"] = True
        plot["claimedBy"] = did
        if not shore_gate:
            c["roadNodes"][entry_node] = {"x": cx, "y": cy}
            if nearest:
                c["roadEdges"].append([entry_node, nearest])
        c["lastDistrictFoundFrame"] = self.frameTick
        self._recompute_road_paths()
        _validate_districts(c["districts"])
        _validate_road_graph(c["roadNodes"], c["roadEdges"])
        self._push_activity(f"The village claims new land in the frontier for a {kind} district ({did}).")
        self._log_benchmark("district_founded", len(c["districts"]), {"id": did, "kind": kind})
        if FOUNDING_EVENTS_ENABLED and label:
            self._push_chronicle(f"{label} was founded on the frontier.", kind="district_founded")
        if ECOLOGY_ENABLED:
            self._ensure_district_stocks()
            new_stocks = self._init_district_stocks({did: c["districts"][did]}, c["resourceRegistry"])
            c["districtStocks"].update(new_stocks)
        self._bump_districts_epoch()
        return did

    def _found_coastal_pair(self, ocean_plot, beach_plot):
        c = self.civilization
        if len(c["districts"]) + 2 > MAX_TOTAL_DISTRICTS:
            return False
        beach_did = self._found_district(
            "beach", COASTAL_PAIR_BEACH_TEMPLATE, beach_plot)
        shore_gate = c["districts"][beach_did]["entryNode"]
        self._found_district(
            "ocean", OCEAN_DISTRICT_TEMPLATE, ocean_plot, shore_gate=shore_gate)
        self._push_activity(
            "The village extends the coast — new ocean and beach districts "
            f"({ocean_plot['id']} + {beach_plot['id']}).")
        return True

    def _try_found_coastal_pair(self):
        ocean_plot, beach_plot = self._claim_adjacent_frontier_pair()
        if not ocean_plot:
            if not self.civilization.get("coastalPairExhaustedLogged"):
                self._push_activity(
                    "The frontier has no adjacent unclaimed plots for a coastal expansion.")
                self.civilization["coastalPairExhaustedLogged"] = True
            return False
        return self._found_coastal_pair(ocean_plot, beach_plot)

    def _maybe_found_district(self):
        """Deterministic, tick-gated backstop (same shape as
        _maybe_advance_rules/_maybe_force_contribution) that founds a new
        district of a buildable kind once every existing district of that
        kind is at/near capacity AND that kind's contribution activity keeps
        stalling -- i.e. the civilization has run out of room to build more
        of something and is actively trying to. This is the mechanism that
        makes the world genuinely open rather than just bigger-but-finite."""
        c = self.civilization
        if len(c["districts"]) >= MAX_TOTAL_DISTRICTS:
            return
        if self.frameTick - c.get("lastDistrictFoundFrame", 0) < DISTRICT_FOUND_STALL_THRESHOLD:
            return
        for kind, tmpl in DISTRICT_KIND_TEMPLATES.items():
            if not self._kind_at_capacity(kind):
                continue
            if self.frameTick - c["kindLastActivityFrame"].get(kind, 0) < DISTRICT_FOUND_STALL_THRESHOLD:
                continue
            plot = self._claim_frontier_plot()
            if not plot:
                # Treat "no unclaimed frontier plot" as a silent no-op (log
                # once) rather than an error -- an extremely distant edge
                # case given the frontier is sized generously relative to
                # MAX_TOTAL_DISTRICTS, but cheap to guard against.
                if not c.get("frontierExhaustedLogged"):
                    self._push_activity("The frontier has no more unclaimed land left to expand into.")
                    c["frontierExhaustedLogged"] = True
                continue
            self._found_district(kind, tmpl, plot)
            return  # one founding per gate check keeps this easy to reason about
        if not self._kind_at_capacity("beach"):
            return
        if self.frameTick - c["kindLastActivityFrame"].get("beach", 0) < DISTRICT_FOUND_STALL_THRESHOLD:
            return
        if len(c["districts"]) + 2 > MAX_TOTAL_DISTRICTS:
            return
        if self._try_found_coastal_pair():
            return

    def _next_rule_seq_token(self, counter_key):
        """Compact monotonic lowercase base36 token for auto-proposed
        rule instance ids, keyed by a persisted civ-dict counter field
        (e.g. "priorityRuleSeq", "taxRuleSeq") -- see those fields' civ-init
        comments for why this is a counter and not the raw frameTick."""
        c = self.civilization
        n = c.get(counter_key, 0) + 1
        c[counter_key] = n
        digits = "0123456789abcdefghijklmnopqrstuvwxyz"
        out = []
        while n:
            n, r = divmod(n, 36)
            out.append(digits[r])
        return "".join(reversed(out)) or "0"

    def _next_priority_rule_seq_token(self):
        """Back-compat wrapper for the priority-rule instance-id counter."""
        return self._next_rule_seq_token("priorityRuleSeq")

    # --- rules backstop ---
    def _maybe_advance_rules(self):
        if not RULES_ENABLED:
            return
        if FACTION_SPLIT_ENABLED:
            self._maybe_trigger_faction_split()
        c = self.civilization
        if LIFECYCLE_ENABLED and c.get("pendingSuccession"):
            if DAILY_COUNCIL_ENABLED:
                # The emergency assembly owns visible candidate discussion and
                # voting. Never manufacture repeated yes votes for whichever
                # rule-shaped candidate record happens to be listed first.
                return
            # Election backstop: cast a ballot for the first still-eligible
            # candidate found (deterministic, round-robins across candidates
            # rule-by-rule rather than always favoring pendingRules[0], so a
            # quiet soak doesn't mechanically crown whichever candidate
            # happened to be listed first). This guarantees the arc completes
            # even with zero organic LLM votes; _maybe_resolve_stalled_succession
            # is the final backstop if even this never fires.
            succession_rules = [r for r in c["pendingRules"] if r["kind"] == "succession"]
            for pending in succession_rules:
                eligible = [a for a in self.agents
                           if not a["incapacitated"] and a["role"] != "elder"
                           and a["name"] not in pending["votes"]]
                voter = next((a for a in eligible if self._is_idle(a)), None) or (eligible[0] if eligible else None)
                if voter:
                    self.apply_decision(voter, {
                        "action": "vote_rule", "target": pending["id"], "vote": "yes",
                        "reasoning": f'Casting my vote for {pending["candidateName"]} as the new elder.'})
                    return
            return
        elder = next((a for a in self.agents if a["role"] == "elder" and not a["incapacitated"]), None)
        elder_sid = self._settlement_id_for_agent(elder) if elder and FACTION_SPLIT_ENABLED else None
        pending_list = self._pending_for_settlement(elder_sid) if elder_sid else c["pendingRules"]
        domestic_pending = [
            r for r in pending_list if not self._is_global_governance_ballot(r)
        ]
        pending = domestic_pending[0] if domestic_pending else None
        if pending:
            ballot_sid = self._ballot_settlement_id(pending)
            if FACTION_SPLIT_ENABLED and not self._is_global_governance_ballot(pending):
                eligible = [a for a in self.agents
                            if not a["incapacitated"] and a["role"] != "elder"
                            and a["name"] not in pending["votes"]
                            and self._settlement_id_for_agent(a) == ballot_sid]
            else:
                eligible = [a for a in self.agents
                            if not a["incapacitated"] and a["role"] != "elder"
                            and a["name"] not in pending["votes"]]
            voter = next((a for a in eligible if self._is_idle(a)), None) or (eligible[0] if eligible else None)
            if voter:
                biased = self._belief_biased_vote(voter, pending)
                if biased is not None:
                    vote = biased
                else:
                    vote = "no" if (pending["kind"] == "resource_tax" and (pending.get("value") or 0) > 2) else "yes"
                reason = f'Casting my vote on the proposed rule "{pending["name"]}".'
                if biased is not None:
                    reason = f'My beliefs lead me to vote {vote} on "{pending["name"]}".'
                self.apply_decision(voter, {"action": "vote_rule", "target": pending["id"],
                                            "vote": vote,
                                            "reasoning": reason})
            return
        last_rule_activity = max(c["lastRuleActivityFrame"], c.get("lastRuleAttemptFrame", 0))
        if self.frameTick - last_rule_activity < RULE_PROPOSE_COOLDOWN:
            return
        elder = next((a for a in self.agents if a["role"] == "elder" and not a["incapacitated"]), None)
        if not elder:
            return
        elder_sid = self._settlement_id_for_agent(elder) if FACTION_SPLIT_ENABLED else None
        # Living-ecosystem Phase 5 (WEATHER_GOVERNANCE_ENABLED): a storm
        # driving an affected district's ecology below STOCK_LOW_RATIO gets a
        # deterministic emergency response -- an auto-proposed "rationing"
        # rule (an EXISTING RULE_KIND, LIFECYCLE_ENABLED already default
        # True; never a new kind). Checked first (ahead of the routine
        # tax/priority churn below) so a real emergency preempts ordinary
        # governance for this cooldown window, but it only ever fires when
        # the condition is real -- same shared RULE_PROPOSE_COOLDOWN/
        # lastRuleAttemptFrame gate above applies to every branch here, so
        # this cannot out-cadence the others. _active_or_pending_rationing
        # additionally stops it from re-proposing every cooldown window while
        # a lingering storm keeps the condition true (governance-churn
        # guard -- see plan-living-ecosystem-5's "governance churn" risk).
        if WEATHER_GOVERNANCE_ENABLED and WEATHER_ENABLED and LIFECYCLE_ENABLED:
            w = c.get("weather") or {}
            if w.get("state") in ("storm", "clearing") and not self._active_or_pending_rationing(elder):
                storm_districts = w.get("districts") or []
                scarce = any(
                    (ratio := self._district_ecology_ratio(did)) is not None and ratio < STOCK_LOW_RATIO
                    for did in storm_districts
                )
                if scarce:
                    # Rule ids are globally non-reusable (see
                    # _ensure_constitution) -- mint a unique per-enactment
                    # instance id via the same shared counter helper as the
                    # priority/tax branches, NEVER a deterministic id (that
                    # would recreate the permanently-blocked-id loop fixed in
                    # 6a78162 the moment this rationing rule is ever
                    # repealed).
                    rule = {
                        "id": f"emerg_{self._next_rule_seq_token('emergencyRuleSeq')}",
                        "name": "Storm Emergency Rationing",
                        "kind": "rationing",
                        "value": max(1, RATIONING_WITHDRAW_CAP - 1),
                        "description": "Storm-driven scarcity: cap stockpile withdrawals until supplies recover.",
                    }
                    if self._validate_rule(rule, agent=elder):
                        self.apply_decision(elder, {
                            "action": "propose_rule",
                            "rule": rule,
                            "reasoning": "The storm has driven local stocks critically low -- "
                                         "proposing emergency rationing.",
                        })
                        return
                    # Defensive: known-invalid (e.g. pending/active rule queue
                    # full) -- don't knowingly emit an invalid action; still
                    # mark this as an attempt so the cooldown advances
                    # normally (same discipline as every other branch here).
                    c["lastRuleAttemptFrame"] = self.frameTick
                    return
        # Sid-parity Phase 2: once a tax exists, propose a priority rule for
        # the scarcest unmet build resource (or wood) so governance diversifies.
        if self._active_resource_tax(elder) > 0 and not self._active_priority_resource(elder):
            unmet = self._first_unmet_resource_anywhere() or "wood"
            if unmet in c["resourceRegistry"]:
                # Rule ids are globally non-reusable (see _ensure_constitution),
                # so a repealed "priority_<resource>" id can never validate
                # again -- give every enactment attempt its own instance id
                # (priority lookup keys off kind=="priority" + value, not id,
                # so this is safe for _active_priority_resource consumers).
                rule = {
                    "id": f"priority_{unmet}_{self._next_priority_rule_seq_token()}",
                    "name": f"{unmet.title()} Priority",
                    "kind": "priority",
                    "value": unmet,
                    "description": f"Contributors prioritize delivering {unmet} to active builds.",
                }
                if self._validate_rule(rule, agent=elder):
                    self.apply_decision(elder, {
                        "action": "propose_rule",
                        "rule": rule,
                        "reasoning": f"Proposing a priority rule so the village focuses on {unmet}.",
                    })
                    return
                # Defensive: known-invalid (e.g. pending/active rule queue
                # full) -- don't knowingly emit an invalid action; still mark
                # this as an attempt so the cooldown advances normally.
                c["lastRuleAttemptFrame"] = self.frameTick
                return
        # If several rules are stacked, propose repealing the oldest non-tax
        # rule so amendment is exercised (Sid's amendable-rules benchmark).
        # Age-gated (RULE_REPEAL_MIN_AGE_FRAMES): without this, tax+priority --
        # the normal 2-rule steady state -- meant this branch fired the very
        # next cooldown window after the propose branch ever enacted a
        # priority rule, undoing it immediately and oscillating forever.
        rules_for_elder = self._rules_for_settlement(elder_sid) if elder_sid else c["rules"]
        non_tax = [r for r in rules_for_elder if r.get("kind") != "resource_tax"]
        repeal_eligible = [r for r in non_tax
                          if self.frameTick - r.get("enactedFrame", 0) >= RULE_REPEAL_MIN_AGE_FRAMES]
        if len(rules_for_elder) >= 2 and repeal_eligible:
            target = repeal_eligible[0]
            self.apply_decision(elder, {
                "action": "repeal_rule",
                "target": target["id"],
                "reasoning": f'Repealing outdated rule "{target["name"]}" to keep village law lean.',
            })
            return
        if self._active_resource_tax(elder) > 0:
            return
        # Rule ids are globally non-reusable (see _ensure_constitution), so a
        # repealed "resource_tax" id (an LLM-driven repeal_rule can target it
        # even though the deterministic repeal branch above excludes it via
        # non_tax) could never validate again -- give every enactment attempt
        # its own instance id, exactly like the priority-rule fix above.
        # _active_resource_tax() keys off kind=="resource_tax", not id, so
        # this is safe for that (and every other) consumer.
        tax_rule = {
            "id": f"resource_tax_{self._next_rule_seq_token('taxRuleSeq')}",
            "name": "Resource Tax", "kind": "resource_tax",
            "value": 1, "description": "Contributors add 1 of the same resource to a shared stockpile.",
        }
        if self._validate_rule(tax_rule, agent=elder):
            self.apply_decision(elder, {
                "action": "propose_rule",
                "rule": tax_rule,
                "reasoning": "Proposing a small resource tax to build a shared village stockpile."})
            return
        # Defensive: known-invalid (e.g. pending/active rule queue full) --
        # don't knowingly emit an invalid action; still mark this as an
        # attempt so the cooldown advances normally.
        c["lastRuleAttemptFrame"] = self.frameTick

