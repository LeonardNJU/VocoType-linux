cmake_minimum_required(VERSION 3.10)
project(fcitx5-vocotype-module VERSION 2.3.0)

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
    ../addon/ipc_client.cpp
)
target_include_directories(vocotype PRIVATE ../addon)
target_link_libraries(vocotype Fcitx5::Core)
if(nlohmann_json_FOUND)
    target_link_libraries(vocotype nlohmann_json::nlohmann_json)
else()
    target_include_directories(vocotype PRIVATE /usr/include)
endif()
set_target_properties(vocotype PROPERTIES PREFIX "")
install(TARGETS vocotype LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}/fcitx5)
