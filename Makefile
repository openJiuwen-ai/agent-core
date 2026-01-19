# Cross-platform Makefile (Windows-friendly)
.PHONY: help install has-staged-changes format lint pylint spelling fix-format fix-lint type-check check fix
.DEFAULT_GOAL := help

# Less noisy
MAKEFLAGS += --no-print-directory

LINESEP := ------------------------------------------------------------------
DEPENDENCIES := "ruff>=0.11.2" "pylint>=3.0.0" "mypy>=1.12.0" "types-requests" "codespell>=2.2.4"

# SHELL = sh.exe / cmd.exe for PowerShell and cmd.exe on Windows, not anywhere else (even Git Bash)
ifeq ($(filter cmd.exe sh.exe,$(SHELL)),$(SHELL))
	START := 
	END := 
	NULL := NUL
	BLANK := & echo.
	FAIL_CMD := exit /b 1
else
	START := "
	END := "
	NULL := /dev/null
	BLANK := ; echo ""
	FAIL_CMD := exit 1
endif

# Check last COMMITS commits if COMMITS > 0
COMMITS ?= 0

# If COMMITS > 0, treat it as "check last N commits"
ifneq ($(filter-out 0,$(COMMITS)),)
	DIFF_OPTION := HEAD~$(COMMITS)..
else
	DIFF_OPTION := --cached
endif

# Get changed files from git, then filter to .py/.pyi using Make
CHANGED_FILES_RAW := $(strip $(shell git diff --name-only $(DIFF_OPTION) --diff-filter=ACMR 2>$(NULL)))
CHANGED_FILES := $(filter %.py %.pyi,$(CHANGED_FILES_RAW))

# Detect uv
UV_EXISTS := $(strip $(shell uv --version >$(NULL) 2>&1 && echo yes))

help:
	@echo Usage: make [target] [COMMITS=N] $(BLANK)
	@echo If COMMITS=N is specified and greater than 0, check Python files changed in last N commits
	@echo Otherwise the currently staged changes are checked $(BLANK)
	@echo Available targets:
	@echo $(START)    help       - Show this help message$(END)
	@echo $(START)    install    - Install dependencies via uv or pip: ruff, pylint, mypy, codespell$(END)
	@echo $(START)    test       - Execute pytest$(END)
	@echo $(START)    format     - Check formatting of selected Python files via ruff$(END)
	@echo $(START)    lint       - Check linting of selected Python files via ruff$(END)
	@echo $(START)    pylint     - Check linting of selected Python files via pylint: more comprehensive$(END)
	@echo $(START)    spelling   - Check spelling of selected Python files via codespell$(END)
	@echo $(START)    fix-format - Auto-fix formatting errors in selected Python files via ruff$(END)
	@echo $(START)    fix-lint   - Auto-fix linting errors in selected Python files via ruff$(END)
	@echo $(START)    type-check - Type-check selected Python files via mypy$(END)
	@echo $(START)    check      - Run all checks: format, spelling, lint, pylint$(END)
	@echo $(START)    fix        - Run all auto-fixes: fix-lint, fix-format$(END)

install:
ifeq ($(UV_EXISTS),yes)
	@echo [Makefile] Installing dependencies via uv
	@uv pip install $(DEPENDENCIES)
else
	@echo [Makefile] Installing dependencies via pip
	@python -m pip install $(DEPENDENCIES)
endif

test:
	@pytest

# Sanity check - fails if there are no selected Python files
has-staged-changes:
ifeq ($(strip $(CHANGED_FILES)),)
	@echo No Python files selected.
	@echo Make sure you have used git add first, or have set COMMITS to a positive integer. $(BLANK)
	@echo $(LINESEP) $(BLANK)
	@$(MAKE) help
	@$(FAIL_CMD)
endif

format: has-staged-changes
	-@ruff check --select I $(CHANGED_FILES)
	-@ruff format --check $(CHANGED_FILES)

lint: has-staged-changes
	-@ruff check --show-fixes $(CHANGED_FILES)

pylint: has-staged-changes
	-@pylint $(CHANGED_FILES)

spelling: has-staged-changes
	-@codespell $(CHANGED_FILES)

fix-format: has-staged-changes
	-@ruff check --select I --fix $(CHANGED_FILES)
	-@ruff format $(CHANGED_FILES)

fix-lint: has-staged-changes
	-@ruff check --fix $(CHANGED_FILES)

type-check: has-staged-changes
	-@mypy $(CHANGED_FILES)

check: has-staged-changes
	@echo $(LINESEP)
	@echo [Makefile] Checking code format...
	-@$(MAKE) format COMMITS=$(COMMITS)
	@echo $(LINESEP)
	@echo [Makefile] Checking spelling...
	-@$(MAKE) spelling COMMITS=$(COMMITS)
	@echo $(LINESEP)
	@echo [Makefile] Checking linting via ruff...
	-@$(MAKE) lint COMMITS=$(COMMITS)
	@echo $(LINESEP)
	@echo [Makefile] Checking linting via pylint...
	-@$(MAKE) pylint COMMITS=$(COMMITS)

fix: has-staged-changes
	@echo $(LINESEP)
	@echo [Makefile] Fixing linting via ruff...
	-@$(MAKE) fix-lint COMMITS=$(COMMITS)
	@echo $(LINESEP)
	@echo [Makefile] Fixing code format...
	-@$(MAKE) fix-format COMMITS=$(COMMITS)
