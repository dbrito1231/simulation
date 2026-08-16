# Open PR integration conflicts

## PR #22 - Agent Interview

- **Recorded:** 2026-08-14
- **Base:** `origin/main` at `52320db`
- **Branch:** `idea-03-agent-interview`
- **Conflicts:** the obsolete shared decision-audit commit `267941b` conflicted
  in `scripts/idea10_decision_audit_smoke.py`,
  `simulation/_server/decision_audit.py`, `simulation/css/council.css`,
  `simulation/index.html`, `simulation/server.py`,
  `simulation/sim_engine/constants.py`, `simulation/viewer/decision-audit.js`,
  and specs 01/04/11/12. Replaying the Agent Interview commit then conflicted
  in `simulation/server.py`, `simulation/sim_engine/constants.py`,
  `simulation/sim_engine/mixin_snapshot.py`,
  `simulation/viewer/divine-bootstrap.js`,
  `simulation/viewer/divine-modal.js`, `simulation/viewer/polling.js`, and
  specs 01/11.
- **Cause:** PR #22 branched before the ordered integrations of anomaly radar,
  decision audit, World Wiki, Chronicle Saga, Dynasty Tree, and Prediction
  Market, and carried an older copy of decision audit.
- **Resolution:** omit the duplicate decision-audit commit; retain the current
  `main` implementations and catalogs; layer Agent Interview's route, flag,
  read-only UI, specs, and smoke on top. Shared decision-audit cache, static
  asset registration, import cleanup, and disabled-poll behavior remain
  inherited exactly once.
- **Status:** resolved on the PR branch; merge remains gated on independent
  review, clean rehearsal, host regression checks, and Docker validation.
