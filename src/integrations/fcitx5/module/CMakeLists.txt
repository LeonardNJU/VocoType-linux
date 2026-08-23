cmake_minimum_required(VERSION 3.10)

if(NOT DEFINED VOCOTYPE_VERSION)
    file(READ "${CMAKE_CURRENT_SOURCE_DIR}/../../../../VERSION" VOCOTYPE_VERSION)
    string(STRIP "${VOCOTYPE_VERSION}" VOCOTYPE_VERSION)
    string(REGEX MATCH "^[0-9]+\.[0-9]+\.[0-9]+" VOCOTYPE_VERSION "${VOCOTYPE_VERSION}")
endif()
if(NOT VOCOTYPE_VERSION)
    message(FATAL_ERROR "Unable to determine VoCoType version")
endif()
project(fcitx5-vocotype-module VERSION ${VOCOTYPE_VERSION})

set(VOCOTYPE_FCITX5_RECORDER_PATH "" CACHE FILEPATH
    "Absolute fallback path to the VoCoType recorder launcher")
set(VOCOTYPE_FCITX5_BACKEND_PATH "" CACHE FILEPATH
    "Absolute fallback path to the VoCoType backend launcher")

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

include(GNUInstallDirs)
find_package(Fcitx5Core REQUIRED)
find_package(nlohmann_json 3.2.0 QUIET)
if(NOT nlohmann_json_FOUND)
    find_package(nlohmann_json QUIET)
endif()

add_library(vocotype MODULE
    vocotype_module.cpp
    ../common/ipc_client.cpp
    ../common/recorder_shutdown.cpp
)
target_include_directories(vocotype PRIVATE ../common)
target_compile_definitions(vocotype PRIVATE
    VOCOTYPE_FCITX5_RECORDER_PATH="${VOCOTYPE_FCITX5_RECORDER_PATH}"
    VOCOTYPE_FCITX5_BACKEND_PATH="${VOCOTYPE_FCITX5_BACKEND_PATH}"
)
target_link_libraries(vocotype Fcitx5::Core)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    set_property(TARGET vocotype APPEND_STRING PROPERTY
                 LINK_FLAGS " -Wl,--no-undefined")
endif()
if(nlohmann_json_FOUND)
    target_link_libraries(vocotype nlohmann_json::nlohmann_json)
else()
    target_include_directories(vocotype PRIVATE /usr/include)
endif()
set_target_properties(vocotype PROPERTIES PREFIX "")

option(VOCOTYPE_BUILD_FCITX_TESTS "Build focused Fcitx integration tests" OFF)
if(VOCOTYPE_BUILD_FCITX_TESTS)
    enable_testing()
    add_executable(vocotype-recorder-shutdown-tests
        tests/recorder_shutdown_tests.cpp
        ../common/recorder_shutdown.cpp
    )
    target_include_directories(vocotype-recorder-shutdown-tests PRIVATE ../common)
    add_test(NAME vocotype-recorder-shutdown
             COMMAND vocotype-recorder-shutdown-tests)
endif()

install(TARGETS vocotype LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}/fcitx5)
