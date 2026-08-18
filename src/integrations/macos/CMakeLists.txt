cmake_minimum_required(VERSION 3.20)
project(vocotype_macos LANGUAGES C CXX OBJCXX)

include(CTest)

if(NOT APPLE)
  message(FATAL_ERROR "The macOS input method can only be built on Apple platforms")
endif()

# Keep development .app bundles out of Spotlight/LaunchServices search results.
# Every direct macOS CMake build gets its own exclusion marker before bundle
# targets are generated, regardless of the caller-selected build directory.
file(TOUCH "${CMAKE_BINARY_DIR}/.metadata_never_index")
if(NOT DEFINED VOCOTYPE_VERSION)
  file(READ "${CMAKE_CURRENT_SOURCE_DIR}/../../../VERSION" VOCOTYPE_VERSION)
  string(STRIP "${VOCOTYPE_VERSION}" VOCOTYPE_VERSION)
endif()
if(NOT VOCOTYPE_VERSION MATCHES
   "^([0-9]+)\\.([0-9]+)\\.([0-9]+)([A-Za-z]+([0-9]+))?$")
  message(FATAL_ERROR "Unsupported VoCoType-linux version: ${VOCOTYPE_VERSION}")
endif()
set(VOCOTYPE_VERSION_MAJOR "${CMAKE_MATCH_1}")
set(VOCOTYPE_VERSION_MINOR "${CMAKE_MATCH_2}")
set(VOCOTYPE_VERSION_PATCH "${CMAKE_MATCH_3}")
set(VOCOTYPE_PRERELEASE_NUMBER "${CMAKE_MATCH_5}")
set(VOCOTYPE_MARKETING_VERSION
    "${VOCOTYPE_VERSION_MAJOR}.${VOCOTYPE_VERSION_MINOR}.${VOCOTYPE_VERSION_PATCH}")

# CFBundleVersion supports at most three numeric components. Encode a
# prerelease number into the patch component instead of emitting a fourth
# component that LaunchServices silently ignores. Final releases sort after
# their prereleases by using suffix 99.
if(VOCOTYPE_PRERELEASE_NUMBER STREQUAL "")
  set(VOCOTYPE_BUILD_SUFFIX 99)
else()
  set(VOCOTYPE_BUILD_SUFFIX "${VOCOTYPE_PRERELEASE_NUMBER}")
endif()
math(EXPR VOCOTYPE_BUILD_PATCH
     "${VOCOTYPE_VERSION_PATCH} * 100 + ${VOCOTYPE_BUILD_SUFFIX}")
set(VOCOTYPE_BUNDLE_VERSION
    "${VOCOTYPE_VERSION_MAJOR}.${VOCOTYPE_VERSION_MINOR}.${VOCOTYPE_BUILD_PATCH}")

set(CMAKE_OSX_DEPLOYMENT_TARGET "13.0" CACHE STRING "Minimum macOS version")
find_package(Threads REQUIRED)
find_package(nlohmann_json 3.10 REQUIRED)
find_package(CURL REQUIRED)
find_package(OpenSSL REQUIRED)
find_package(PkgConfig REQUIRED)
pkg_check_modules(PORTAUDIO REQUIRED IMPORTED_TARGET portaudio-2.0)
find_library(APPKIT_FRAMEWORK AppKit REQUIRED)
find_library(AVFAUDIO_FRAMEWORK AVFAudio REQUIRED)
find_library(INPUTMETHODKIT_FRAMEWORK InputMethodKit REQUIRED)
find_library(CARBON_FRAMEWORK Carbon REQUIRED)
find_library(COREGRAPHICS_FRAMEWORK CoreGraphics REQUIRED)
find_library(COREFOUNDATION_FRAMEWORK CoreFoundation REQUIRED)
find_library(UNIFORMTYPEIDENTIFIERS_FRAMEWORK UniformTypeIdentifiers REQUIRED)

