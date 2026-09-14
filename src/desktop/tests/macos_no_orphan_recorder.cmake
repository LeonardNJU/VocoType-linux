if(NOT DEFINED SETTINGS_APP OR NOT DEFINED TEST_ROOT)
  message(FATAL_ERROR "SETTINGS_APP and TEST_ROOT are required")
endif()

file(REMOVE_RECURSE "${TEST_ROOT}")
file(MAKE_DIRECTORY "${TEST_ROOT}")
set(output "${TEST_ROOT}/menu-smoke.json")
execute_process(
  COMMAND "${CMAKE_COMMAND}" -E env
          "XDG_CONFIG_HOME=${TEST_ROOT}/config"
          "VOCOTYPE_SOCKET=${TEST_ROOT}/vocotype.sock"
          "VOCOTYPE_CACHE_DIR=${TEST_ROOT}/cache"
          "${SETTINGS_APP}" --menu-smoke "${output}"
  RESULT_VARIABLE app_result
  TIMEOUT 12
)
if(NOT app_result EQUAL 0)
  message(FATAL_ERROR "Settings smoke failed: ${app_result}")
endif()

execute_process(COMMAND /bin/sleep 1)
execute_process(
  COMMAND /usr/bin/pgrep -f "vocotype-audio-recorder.*${TEST_ROOT}"
  RESULT_VARIABLE pgrep_result
  OUTPUT_VARIABLE orphan_pids
  ERROR_QUIET
  OUTPUT_STRIP_TRAILING_WHITESPACE
)
if(pgrep_result EQUAL 0)
  execute_process(COMMAND /usr/bin/pkill -9 -f "vocotype-audio-recorder.*${TEST_ROOT}")
  message(FATAL_ERROR "Settings left orphan recorder(s): ${orphan_pids}")
endif()
