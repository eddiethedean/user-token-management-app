.PHONY: check migrate

check:
	ruff check app tests
	ruff format --check app tests
	basedpyright app
	pytest --cov=app --cov-report=term-missing --cov-fail-under=$(COV_FAIL_UNDER)

migrate:
	python -m app migrate

# Overridden after baseline measurement; default keeps local/CI honest.
COV_FAIL_UNDER ?= 80