add_library(vocotype_macos_base STATIC
  ../../common/src/posix.cpp
  ../../desktop/src/config.cpp
  ../../desktop/src/ipc.cpp
  ../../desktop/src/recorder_process.cpp
  ../../desktop/src/streaming_preview.cpp
)
target_include_directories(vocotype_macos_base PUBLIC
  ../../common/include
  ../../desktop/include
)
target_compile_features(vocotype_macos_base PUBLIC cxx_std_20)
target_compile_options(vocotype_macos_base PRIVATE
  -Wall -Wextra -Wpedantic -Wconversion -Wshadow
)
target_link_libraries(vocotype_macos_base PUBLIC
  Threads::Threads
  nlohmann_json::nlohmann_json
  ${CMAKE_DL_LIBS}
)

add_executable(vocotype-audio-recorder
  ../../desktop/src/audio_recorder_main.cpp
  ../../desktop/src/audio.cpp
  ../../desktop/src/wav.cpp
)
target_include_directories(vocotype-audio-recorder PRIVATE ../../desktop/include)
target_compile_features(vocotype-audio-recorder PRIVATE cxx_std_20)
target_link_libraries(vocotype-audio-recorder PRIVATE
  vocotype_macos_base PkgConfig::PORTAUDIO
)

add_executable(vocotype-model-manager ../../desktop/src/model_manager_main.cpp)
target_include_directories(vocotype-model-manager PRIVATE ../../desktop/include)
target_compile_features(vocotype-model-manager PRIVATE cxx_std_20)
target_link_libraries(vocotype-model-manager PRIVATE
  vocotype_macos_base CURL::libcurl OpenSSL::Crypto
)

configure_file(Info.plist.in ${CMAKE_CURRENT_BINARY_DIR}/Info.plist @ONLY)
configure_file(SettingsInfo.plist.in ${CMAKE_CURRENT_BINARY_DIR}/SettingsInfo.plist @ONLY)

set(VOCOTYPE_MACOS_ICON "${CMAKE_CURRENT_BINARY_DIR}/VoCoType-linux.icns")
add_custom_command(
  OUTPUT "${VOCOTYPE_MACOS_ICON}"
  COMMAND "${CMAKE_CURRENT_SOURCE_DIR}/../../../packaging/macos/make-icns.sh"
          "${CMAKE_CURRENT_SOURCE_DIR}/../../../web/icon-512.png"
          "${VOCOTYPE_MACOS_ICON}"
  DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../web/icon-512.png"
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../packaging/macos/make-icns.sh"
  VERBATIM
)
add_custom_target(vocotype-macos-icon DEPENDS "${VOCOTYPE_MACOS_ICON}")
add_executable(VocoType MACOSX_BUNDLE
  main.mm
  VocoTypeRuntime.mm
  VocoTypeRuntime.h
  VocoTypeInputController.mm
  VocoTypeInputController.h
)
target_include_directories(VocoType PRIVATE ../../common/include ../../desktop/include)
target_compile_features(VocoType PRIVATE cxx_std_20)
target_compile_options(VocoType PRIVATE -fobjc-arc -Wall -Wextra -Wpedantic)
target_link_libraries(VocoType PRIVATE
  vocotype_macos_base
  ${APPKIT_FRAMEWORK}
  ${INPUTMETHODKIT_FRAMEWORK}
  ${CARBON_FRAMEWORK}
  ${COREGRAPHICS_FRAMEWORK}
)

add_library(vocotype_macos_settings_backend STATIC
  ../../desktop/src/settings_backend.cpp
  ../../desktop/src/audio.cpp
  ../../desktop/src/wav.cpp
  ../../common/src/terms_yaml.cpp
)
target_include_directories(vocotype_macos_settings_backend PUBLIC
  ../../common/include ../../desktop/include
)
target_compile_features(vocotype_macos_settings_backend PUBLIC cxx_std_20)
target_compile_definitions(vocotype_macos_settings_backend PRIVATE
  VOCOTYPE_VERSION="${VOCOTYPE_VERSION}"
)
target_compile_options(vocotype_macos_settings_backend PRIVATE
  -Wall -Wextra -Wpedantic -Wconversion -Wshadow
)
target_link_libraries(vocotype_macos_settings_backend PUBLIC
  vocotype_macos_base PkgConfig::PORTAUDIO CURL::libcurl
)


