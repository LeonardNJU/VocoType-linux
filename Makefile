.PHONY: test cpp-core cpp-core-test desktop-test release package-deb package-rpm package-arch package-stage clean

test:
	scripts/test/native.sh

cpp-core:
	cmake -S src/core -B build/native-core -DCMAKE_BUILD_TYPE=RelWithDebInfo -DBUILD_TESTING=ON
	cmake --build build/native-core -j

cpp-core-test: cpp-core
	ctest --test-dir build/native-core --output-on-failure

desktop-test:
	cmake -S src/desktop -B build/native-desktop -DCMAKE_BUILD_TYPE=RelWithDebInfo -DVOCOTYPE_BUILD_IBUS=OFF -DVOCOTYPE_BUILD_RIME=OFF -DBUILD_TESTING=ON
	cmake --build build/native-desktop -j
	ctest --test-dir build/native-desktop --output-on-failure

release:
	packaging/scripts/build-source-release.sh
	packaging/scripts/validate-source-release.sh

package-stage:
	rm -rf build/package-stage
	packaging/scripts/stage-system-package.sh --destdir build/package-stage

package-deb:
	packaging/scripts/build-deb.sh

package-rpm:
	packaging/scripts/build-rpm.sh

package-arch:
	packaging/scripts/build-arch.sh

clean:
	rm -rf build dist
