cmake_minimum_required(VERSION 3.24)
project(vocotype_windows LANGUAGES CXX)
if(NOT WIN32)
  message(FATAL_ERROR "The Windows integration requires Windows")
endif()
include(CTest)
find_package(nlohmann_json 3.10 REQUIRED)
find_package(CURL REQUIRED)
add_compile_definitions(UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX _WIN32_WINNT=0x0A00)
if(MSVC)
  add_compile_options(/W4 /permissive- /EHsc /utf-8 /bigobj)
  add_link_options("/MANIFEST:EMBED" "/MANIFESTINPUT:${CMAKE_CURRENT_SOURCE_DIR}/utf8.manifest")
endif()
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
add_library(vocotype_windows_support win_support.cpp)
target_include_directories(vocotype_windows_support PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(vocotype_windows_support PUBLIC nlohmann_json::nlohmann_json user32 ole32 shell32 advapi32 crypt32)
set(CORE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/../../core)
add_library(vocotype_windows_core
  ../../common/src/terms_yaml.cpp
  ${CORE_DIR}/src/config.cpp
  ${CORE_DIR}/src/dispatcher.cpp
  ${CORE_DIR}/src/offline_asr.cpp
  ${CORE_DIR}/src/slm_client.cpp
  ${CORE_DIR}/src/streaming_asr.cpp
  ${CORE_DIR}/src/text_normalizer.cpp
  ${CORE_DIR}/src/transcription_tasks.cpp
  ${CORE_DIR}/src/voice_edit.cpp
  ${CORE_DIR}/src/voice_edit_tasks.cpp
  json_line_worker_win.cpp)
target_include_directories(vocotype_windows_core PUBLIC ${CORE_DIR}/include ../../common/include)
target_link_libraries(vocotype_windows_core PUBLIC vocotype_windows_support CURL::libcurl)
add_executable(vocotype-core core_main.cpp)
target_link_libraries(vocotype-core PRIVATE vocotype_windows_core)
add_executable(vocotype-windows windows_main.cpp)
target_link_libraries(vocotype-windows PRIVATE vocotype_windows_support winmm)
if(BUILD_TESTING)
  add_test(NAME vocotype-windows-self-test COMMAND vocotype-windows --self-test)
endif()

add_executable(vocotype-wasapi-recorder wasapi_recorder.cpp)
target_link_libraries(vocotype-wasapi-recorder PRIVATE vocotype_windows_support)

add_library(vocotype_windows_pipeline pipeline.cpp)
target_link_libraries(vocotype_windows_pipeline PUBLIC vocotype_windows_support)
