cmake_minimum_required(VERSION 3.20)
project(vocotype_feedback LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

include(GNUInstallDirs)
include(CTest)
find_package(OpenSSL REQUIRED)
find_package(SQLite3 REQUIRED)
find_package(Threads REQUIRED)
find_package(nlohmann_json 3.11 REQUIRED)

if(TARGET SQLite3::SQLite3)
  set(VOCOTYPE_SQLITE_TARGET SQLite3::SQLite3)
elseif(TARGET SQLite::SQLite3)
  set(VOCOTYPE_SQLITE_TARGET SQLite::SQLite3)
else()
  add_library(vocotype_sqlite3 INTERFACE)
  target_include_directories(vocotype_sqlite3 INTERFACE ${SQLite3_INCLUDE_DIRS})
  target_link_libraries(vocotype_sqlite3 INTERFACE ${SQLite3_LIBRARIES})
  set(VOCOTYPE_SQLITE_TARGET vocotype_sqlite3)
endif()

add_library(vocotype_feedback_lib
  src/service.cpp
)
target_include_directories(vocotype_feedback_lib PUBLIC include)
target_link_libraries(vocotype_feedback_lib
  PUBLIC
    OpenSSL::Crypto
    ${VOCOTYPE_SQLITE_TARGET}
    Threads::Threads
    nlohmann_json::nlohmann_json
)
target_compile_options(vocotype_feedback_lib PRIVATE -Wall -Wextra -Wpedantic)

add_executable(vocotype-feedback src/main.cpp)
target_compile_definitions(vocotype-feedback PRIVATE
  BOOST_ERROR_CODE_HEADER_ONLY
  BOOST_SYSTEM_NO_DEPRECATED
)
target_link_libraries(vocotype-feedback PRIVATE vocotype_feedback_lib Threads::Threads)
target_compile_options(vocotype-feedback PRIVATE -Wall -Wextra -Wpedantic)

install(TARGETS vocotype-feedback RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})

if(BUILD_TESTING)
  add_executable(vocotype-feedback-tests tests/feedback_tests.cpp)
  target_link_libraries(vocotype-feedback-tests PRIVATE vocotype_feedback_lib)
  add_test(NAME vocotype-feedback-tests COMMAND vocotype-feedback-tests)
endif()
