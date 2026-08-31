# MergeCut — Makefile (root)
#
# Convenience targets for Phase 0 + Phase 1 work. Phases 2+ are not
# implemented yet; their targets will be added when their acceptance
# criteria come online (PROJECT_PLAN §29).
#
# Run `make help` to list targets.

SHELL := /bin/bash
.DEFAULT_GOAL := help

PYTHON  ?= python3
UV      ?= uv
NODE    ?= node
NPM     ?= npm
BACKEND := backend
FRONTEND := frontend

# Pin a project-local Python via uv so we don't pollute system Python
# and we don't depend on whatever the system Python3 is (Hermes host has
# 3.9.6; we want 3.12 per PROJECT_PLAN §10).
UV_PYTHON ?= 3.12

# ---- Help ------------------------------------------------------------------

.PHONY: help
help: ## List available targets
	@awk 'BEGIN {FS = ":.*##"; printf "Targets:\n"} \
	/^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

# ---- Backend setup -----------------------------------------------------------

.PHONY: backend-install
backend-install: ## Create uv venv for backend with dev deps
	cd $(BACKEND) && $(UV) python install $(UV_PYTHON) 2>/dev/null || true
	cd $(BACKEND) && $(UV) venv --python $(UV_PYTHON) 2>/dev/null || true
	cd $(BACKEND) && $(UV) sync --extra dev

.PHONY: backend-shell
backend-shell: backend-install ## Drop into the backend venv (activates)
	cd $(BACKEND) && $(UV) run python

# ---- Tests / lint ----------------------------------------------------------

.PHONY: test
test: backend-install ## Run backend unit tests
	cd $(BACKEND) && $(UV) run pytest -q

.PHONY: lint
lint: backend-install ## Run ruff + mypy on backend
	cd $(BACKEND) && $(UV) run ruff check .
	cd $(BACKEND) && $(UV) run ruff format --check .
	cd $(BACKEND) && $(UV) run mypy app

.PHONY: format
format: backend-install ## Auto-format backend with ruff
	cd $(BACKEND) && $(UV) run ruff check --fix .
	cd $(BACKEND) && $(UV) run ruff format .

# ---- FFmpeg / smoke -------------------------------------------------------

.PHONY: check-ffmpeg
check-ffmpeg: ## Verify ffmpeg/ffprobe are present
	$(PYTHON) scripts/check_ffmpeg.py

.PHONY: smoke
smoke: check-ffmpeg ## Run smoke checks (ffmpeg + env + minimal M3 call)
	@test -f .env || cp .env.example .env
	cd $(BACKEND) && $(UV) run python -c "from app.main import app; print('fastapi import OK')"

# Phase 2 — media pipeline end-to-end smoke against the controlled
# fixture set. Builds 5 small MP4s (3-shot normal, 6-shot multi,
# 3-shot speech, 3-shot no-audio, 1 zero-byte bad input) in tmpdir
# and runs the full pipeline. Skips the ASR-backed assertions if
# the whisper model isn't available locally.
.PHONY: media-smoke
media-smoke: backend-install ## Build Phase 2 fixtures + run pipeline
	cd $(BACKEND) && $(UV) run pytest -q tests/unit/test_media_pipeline.py

.PHONY: real-speech
real-speech: backend-install ## Run the pipeline against a real video (positional arg: path)
	cd $(BACKEND) && $(UV) run python scripts/test_real_speech.py $(REAL_SPEECH_VIDEO)

REAL_SPEECH_VIDEO ?= path/to/video.mp4

# ---- Phase 1 capability spike --------------------------------------------

.PHONY: spike
spike: backend-install ## Run the 5-fixture semantic-merge spike (live M3 calls)
	@test -f .env || cp .env.example .env
	@echo "NOTE: spike requires GMI_API_KEY in .env"
	$(UV) run --project $(BACKEND) python scripts/run_spike.py

.PHONY: spike-dry
spike-dry: backend-install ## Validate fixtures + schema without calling the model
	cd $(BACKEND) && $(UV) run pytest -q tests/unit/test_spike_fixtures.py tests/unit/test_semantic_schema.py

# ---- Frontend --------------------------------------------------------------

.PHONY: frontend-install
frontend-install: ## Install frontend deps (npm)
	cd $(FRONTEND) && $(NPM) install

.PHONY: frontend-lint
frontend-lint: frontend-install ## Lint frontend
	cd $(FRONTEND) && $(NPM) run lint

.PHONY: frontend-typecheck
frontend-typecheck: frontend-install ## Typecheck frontend
	cd $(FRONTEND) && $(NPM) run typecheck

# ---- Aggregate ------------------------------------------------------------

.PHONY: verify
verify: test lint check-ffmpeg ## All offline acceptance checks for Phase 0
	@echo ""
	@echo "Phase 0 acceptance — make test + make lint both green."