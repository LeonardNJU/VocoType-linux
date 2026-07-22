.PHONY: test cpp-core cpp-core-test desktop-test release package-deb package-rpm package-arch package-stage clean

test:
	tools/test-native.sh

cpp-core:
	cmake -S native/core -B build/native-core -DCMAKE_BUILD_TYPE=RelWithDebInfo -DBUILD_TESTING=ON
	cmake --build build/native-core -j

cpp-core-test: cpp-core
	ctest --test-dir build/native-core --output-on-failure

desktop-test:
	cmake -S native/desktop -B build/native-desktop -DCMAKE_BUILD_TYPE=RelWithDebInfo -DVOCOTYPE_BUILD_IBUS=OFF -DVOCOTYPE_BUILD_RIME=OFF -DBUILD_TESTING=ON
	cmake --build build/native-desktop -j
	ctest --test-dir build/native-desktop --output-on-failure

release:
	packaging/tools/build-source-release.sh
	packaging/tools/validate-source-release.sh

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
	rm -rf build dist
