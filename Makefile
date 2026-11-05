PYTHON ?= python
.PHONY: bootstrap dev db-up migrate test lint typecheck build smoke contracts security format
bootstrap dev db-up migrate test lint typecheck build smoke contracts security format:
	$(PYTHON) scripts/manage.py $@