if(BUILD_TESTING)
  add_executable(vocotype-macos-settings-backend-tests
    ../../desktop/tests/settings_backend_tests.cpp
  )
  target_link_libraries(vocotype-macos-settings-backend-tests PRIVATE
    vocotype_macos_settings_backend
  )
  add_test(NAME vocotype-macos-settings-backend-tests
           COMMAND vocotype-macos-settings-backend-tests)
  add_executable(vocotype-macos-streaming-preview-tests
    ../../desktop/tests/streaming_preview_tests.cpp
  )
  target_link_libraries(vocotype-macos-streaming-preview-tests PRIVATE
    vocotype_macos_base
  )
  add_test(NAME vocotype-macos-streaming-preview-tests
           COMMAND vocotype-macos-streaming-preview-tests)
  add_executable(vocotype-macos-ipc-quick-response-tests
    ../../desktop/tests/ipc_quick_response_tests.cpp
  )
  target_link_libraries(vocotype-macos-ipc-quick-response-tests PRIVATE
    vocotype_macos_base
  )
  add_test(NAME vocotype-macos-ipc-quick-response-tests
           COMMAND vocotype-macos-ipc-quick-response-tests)

  add_executable(vocotype-macos-fake-slow-preview-recorder
    ../../desktop/tests/fake_slow_preview_recorder.cpp
  )
  add_executable(vocotype-macos-recorder-fast-stop-tests
    ../../desktop/tests/recorder_process_fast_stop_tests.cpp
  )
  target_link_libraries(vocotype-macos-recorder-fast-stop-tests PRIVATE
    vocotype_macos_base
  )
  add_test(NAME vocotype-macos-recorder-fast-stop
           COMMAND vocotype-macos-recorder-fast-stop-tests
                   $<TARGET_FILE:vocotype-macos-fake-slow-preview-recorder>)
  add_test(NAME vocotype-macos-pane-long-text
           COMMAND $<TARGET_FILE:VocoType> --pane-smoke
                   ${CMAKE_CURRENT_BINARY_DIR}/pane-long-text-smoke.json)
  add_test(NAME vocotype-macos-short-recording-result
           COMMAND $<TARGET_FILE:VocoType> --short-result-smoke
                   ${CMAKE_CURRENT_BINARY_DIR}/short-recording-result-smoke.json)
  add_test(NAME vocotype-macos-voice-lifecycle
           COMMAND $<TARGET_FILE:VocoType> --voice-lifecycle-smoke
                   ${CMAKE_CURRENT_BINARY_DIR}/voice-lifecycle-smoke.json)
  add_test(NAME vocotype-macos-settings-menu
           COMMAND ${CMAKE_COMMAND} -E env
                   XDG_CONFIG_HOME=${CMAKE_CURRENT_BINARY_DIR}/menu-smoke-config
                   VOCOTYPE_SOCKET=${CMAKE_CURRENT_BINARY_DIR}/menu-smoke.sock
                   VOCOTYPE_CACHE_DIR=${CMAKE_CURRENT_BINARY_DIR}/menu-smoke-cache
                   $<TARGET_FILE:VocoTypeSettings> --menu-smoke
                   ${CMAKE_CURRENT_BINARY_DIR}/menu-smoke.json)
  add_test(NAME vocotype-macos-settings-tab-switch
           COMMAND ${CMAKE_COMMAND} -E env
                   XDG_CONFIG_HOME=${CMAKE_CURRENT_BINARY_DIR}/tab-switch-smoke-config
                   VOCOTYPE_SOCKET=${CMAKE_CURRENT_BINARY_DIR}/tab-switch-smoke.sock
                   VOCOTYPE_CACHE_DIR=${CMAKE_CURRENT_BINARY_DIR}/tab-switch-smoke-cache
                   $<TARGET_FILE:VocoTypeSettings> --tab-switch-smoke
                   ${CMAKE_CURRENT_BINARY_DIR}/tab-switch-smoke.json)
  add_test(NAME vocotype-macos-terms-page
           COMMAND ${CMAKE_COMMAND} -E env
                   XDG_CONFIG_HOME=${CMAKE_CURRENT_BINARY_DIR}/terms-page-smoke-config
                   VOCOTYPE_SOCKET=${CMAKE_CURRENT_BINARY_DIR}/terms-page-smoke.sock
                   VOCOTYPE_CACHE_DIR=${CMAKE_CURRENT_BINARY_DIR}/terms-page-smoke-cache
                   $<TARGET_FILE:VocoTypeSettings> --terms-page-smoke
                   ${CMAKE_CURRENT_BINARY_DIR}/terms-page-smoke.json)
