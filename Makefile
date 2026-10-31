# The checks CI runs, one target each (docs/ROADMAP.md, Stage 1, item 6). They work in the pinned
# container (Dockerfile, .devcontainer/) and in a local checkout with the pinned dependencies
# installed: pip install -r requirements.txt -r requirements-dev.txt and npm ci --ignore-scripts.

PYTHON ?= python
REBUILT ?= /tmp/vol_lab.rebuilt.json

# The Reproduce workflow's numeric environment (.github/workflows/reproduce.yml): one thread
# everywhere and the same BLAS and NumPy code paths on every x86-64 host.
REPRODUCE_ENV = OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_CORETYPE=Haswell \
	NPY_DISABLE_CPU_FEATURES="X86_V4 AVX512_ICL AVX512_SPR" PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1 \
	PYTHONPATH=.

.PHONY: help lint type test reproduce check-frontend figures docs

help:
	@echo "make lint            ruff (both configurations) and eslint"
	@echo "make type            mypy strict on the typed modules (pyproject.toml)"
	@echo "make test            the Python and frontend unit suites"
	@echo "make reproduce       rebuild data/vol_lab.json from the pinned snapshot and compare (about 4 minutes)"
	@echo "make check-frontend  npm run check: eslint, tests, build and both audits"
	@echo "make figures         regenerate docs/figures and show any drift from the committed ones"
	@echo "make docs            the generated numbers in the README and docs match the artifacts (cog)"

lint:
	ruff check backend/ scripts/ tests/ freeze.py ./*.py --select F,E9
	ruff check
	npm run lint

type:
	mypy

test:
	PYTHONPATH=. $(PYTHON) -B -m unittest discover -s tests
	npm test

reproduce:
	$(REPRODUCE_ENV) $(PYTHON) -B -c "from backend.market_data import load_snapshot; load_snapshot()"
	$(REPRODUCE_ENV) RUN_SLOW=1 VOL_LAB_REBUILT=$(REBUILT) $(PYTHON) -B -m unittest -v \
		tests.test_vol_lab.ArtifactContractTest.test_artifact_reproduces_from_the_snapshot
	$(REPRODUCE_ENV) $(PYTHON) -B scripts/describe_rebuild.py $(REBUILT)

check-frontend:
	npm run check

figures:
	PYTHONPATH=. $(PYTHON) -B scripts/make_figures.py
	@if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then \
		git diff --exit-code --stat -- docs/figures && echo "docs/figures: no drift"; \
	else echo "not a git checkout: compare docs/figures by hand"; fi

docs:
	PYTHONPATH=. $(PYTHON) -m cogapp --check README.md docs/RESULTS.md docs/MODEL_CARD.md docs/DATASHEET.md \
		docs/PREREGISTRATION.md
