---
name: verification-loop
description: Formalizes agent-core's make check/type-check/test/fix pipeline into a structured 6-phase verification skill.
disable-model-invocation: true
---

# Verification Loop

Runs the full verification pipeline before every meaningful commit.
Use this skill whenever you have completed a feature, fixed a bug, or
are about to submit a PR.

## When to Run

- Before every commit (or `git push`)
- After any significant change spanning multiple modules
- After dependency changes (`pyproject.toml`, `requirements*.txt`)
- Before marking a PR as ready for review

## 6-Phase Verification

Run each phase in order. Stop immediately on failure.

### Phase 1 — Build Check

Verify all modules compile without syntax errors:

```bash
python -m py_compile openjiuwen/
```

### Phase 2 — Type Check

```bash
make type-check
```

Address all `error:` messages. `warning:` messages are acceptable but
should be reduced over time.

### Phase 3 — Lint

```bash
make check
```

Fix Ruff violations. If a rule is a false positive, add a per-line
suppression comment, not a blanket disable in the config.

### Phase 4 — Tests + Coverage

```bash
make test
```

If any test fails, fix the test or the implementation before proceeding.
For `@pytest.mark.slow` tests, run selectively:

```bash
pytest -m "not slow"
```

Verify coverage stays above 80% for all modules under `openjiuwen/core/`
and `openjiuwen/harness/`. If coverage dropped, add tests for the
missing paths.

### Phase 5 — Security Scan

```bash
bandit -r openjiuwen/ -ll
```

Address all HIGH and CRITICAL findings. SECURITY-level findings require
a code change, not a suppression. Only suppress with a documented reason.

### Phase 6 — Diff Review

```bash
git diff --stat
git diff openjiuwen/
```

Review every changed line. Check for:
- No unintended changes (stale debug code, accidental formatting changes)
- All new public APIs have corresponding tests
- Commit message accurately describes the change

## Structured Verification Report

After running the full loop, report the result:

```
Verification Report
==================
Build:     PASS
TypeCheck: PASS (2 warnings)
Lint:      PASS (0 errors)
Tests:     PASS (142 passed, 3 skipped)
Coverage:  PASS (84.3% overall)
Security:  PASS (0 HIGH/CRITICAL)
Diff:      3 files changed, +47 -12
Status:    READY
```

## Continuous Mode (CI)

In CI pipelines, run the full loop without stopping on the first failure.
Collect all failures and report them together:

```bash
# CI mode: run all phases, report all failures at the end
set +e
make type-check 2>&1 | tee /tmp/typecheck.log
make check      2>&1 | tee /tmp/lint.log
make test       2>&1 | tee /tmp/test.log
bandit -r openjiuwen/ -ll 2>&1 | tee /tmp/bandit.log
set -e

# Report consolidated results
```

## Partial Verification

For small, isolated changes, you may skip phases 1-2 if they are clearly
unaffected. Use judgment:

| Change type | Can skip phase 1-2? |
|-------------|---------------------|
| Doc-only change | Yes (phases 3, 4, 5, 6) |
| Test-only change | Yes (phases 3, 4, 6) |
| Source code change | No (all phases) |
| Dependency change | No (all phases, especially 5) |

When skipping phases, state which ones were skipped and why in the
verification report.
