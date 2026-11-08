PYTHON ?= python
.PHONY: bootstrap dev db-up migrate test lint typecheck build smoke contracts security security-audit format integration e2e quality-gate containment restore-test emergency-stop demo-seed demo-run demo-reset demo-fixture-bug-on demo-fixture-bug-off demo-containment
bootstrap dev db-up migrate test lint typecheck build smoke contracts security format integration e2e:
	$(PYTHON) scripts/manage.py $@

demo-seed demo-run demo-reset demo-fixture-bug-on demo-fixture-bug-off demo-containment:
	$(PYTHON) scripts/manage.py $@

quality-gate containment restore-test security-audit emergency-stop:
	$(PYTHON) scripts/manage.py $@
