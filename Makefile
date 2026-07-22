.PHONY: test cpp-core cpp-core-test release package-deb package-rpm package-arch package-stage clean

PYTHON ?= .venv/bin/python

test:
	$(PYTHON) -m pytest -q

cpp-core:
	cmake -S native/core -B build/native-core -DCMAKE_BUILD_TYPE=RelWithDebInfo
	cmake --build build/native-core -j

cpp-core-test: cpp-core
	ctest --test-dir build/native-core --output-on-failure

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
	find . -path './.git' -prune -o -path './.venv' -prune -o -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f \( -name '*.pyc' -o -name '*.pyo' \) ! -path './.git/*' ! -path './.venv/*' -delete
