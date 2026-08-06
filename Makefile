.PHONY: check migrate install serve create-admin email-worker schema-status

install:
	python -m pip install -e ".[dev]"

migrate:
	python -m app migrate

schema-status:
	python -m app schema-status

serve:
	python -m app serve --reload

create-admin:
	@test -n "$(ADMIN_EMAIL)" || (echo "Set ADMIN_EMAIL=…" >&2; exit 1)
	@if [ -n "$(ADMIN_BOOTSTRAP_PASSWORD)" ]; then \
		ADMIN_BOOTSTRAP_PASSWORD="$(ADMIN_BOOTSTRAP_PASSWORD)" python -m app create-admin \
			--email "$(ADMIN_EMAIL)" --password-env ADMIN_BOOTSTRAP_PASSWORD; \
	else \
		python -m app create-admin --email "$(ADMIN_EMAIL)"; \
	fi

email-worker:
	python -m app email-worker

check:
	ruff check app tests
	ruff format --check app tests
	basedpyright app
	pytest --cov=app --cov-report=term-missing --cov-fail-under=$(COV_FAIL_UNDER)

# Overridden after baseline measurement; default keeps local/CI honest.
COV_FAIL_UNDER ?= 80

# Default admin email for local demos when callers only set the password env var.
ADMIN_EMAIL ?= admin@example.gov
