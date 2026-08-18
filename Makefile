.PHONY: check demo demo-check hedron-check hedron-build migrate install serve create-admin email-worker pipeline-worker pipeline-janitor schema-status workbench-up workbench-down workbench-test workbench-logs connect-smoke

install:
	python -m pip install -e ".[dev]"

migrate:
	python -m app migrate

schema-status:
	python -m app schema-status

serve:
	python -m app serve --reload

demo:
	bash scripts/run-demo.sh

create-admin:
	@test -n "$(ADMIN_EMAIL)" || (echo "Set ADMIN_EMAIL=…" >&2; exit 1)
	@if [ -n "$(ADMIN_BOOTSTRAP_PASSWORD)" ]; then \
		ADMIN_BOOTSTRAP_PASSWORD="$(ADMIN_BOOTSTRAP_PASSWORD)" python -m app create-admin \
			--email "$(ADMIN_EMAIL)" --password-env ADMIN_BOOTSTRAP_PASSWORD; \
	else \
		python -m app create-admin --email "$(ADMIN_EMAIL)"; \
	fi

pipeline-worker:
	python -m app pipeline-worker

pipeline-janitor:
	python -m app pipeline-janitor

check:
	ruff check app tests demo-app
	ruff format --check app tests demo-app
	basedpyright app
	$(MAKE) hedron-check
	pytest --cov=app --cov-report=term-missing --cov-fail-under=$(COV_FAIL_UNDER)
	$(MAKE) demo-check

demo-check:
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest demo-app/tests

hedron-check:
	python -m hedron --app app.main:app check --severity warning

hedron-build:
	python -m hedron build

# Posit Workbench Docker integration (requires POSIT_WORKBENCH_KEY in .env).
# Prefer `make workbench-down` over docker kill so license-key slots can deactivate.
workbench-up:
	bash docker/workbench-compose.sh up -d --build --wait

workbench-down:
	bash docker/workbench-compose.sh down --remove-orphans

workbench-logs:
	bash docker/workbench-compose.sh logs --tail=200

workbench-test:
	ACCESS_REGISTRY_WORKBENCH_DOCKER=1 \
	ACCESS_REGISTRY_WORKBENCH_RESET=$${ACCESS_REGISTRY_WORKBENCH_RESET:-1} \
	ACCESS_REGISTRY_WORKBENCH_KEEP=$${ACCESS_REGISTRY_WORKBENCH_KEEP:-0} \
	python -m pytest tests/test_workbench_docker.py -m workbench_docker --tb=short

# Isolated Posit Connect 2025.06 deployment smoke test. Reads CONNECT_LICENSE
# from .env and always deactivates it before removing the test container.
connect-smoke:
	bash docker/connect-smoke.sh
# Overridden after baseline measurement; default keeps local/CI honest.
COV_FAIL_UNDER ?= 80

# Default admin email for local demos when callers only set the password env var.
ADMIN_EMAIL ?= admin@example.gov