endif()

add_executable(VocoTypeSettings MACOSX_BUNDLE
  settings_main.mm
  VocoTypeRuntime.mm
  VocoTypeRuntime.h
  VocoTypeApplicationController.mm
  VocoTypeApplicationController.h
)
target_include_directories(VocoTypeSettings PRIVATE ../../common/include ../../desktop/include)
target_compile_features(VocoTypeSettings PRIVATE cxx_std_20)
target_compile_options(VocoTypeSettings PRIVATE -fobjc-arc -Wall -Wextra -Wpedantic)
target_link_libraries(VocoTypeSettings PRIVATE
  vocotype_macos_settings_backend
  ${APPKIT_FRAMEWORK}
  ${AVFAUDIO_FRAMEWORK}
  ${COREFOUNDATION_FRAMEWORK}
  ${UNIFORMTYPEIDENTIFIERS_FRAMEWORK}
)

add_executable(vocotype-input-source-tool input_source_tool.mm)
target_compile_features(vocotype-input-source-tool PRIVATE cxx_std_20)
target_compile_options(vocotype-input-source-tool PRIVATE -fobjc-arc -Wall -Wextra -Wpedantic)
target_link_libraries(vocotype-input-source-tool PRIVATE
  nlohmann_json::nlohmann_json
  ${CARBON_FRAMEWORK}
  ${COREFOUNDATION_FRAMEWORK}
)

foreach(bundle_target IN ITEMS VocoType VocoTypeSettings)
  add_dependencies(${bundle_target} vocotype-macos-icon)
  add_custom_command(TARGET ${bundle_target} POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E make_directory
            "$<TARGET_BUNDLE_CONTENT_DIR:${bundle_target}>/Resources"
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "${VOCOTYPE_MACOS_ICON}"
            "$<TARGET_BUNDLE_CONTENT_DIR:${bundle_target}>/Resources/VoCoType-linux.icns"
    VERBATIM
  )
endforeach()

set_target_properties(VocoType PROPERTIES
  OUTPUT_NAME VoCoTypeLinuxInputMethod
  MACOSX_BUNDLE_BUNDLE_NAME VoCoType-linux
  MACOSX_BUNDLE_GUI_IDENTIFIER io.github.LeonardNJU.VoCoTypeLinux.InputMethod
  MACOSX_BUNDLE_INFO_PLIST ${CMAKE_CURRENT_BINARY_DIR}/Info.plist
  XCODE_ATTRIBUTE_CODE_SIGNING_ALLOWED NO
)

set_target_properties(VocoTypeSettings PROPERTIES
  OUTPUT_NAME VoCoTypeLinuxSettings
  MACOSX_BUNDLE_BUNDLE_NAME "VoCoType-linux"
  MACOSX_BUNDLE_GUI_IDENTIFIER io.github.LeonardNJU.VoCoTypeLinux.Settings
  MACOSX_BUNDLE_INFO_PLIST ${CMAKE_CURRENT_BINARY_DIR}/SettingsInfo.plist
  XCODE_ATTRIBUTE_CODE_SIGNING_ALLOWED NO
)
