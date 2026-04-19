---
description: Git commit style, branch naming, PR guidelines, and workflow conventions for agent-core.
language: chinese
paths: []
alwaysApply: true
---

# Git Workflow Rules

## Commit Messages

- Follow conventional commits: `type(scope): description`
  - `feat`: new feature
  - `fix`: bug fix
  - `docs`: documentation only
  - `test`: test additions or fixes
  - `refactor`: code restructure without behavior change
  - `perf`: performance improvement
  - `chore`: tooling, dependencies, CI
- Use imperative mood in commit subject (e.g., "Add feature" not "Added feature").
- Keep subject line under 72 characters.
- Reference issues/PRs in body when applicable.

## Branch Naming

- Feature: `feature/<short-description>`
- Bug fix: `fix/<short-description>`
- Documentation: `docs/<short-description>`
- Avoid generic names like `feature-branch`.

## Before Committing

- Run `make check` (staged files by default; use `COMMITS=N` for last N commits).
- Run targeted tests for changed areas: `make test TESTFLAGS="tests/unit_tests/path/to/test.py"`.
- Do NOT commit real credentials, `.env` files, or secrets.

## Pull Requests

- Keep PRs focused: one logical change per PR.
- Update tests and docs when behavior changes are user-visible.
- Use the PR description template (if provided) or include:
  - What changed and why
  - How to test / verify
  - Breaking changes (if any)

## Making Commits

- Stage files intentionally: do not `git add .` blindly.
- Use `make check COMMITS=N` to check the last N commits without staging.

## Handling Large Changes

- Prefer small, targeted diffs. Do not refactor unrelated areas opportunistically.
- If a change touches multiple subsystems, split into multiple commits.
