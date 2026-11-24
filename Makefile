# Developer entry points of the Workspace IA kit. `make check` is what must be green before a merge.
# Tools are pinned here (configuration: ruff.toml, pyrightconfig.json); the kit needs only Python 3.14 (.python-version).
PYTHON ?= python3.14
RUFF ?= uvx ruff@0.17.2
PYRIGHT ?= pyright  # version 1.1.402, checked by `make typecheck`

.PHONY: help lint fmt fmt-check typecheck test test-full mirror check clean

help:
	@echo "make check      lint + format check + type check + fast tests (run before any merge)"
	@echo "make lint       ruff: errors, imports, likely bugs, complexity, modern syntax"
	@echo "make fmt        format the code in place (ruff format), then refresh the scripts/ mirror"
	@echo "make fmt-check  fail if a file is not formatted"
	@echo "make typecheck  list pyright errors (engine and tools), then fail if they exceed the budget of tests/test_code_quality.py"
	@echo "make test       fast suite (PYTHON=$(PYTHON))"
	@echo "make test-full  full suite"
	@echo "make mirror     copy template/scripts to scripts/ (required after any change there)"
	@echo "make clean      remove the caches Python, ruff and the tests leave behind (never source files; not while a test suite runs)"

lint:
	$(RUFF) check .

fmt:
	$(RUFF) format .
	$(MAKE) mirror

fmt-check:
	$(RUFF) format --check .

typecheck:
	@$(PYRIGHT) --version | grep -q '1.1.402' || { echo 'pyright 1.1.402 required (pip install pyright==1.1.402)'; exit 1; }
	-$(PYRIGHT)
	$(PYTHON) -m unittest tests.test_code_quality.CodeQualityTests.test_type_errors_stay_within_budget

test:
	PYTHON=$(PYTHON) sh tests/run.sh fast

test-full:
	PYTHON=$(PYTHON) sh tests/run.sh full

mirror:
	rsync -a --exclude __tests__ --exclude __pycache__ template/scripts/ scripts/

check: lint fmt-check typecheck test

# Only regenerated files: __pycache__ folders, the ruff cache, the leftovers of interrupted tests.
clean:
	find . -path ./.git -prune -o -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .ruff_cache .workspace/tmp-tests
