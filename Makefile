.PHONY: test release package-deb package-rpm package-arch package-stage clean

PYTHON ?= .venv/bin/python

test:
	$(PYTHON) -m pytest -q

release:
	$(PYTHON) scripts/build-release.py
	$(PYTHON) scripts/validate-release.py

package-stage:
	rm -rf build/package-stage
	packaging/stage-system-package.sh --destdir build/package-stage

package-deb:
	scripts/build-deb.sh

package-rpm:
	scripts/build-rpm.sh

package-arch:
	scripts/build-arch.sh

clean:
	rm -rf build dist *.egg-info vocotype_linux.egg-info vocotype_ibus.egg-info
