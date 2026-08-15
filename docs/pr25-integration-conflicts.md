# PR #25 integration conflicts

PR #25 (`idea-05-raiders-contagion`) was rebased onto `origin/main` at
`0a116c1` on 2026-08-14. The obsolete shared decision-audit commit `267941b`
was excluded; only the raiders/contagion feature commit was replayed.

Six content conflicts were resolved under the user's authorization:

| File | Current-main content preserved | PR #25 content added |
|---|---|---|
| `simulation/viewer/setup.js` | `DYNASTY_TREE_ENABLED` | `RAIDERS_CONTAGION_ENABLED` |
| `simulation/viewer/state.js` | `saga` delta projection | `pressureTelegraph` delta projection |
| `specs/01-architecture.md` | Current flag catalog | Raiders/contagion flag and reconciled total |
| `specs/06-agents.md` | Current `/state` snapshot contract | Infection fields and projection |
| `specs/08-systems-economy.md` | World Wiki economy pages | Raid/contagion economy mechanics |
| `specs/10-path1.md` | World Wiki settlement/treaty pages | Raiders/contagion Path 1 contract |

The reconciliation preserves all features already merged through PR #22 and
does not reintroduce the old decision-audit implementation. Required host
smokes must pass before PR #25 is eligible for review or merge.
