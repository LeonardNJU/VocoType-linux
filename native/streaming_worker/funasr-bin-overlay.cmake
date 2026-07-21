add_executable(vocotype-streaming-worker
    "@VOCOTYPE_WORKER_SOURCE@"
)
target_compile_features(vocotype-streaming-worker PRIVATE cxx_std_17)
target_include_directories(vocotype-streaming-worker PRIVATE
    "${CMAKE_SOURCE_DIR}/include"
    "${CMAKE_SOURCE_DIR}/src"
    "${CMAKE_SOURCE_DIR}/third_party/json/include"
)
target_link_options(vocotype-streaming-worker PRIVATE "-Wl,--no-as-needed")
target_link_libraries(vocotype-streaming-worker PRIVATE funasr)
# Both development bundles and distro packages use relative lookup paths:
#   bundle/bin -> bundle/lib
#   /usr/libexec -> /usr/lib/vocotype
set(_vocotype_worker_rpath "$ORIGIN;$ORIGIN/../lib;$ORIGIN/../lib/vocotype")
set_target_properties(vocotype-streaming-worker PROPERTIES
    BUILD_RPATH "${_vocotype_worker_rpath}"
    INSTALL_RPATH "${_vocotype_worker_rpath}"
    BUILD_WITH_INSTALL_RPATH TRUE
)
foreach(_vocotype_private_target funasr fst glog yaml-cpp)
    if(TARGET ${_vocotype_private_target})
        set_target_properties(${_vocotype_private_target} PROPERTIES
            BUILD_RPATH "$ORIGIN"
            INSTALL_RPATH "$ORIGIN"
            BUILD_WITH_INSTALL_RPATH TRUE
        )
    endif()
endforeach()
