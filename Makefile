.PHONY: help install dev lint test test-unit test-integration simulate simulate-all train-scorer demo-story web web-dev api experiment experiment-all figures tables paper demo demo-docker clean

PYTHON ?= python
DOCKER ?= docker compose
SEEDS  ?= 5
SCENARIO ?= baseline
SUITE  ?= baselines
SEED   ?= 42

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ─── Setup ────────────────────────────────────────────────────────────

install: ## Install the package in editable mode
	pip install -e ".[dev]"

dev: ## Install with all optional dependencies
	pip install -e ".[dev,redis]"

# ─── Docker ───────────────────────────────────────────────────────────

up: ## Start all services
	$(DOCKER) up -d

down: ## Stop all services
	$(DOCKER) down

reset: ## Stop services and wipe volumes
	$(DOCKER) down -v

logs: ## Tail service logs
	$(DOCKER) logs -f

# ─── Quality ──────────────────────────────────────────────────────────

lint: ## Run ruff linter and formatter check
	$(PYTHON) -m ruff check src/ tests/ scripts/
	$(PYTHON) -m ruff format --check src/ tests/ scripts/

format: ## Auto-format code
	$(PYTHON) -m ruff check --fix src/ tests/
	$(PYTHON) -m ruff format src/ tests/

typecheck: ## Run mypy type checker
	$(PYTHON) -m mypy src/fraudsim/

# ─── Tests ────────────────────────────────────────────────────────────

test: ## Run all tests
	$(PYTHON) -m pytest tests/ -v --tb=short

test-unit: ## Run unit tests only (no Neo4j needed)
	$(PYTHON) -m pytest tests/ -v --tb=short -m "not integration and not slow"

test-integration: ## Run integration tests (needs running Neo4j)
	$(PYTHON) -m pytest tests/ -v --tb=short -m integration

test-smoke: ## Quick smoke test: 60-second headless scenario
	$(PYTHON) -m pytest tests/test_integration.py -v --tb=short -k smoke -m integration

# ─── Seed (legacy) ────────────────────────────────────────────────────

seed: ## Run the original seed.py to load the demo graph
	$(PYTHON) seed.py

# ─── Simulator ────────────────────────────────────────────────────────

simulate: ## Headless simulator run: make simulate SCENARIO=adversarial SEED=7
	$(PYTHON) -m fraudsim.cli simulate --scenario $(SCENARIO) --seed $(SEED) --out runs/

simulate-all: ## Headless simulator run of every scenario
	@for s in baseline high_noise hub_heavy adversarial scale_sweep drift; do \
		$(MAKE) simulate SCENARIO=$$s SEED=$(SEED) ; \
	done

# ─── Scoring and demo ─────────────────────────────────────────────────

train-scorer: ## Train + calibrate the ring risk model (logistic regression)
	$(PYTHON) -m fraudsim.cli train-scorer --model logistic_regression --train-seeds 101-110 --test-seeds 201-205

demo-story: ## One-command demo: hidden ring vs degree cap, writes demo/story.html
	$(PYTHON) -m fraudsim.cli demo-story --seed 7 --out demo/ --open

# ─── Experiments ──────────────────────────────────────────────────────

experiment: ## Run a paper experiment: make experiment SUITE=baselines SEEDS=10 (SUITE=tune first)
	$(PYTHON) -m fraudsim.cli experiment --suite $(SUITE) --seeds $(SEEDS) --output-dir results/

experiment-all: ## Tune on validation seeds, then run the paper experiments with the seed counts used in the paper (~2-3 h)
	$(PYTHON) -m fraudsim.cli experiment --suite tune --output-dir results/
	PYTHONPATH=src $(PYTHON) scripts/run_paper_experiments.py

figures: ## Generate paper figures from experiment results
	$(PYTHON) -m fraudsim.experiments.plotting --results-dir results/ --output-dir docs/paper/figures/
	$(PYTHON) scripts/make_architecture.py

tables: ## Generate LaTeX tables and number macros from experiment results
	$(PYTHON) -m fraudsim.experiments.latex_export --results-dir results/ --output-dir docs/paper/tables/

paper: figures tables ## Regenerate figures/tables, then build docs/paper/paper.pdf (needs pdflatex)
	cd docs/paper && pdflatex -interaction=nonstopmode paper.tex && bibtex paper && pdflatex -interaction=nonstopmode paper.tex && pdflatex -interaction=nonstopmode paper.tex

# ─── Demo ─────────────────────────────────────────────────────────────

web: ## Build the React dashboard into web/dist (served by the API at /ui)
	cd web && npm ci && npm run build

web-dev: ## Dashboard dev server with hot reload on :5173 (run `make api` too)
	cd web && npm run dev

api: ## Run the FastAPI backend (dashboard at http://localhost:8000/ui/)
	$(PYTHON) -m fraudsim.cli api --port 8000

demo-docker: ## Everything in containers: Neo4j, Redis and the API + dashboard on http://localhost:8000/ui/
	docker compose --profile app up --build

demo: web ## Build the dashboard and serve it with the API (open http://localhost:8000/ui/)
	@echo ""
	@echo "Dashboard:  http://localhost:8000/ui/"
	@echo "API docs:   http://localhost:8000/docs"
	@echo "Neo4j:      http://localhost:7474 (only needed for --backend neo4j runs)"
	@echo ""
	$(PYTHON) -m fraudsim.cli api --port 8000

# ─── Cleanup ──────────────────────────────────────────────────────────

clean: ## Remove build artifacts and caches
	rm -rf build/ dist/ *.egg-info .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf results/ runs/
