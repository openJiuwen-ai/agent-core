# Cross-platform Makefile for the openJiuwen project (Windows-friendly)
# Feel free to submit feature requests at https://gitcode.com/openJiuwen/agent-core/issues

.PHONY: help dep install update flame speedscope test has-staged-changes format lint pylint spelling fix-format fix-lint type-check check fix

# Less noisy
MAKEFLAGS += --no-print-directory
TESTFLAGS ?= .

PYTHON ?= python
DEP ?=
FLAME ?=
SPEEDSCOPE ?=
LINESEP := ------------------------------------------------------------------
DEPENDENCIES := "ruff>=0.11.2" "pylint>=3.0.0" "mypy>=1.12.0" "codespell>=2.2.4" "pipdeptree" "pyinstrument"

ifneq ($(DEP),)
.DEFAULT_GOAL := dep
else ifneq ($(FLAME),)
.DEFAULT_GOAL := flame
else ifneq ($(SPEEDSCOPE),)
.DEFAULT_GOAL := speedscope
else
.DEFAULT_GOAL := help
endif

# SHELL = sh.exe / cmd.exe for PowerShell and cmd.exe on Windows, not anywhere else (even Git Bash)
ifeq ($(filter cmd.exe sh.exe,$(SHELL)),$(SHELL))
	START := 
	END := 
	DQ := "
	NULL := NUL
	BLANK := & echo.
	FAIL_CMD := exit /b 1
	CURL ?= curl.exe
else
	START := "
	END := "
	DQ := \"
	NULL := /dev/null
	BLANK := ; echo ""
	FAIL_CMD := exit 1
	CURL ?= curl
endif

# Check last COMMITS commits if COMMITS > 0
COMMITS ?= 0

# If COMMITS > 0, treat it as "check last N commits"
ifneq ($(filter-out 0,$(COMMITS)),)
	DIFF_OPTION := HEAD~$(COMMITS)..
else
	DIFF_OPTION := --cached
endif

# Get changed files from git, then filter to get .py/.pyi via Python (cross-platform)
# Use -z flag (null-terminated) to avoid git quoting filenames
CHANGES_RAW := $(strip $(shell \
	git diff -z --name-only $(DIFF_OPTION) --diff-filter=ACMR 2>$(NULL) | \
	$(PYTHON) -c "import re;print(*(f for f in open(0).read().split('\0')if re.search(r'\.pyi?\Z',f)),sep='\n')" \
))

