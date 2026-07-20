cmake_minimum_required(VERSION 3.10)

if(NOT DEFINED VOCOTYPE_VERSION)
    file(STRINGS "${CMAKE_CURRENT_SOURCE_DIR}/../../vocotype_version.py"
         _vocotype_version_line REGEX "^__version__ =")
    string(REGEX MATCH "[0-9]+\\.[0-9]+\\.[0-9]+"
           VOCOTYPE_VERSION "${_vocotype_version_line}")
endif()
if(NOT VOCOTYPE_VERSION)
    message(FATAL_ERROR "Unable to determine VoCoType version")
endif()
project(fcitx5-vocotype-module VERSION ${VOCOTYPE_VERSION})

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
)
target_include_directories(vocotype PRIVATE ../common)
target_link_libraries(vocotype Fcitx5::Core)
if(nlohmann_json_FOUND)
    target_link_libraries(vocotype nlohmann_json::nlohmann_json)
else()
    target_include_directories(vocotype PRIVATE /usr/include)
endif()
set_target_properties(vocotype PROPERTIES PREFIX "")
install(TARGETS vocotype LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}/fcitx5)
