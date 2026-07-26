cmake_minimum_required(VERSION 3.20)
project(vocotype_core LANGUAGES CXX)

include(CTest)
find_package(Threads REQUIRED)
find_package(CURL REQUIRED)
find_package(nlohmann_json 3.10 REQUIRED)

add_library(vocotype_core_lib
    ../common/src/terms_yaml.cpp
    ../common/src/posix.cpp
    src/config.cpp
    src/dispatcher.cpp
    src/json_line_worker.cpp
    src/offline_asr.cpp
    src/server.cpp
    src/slm_client.cpp
    src/streaming_asr.cpp
    src/text_normalizer.cpp
    src/transcription_tasks.cpp
    src/voice_edit.cpp
    src/voice_edit_tasks.cpp
)

target_include_directories(vocotype_core_lib PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../common/include
)
target_compile_features(vocotype_core_lib PUBLIC cxx_std_20)
target_compile_options(vocotype_core_lib PRIVATE
    -Wall -Wextra -Wpedantic -Wconversion -Wshadow
)
if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
    # GCC 16 misdiagnoses nlohmann/json's shared output adapter internals.
    target_compile_options(vocotype_core_lib PRIVATE -Wno-array-bounds)
endif()
target_link_libraries(vocotype_core_lib
    PUBLIC CURL::libcurl nlohmann_json::nlohmann_json Threads::Threads
)

add_executable(vocotype-core src/main.cpp)
target_link_libraries(vocotype-core PRIVATE vocotype_core_lib)

if(BUILD_TESTING)
    add_executable(vocotype-fake-offline-worker tests/fake_offline_worker.cpp)
    target_compile_features(vocotype-fake-offline-worker PRIVATE cxx_std_20)
    target_link_libraries(vocotype-fake-offline-worker
        PRIVATE nlohmann_json::nlohmann_json
    )

    add_executable(vocotype-fake-streaming-worker tests/fake_streaming_worker.cpp)
    target_compile_features(vocotype-fake-streaming-worker PRIVATE cxx_std_20)
    target_link_libraries(vocotype-fake-streaming-worker
        PRIVATE nlohmann_json::nlohmann_json
    )

    add_executable(vocotype-core-tests tests/core_tests.cpp)
    target_link_libraries(vocotype-core-tests PRIVATE vocotype_core_lib)
    add_test(
        NAME vocotype-core-tests
        COMMAND vocotype-core-tests
            $<TARGET_FILE:vocotype-fake-streaming-worker>
            $<TARGET_FILE:vocotype-fake-offline-worker>
    )
endif()
