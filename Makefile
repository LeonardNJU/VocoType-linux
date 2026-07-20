.PHONY: test release package-deb package-rpm package-arch package-stage clean

PYTHON ?= .venv/bin/python

test:
	$(PYTHON) -m pytest -q

release:
	$(PYTHON) packaging/tools/build-release.py
	$(PYTHON) packaging/tools/validate-release.py

package-stage:
	rm -rf build/package-stage
	packaging/tools/stage-system-package.sh --destdir build/package-stage

package-deb:
	packaging/tools/build-deb.sh

package-rpm:
	packaging/tools/build-rpm.sh

package-arch:
	packaging/tools/build-arch.sh

clean:
	rm -rf build dist *.egg-info vocotype_linux.egg-info vocotype_ibus.egg-info
