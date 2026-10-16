# Prefer the repository virtualenv when one exists. This prevents a globally
# installed Hedron version from being used accidentally for local checks.
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python)
POSIT_CLI ?= $(if $(wildcard $(dir $(PYTHON))hedron-posit),$(dir $(PYTHON))hedron-posit,hedron-posit)

.PHONY: check format format-check lint typecheck test test-file docs-check demo demo-check databricks-tools databricks-check hedron-check hedron-build hedron-security-check posit-check migrate install serve create-admin schema-status workbench-up workbench-down workbench-test workbench-logs connect-smoke

install:
	$(PYTHON) -m pip install -e ".[dev]"

migrate:
	$(PYTHON) -m app migrate

schema-status:
	$(PYTHON) -m app schema-status

serve:
	$(PYTHON) -m app serve --reload

# Rebuild Hedron's fingerprinted assets before the demo starts.  The generated
# manifest carries the selected Folio theme tokens; reusing an older manifest
# can otherwise override the native Folio defaults with stale palette values.
demo: hedron-build
	bash scripts/run-demo.sh

create-admin:
	@test -n "$(ADMIN_EMAIL)" || (echo "Set ADMIN_EMAIL=…" >&2; exit 1)
	@if [ -n "$(ADMIN_BOOTSTRAP_PASSWORD)" ]; then \
		ADMIN_BOOTSTRAP_PASSWORD="$(ADMIN_BOOTSTRAP_PASSWORD)" $(PYTHON) -m app create-admin \
			--email "$(ADMIN_EMAIL)" --password-env ADMIN_BOOTSTRAP_PASSWORD; \
	else \
		$(PYTHON) -m app create-admin --email "$(ADMIN_EMAIL)"; \
	fi

format:
	$(PYTHON) -m ruff format app tests demo-app scripts

format-check:
	$(PYTHON) -m ruff format --check app tests demo-app scripts

lint:
	$(PYTHON) -m ruff check app tests demo-app scripts

typecheck:
	$(PYTHON) -m basedpyright

test: databricks-tools
	DATABRICKS_TEST_TOOLS_REQUIRED=1 $(PYTHON) -m pytest -n auto --cov=app --cov-report=term-missing --cov-fail-under=$(COV_FAIL_UNDER)

test-file:
	@test -n "$(FILE)" || (echo "Usage: make test-file FILE=tests/test_example.py" >&2; exit 1)
	$(PYTHON) -m pytest "$(FILE)"

docs-check:
	$(PYTHON) scripts/check_docs.py

check: lint format-check typecheck docs-check
	$(MAKE) hedron-check
	$(MAKE) posit-check
	$(MAKE) test
	$(MAKE) demo-check

demo-check:
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 $(PYTHON) -m pytest demo-app/tests

databricks-tools:
	$(PYTHON) scripts/install-databricks-test-tools.py

databricks-check: databricks-tools
	DATABRICKS_TEST_TOOLS_REQUIRED=1 $(PYTHON) -m pytest -n auto tests/test_databricks_*.py

hedron-check:
	$(PYTHON) -m hedron --app app.main:app check --severity warning

hedron-security-check:
	mkdir -p .hedron
	$(PYTHON) -m hedron security-check --policy strict --format sarif > .hedron/security-report.sarif

posit-check:
	$(POSIT_CLI) check --matrix

hedron-build:
	$(PYTHON) -m hedron build

# Posit Workbench Docker integration (requires PWB_LICENSE in .env).
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
	$(PYTHON) -m pytest tests/test_workbench_docker.py -m workbench_docker --tb=short

# Isolated Posit Connect 2025.06 deployment smoke test. Reads CONNECT_LICENSE
# from .env and always deactivates it before removing the test container.
connect-smoke:
	bash docker/connect-smoke.sh
# Overridden after baseline measurement; default keeps local/CI honest.
COV_FAIL_UNDER ?= 80

# Default admin email for local demos when callers only set the password env var.
ADMIN_EMAIL ?= admin@example.gov