# Helper functions to check for quotes in paths and escape double quotes within a string
has-dquote = $(findstring ",$(1))
has-squote = $(findstring ',$(1))
escape-dquote = $(subst ",\",$(1))

# Helper function for handles spaces and quotes in filenames
# - If path contains double quote: use double quotes with escaped double quotes inside
# - Otherwise: use double quotes (handles spaces and single quotes safely)
define quote-path
$(if $(call has-dquote,$(1)), \
  "$(call escape-dquote,$(1))" \
, \
  "$(1)" \
)
endef

# Create a properly quoted list of files (handles spaces and quotes in filenames)
CHANGED_FILES := $(foreach file,$(CHANGES_RAW),$(call quote-path,$(file)))

# Sanitize output file for flame graph
FLAME_OUT := $(call quote-path,$(FLAME).html)

# Sanitize output file for speedscope
SPEEDSCOPE_OUT := $(call quote-path,$(SPEEDSCOPE).json)

# Detect uv
UV ?= $(strip $(shell uv --version >$(NULL) 2>&1 && echo yes || echo no))
ifeq ($(UV),yes)
	RUN_CMD ?= uv run
	INSTALL_CMD := uv pip install
else ifeq ($(UV),no)
	RUN_CMD ?= $(PYTHON) -m
	INSTALL_CMD := $(RUN_CMD) pip install
endif

help:
	-@echo $(START)Usage: make [target] [COMMITS=N] [UV=yes|no]$(END)$(BLANK)
	@echo - If COMMITS=N is specified and greater than 0, check Python files changed in last N commits
	@echo $(START)  Otherwise the currently staged changes are checked$(END)
	@echo - If UV is set, it must be either yes or no, otherwise make will detect if uv is available$(BLANK)
	@echo - You can also use this Makefile to check what packages depends on a specific package
	@echo $(START)  Example: make DEP=pydantic-core$(END)
	@echo $(START)  Syntax:  make DEP=<package-name>$(END)$(BLANK)
	@echo - This Makefile can be used to create a flame graph for a Python script
	@echo $(START)  Example: make FLAME=test-openai-emb.py$(END)
	@echo $(START)  Syntax:  make FLAME=<script-name>$(END)$(BLANK)
	@echo - This Makefile can be used to create a speedscope flame graph profile for a Python script
	@echo $(START)  Example: make SPEEDSCOPE=test-openai-emb.py$(END)
	@echo $(START)  Syntax:  make SPEEDSCOPE=<script-name>$(END)
	@echo $(START)  View the profile at https://www.speedscope.app$(END)$(BLANK)
	@echo Available targets:
	@echo $(START)    help       - Show this help message$(END)
	@echo $(START)    install    - Install dependencies via uv or pip: ruff, pylint, mypy, codespell, pipdeptree, pyinstrument$(END)
	@echo $(START)    update     - Download latest version of this Makefile from gitcode.com/openJiuwen/agent-core$(END)
	@echo $(START)    test       - Execute pytest, you can supply arguments via TESTFLAGS=$(DQ)...$(DQ)$(END)
	@echo $(START)    format     - Check formatting of selected Python files via ruff$(END)
	@echo $(START)    lint       - Check linting of selected Python files via ruff$(END)
	@echo $(START)    pylint     - Check linting of selected Python files via pylint: more comprehensive$(END)
	@echo $(START)    spelling   - Check spelling of selected Python files via codespell$(END)
	@echo $(START)    fix-format - Auto-fix formatting errors in selected Python files via ruff$(END)
	@echo $(START)    fix-lint   - Auto-fix linting errors in selected Python files via ruff$(END)
	@echo $(START)    type-check - Type-check selected Python files via mypy$(END)
	@echo $(START)    check      - Run all checks: format, spelling, lint, pylint$(END)
	@echo $(START)    fix        - Run all auto-fixes: fix-lint, fix-format$(END)


dep:
	@echo What packages depend on [$(DEP)]:
	@$(RUN_CMD) pipdeptree --reverse --package $(DEP)

install:
ifeq ($(UV),yes)
	@echo [Makefile] Installing dependencies via uv
	@$(INSTALL_CMD) $(DEPENDENCIES)
else ifeq ($(UV),no)
	@echo [Makefile] Installing dependencies via pip
	@$(INSTALL_CMD) $(DEPENDENCIES)
else
	@echo $(START)[Makefile] Unknown value for UV (yes/no): $(UV)$(END)
	@$(FAIL_CMD)
endif

update:
	@echo Downloading latest version of this Makefile from gitcode.com/openJiuwen/agent-core...
	@echo NOTE: If this did not work, try running
	@echo $(START)  > make update CURL=path/to/your/curl_executable (curl.exe on Windows 10+)$(END)
	@$(CURL) -fsSL https://raw.gitcode.com/openJiuwen/agent-core/raw/develop/Makefile -o Makefile

flame:
	@echo Creating flame graph...
	@$(RUN_CMD) pyinstrument -r html -o $(FLAME_OUT) $(call quote-path,$(FLAME))
	@echo Flame graph created at $(FLAME_OUT)

speedscope:
	@echo Creating speedscope profile...
	@$(RUN_CMD) pyinstrument -r speedscope -o $(SPEEDSCOPE_OUT) $(call quote-path,$(SPEEDSCOPE))
	@echo Speedscope profile created at $(SPEEDSCOPE_OUT)
	@echo $(START)View it at https://www.speedscope.app$(END)

test:
	@echo NOTE: To supply arguments to pytest (for example, to use pytest-xdist), try running
	@echo $(START)  > make test TESTFLAGS=$(DQ)...$(DQ)$(END)
	@$(RUN_CMD) pytest $(TESTFLAGS)

# Sanity check - fails if there are no selected Python files
has-staged-changes:
ifeq ($(strip $(CHANGES_RAW)),)
	@echo No Python files selected.
	@echo NOTE: Make sure you have used git add first, or have set COMMITS to a positive integer. $(BLANK)
	@echo $(LINESEP) $(BLANK)
	@$(MAKE) help
	@$(FAIL_CMD)
endif

format: has-staged-changes
	-@$(RUN_CMD) ruff check --select I $(CHANGED_FILES)
	-@$(RUN_CMD) ruff format --check $(CHANGED_FILES)

lint: has-staged-changes
	-@$(RUN_CMD) ruff check --show-fixes $(CHANGED_FILES)

pylint: has-staged-changes
	-@$(RUN_CMD) pylint $(CHANGED_FILES)

spelling: has-staged-changes
	-@$(RUN_CMD) codespell $(CHANGED_FILES)

fix-format: has-staged-changes
	-@$(RUN_CMD) ruff check --select I --fix $(CHANGED_FILES)
	-@$(RUN_CMD) ruff format $(CHANGED_FILES)

fix-lint: has-staged-changes
	-@$(RUN_CMD) ruff check --fix $(CHANGED_FILES)

type-check: has-staged-changes
	-@$(RUN_CMD) mypy $(CHANGED_FILES)

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
