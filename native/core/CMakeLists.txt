cmake_minimum_required(VERSION 3.20)
project(vocotype_core LANGUAGES CXX)

include(CTest)
find_package(Threads REQUIRED)
find_package(CURL REQUIRED)
find_package(nlohmann_json 3.11 REQUIRED)

add_library(vocotype_core_lib
    src/config.cpp
    src/dispatcher.cpp
    src/server.cpp
    src/slm_client.cpp
)

target_include_directories(vocotype_core_lib PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_compile_features(vocotype_core_lib PUBLIC cxx_std_20)
target_compile_options(vocotype_core_lib PRIVATE
    -Wall -Wextra -Wpedantic -Wconversion -Wshadow
)
target_link_libraries(vocotype_core_lib
    PUBLIC CURL::libcurl nlohmann_json::nlohmann_json Threads::Threads
)

add_executable(vocotype-core src/main.cpp)
target_link_libraries(vocotype-core PRIVATE vocotype_core_lib)

if(BUILD_TESTING)
    add_executable(vocotype-core-tests tests/core_tests.cpp)
    target_link_libraries(vocotype-core-tests PRIVATE vocotype_core_lib)
    add_test(NAME vocotype-core-tests COMMAND vocotype-core-tests)
endif()
