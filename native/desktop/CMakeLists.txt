cmake_minimum_required(VERSION 3.20)
project(vocotype_desktop LANGUAGES C CXX)
if(NOT DEFINED VOCOTYPE_VERSION)
  file(READ "${CMAKE_CURRENT_SOURCE_DIR}/../../VERSION" VOCOTYPE_VERSION)
  string(STRIP "${VOCOTYPE_VERSION}" VOCOTYPE_VERSION)
endif()

option(VOCOTYPE_BUILD_SETTINGS "Build the GTK settings center" ON)
option(VOCOTYPE_BUILD_IBUS "Build the IBus engine" ON)
option(VOCOTYPE_BUILD_RIME "Enable librime in settings and IBus" ON)

include(CTest)
include(GNUInstallDirs)
find_package(Threads REQUIRED)
find_package(PkgConfig REQUIRED)
find_package(nlohmann_json 3.11 REQUIRED)
find_package(yaml-cpp REQUIRED)
if(TARGET yaml-cpp::yaml-cpp)
  set(VOCOTYPE_YAML_TARGET yaml-cpp::yaml-cpp)
else()
  set(VOCOTYPE_YAML_TARGET yaml-cpp)
endif()
find_package(CURL REQUIRED)
find_package(OpenSSL REQUIRED)
pkg_check_modules(PORTAUDIO REQUIRED IMPORTED_TARGET portaudio-2.0)
if(VOCOTYPE_BUILD_SETTINGS)
  pkg_check_modules(GTK3 REQUIRED IMPORTED_TARGET gtk+-3.0)
endif()
if(VOCOTYPE_BUILD_IBUS)
  pkg_check_modules(IBUS REQUIRED IMPORTED_TARGET ibus-1.0)
endif()
if(VOCOTYPE_BUILD_RIME)
  pkg_check_modules(RIME REQUIRED IMPORTED_TARGET rime)
endif()

add_library(vocotype_desktop_common
    src/audio.cpp
    src/config.cpp
    src/fcitx_profile.cpp
    src/ipc.cpp
    src/recorder_process.cpp
    src/wav.cpp
)
target_include_directories(vocotype_desktop_common PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_compile_features(vocotype_desktop_common PUBLIC cxx_std_20)
target_compile_options(vocotype_desktop_common PRIVATE -Wall -Wextra -Wpedantic -Wconversion -Wshadow)
target_link_libraries(vocotype_desktop_common PUBLIC Threads::Threads nlohmann_json::nlohmann_json PkgConfig::PORTAUDIO ${CMAKE_DL_LIBS})

add_executable(vocotype-audio-recorder src/audio_recorder_main.cpp)
target_link_libraries(vocotype-audio-recorder PRIVATE vocotype_desktop_common)

add_executable(vocotype-model-manager src/model_manager_main.cpp)
target_link_libraries(vocotype-model-manager PRIVATE vocotype_desktop_common CURL::libcurl OpenSSL::Crypto)

if(VOCOTYPE_BUILD_SETTINGS)
  add_executable(vocotype-settings
    src/settings_main.cpp
    src/settings_ui.cpp
  )
  target_compile_definitions(vocotype-settings PRIVATE VOCOTYPE_VERSION="${VOCOTYPE_VERSION}")
  target_link_libraries(vocotype-settings PRIVATE vocotype_desktop_common PkgConfig::GTK3 ${VOCOTYPE_YAML_TARGET} CURL::libcurl)
  if(VOCOTYPE_BUILD_RIME)
    target_sources(vocotype-settings PRIVATE src/rime_session.cpp)
    target_compile_definitions(vocotype-settings PRIVATE VOCOTYPE_HAVE_RIME=1)
    target_link_libraries(vocotype-settings PRIVATE PkgConfig::RIME)
  endif()
endif()

if(VOCOTYPE_BUILD_IBUS)
  add_executable(vocotype-ibus-engine src/ibus_main.cpp src/rime_session.cpp)
  target_link_libraries(vocotype-ibus-engine PRIVATE vocotype_desktop_common PkgConfig::IBUS)
  if(VOCOTYPE_BUILD_RIME)
    target_compile_definitions(vocotype-ibus-engine PRIVATE VOCOTYPE_HAVE_RIME=1)
    target_link_libraries(vocotype-ibus-engine PRIVATE PkgConfig::RIME)
  endif()
endif()

install(TARGETS vocotype-audio-recorder vocotype-model-manager RUNTIME DESTINATION ${CMAKE_INSTALL_LIBEXECDIR})
if(TARGET vocotype-settings)
  install(TARGETS vocotype-settings RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
endif()
if(TARGET vocotype-ibus-engine)
  install(TARGETS vocotype-ibus-engine RUNTIME DESTINATION ${CMAKE_INSTALL_LIBEXECDIR})
endif()

if(BUILD_TESTING)
  add_executable(vocotype-desktop-tests tests/desktop_tests.cpp)
  target_link_libraries(vocotype-desktop-tests PRIVATE vocotype_desktop_common)
  add_test(NAME vocotype-desktop-tests COMMAND vocotype-desktop-tests)
endif()
