function(vocotype_add_worker target source)
    add_executable(${target} "${source}")
    target_compile_features(${target} PRIVATE cxx_std_17)
    target_include_directories(${target} PRIVATE
        "${CMAKE_SOURCE_DIR}/include"
        "${CMAKE_SOURCE_DIR}/src"
        "${CMAKE_SOURCE_DIR}/third_party/json/include"
    )
    if(NOT APPLE)
        target_link_options(${target} PRIVATE "-Wl,--no-as-needed")
        set(_vocotype_worker_rpath "$ORIGIN;$ORIGIN/../lib;$ORIGIN/../lib/vocotype")
    else()
        set(_vocotype_worker_rpath "@loader_path;@loader_path/../lib")
    endif()
    target_link_libraries(${target} PRIVATE funasr)
    set_target_properties(${target} PROPERTIES
        BUILD_RPATH "${_vocotype_worker_rpath}"
        INSTALL_RPATH "${_vocotype_worker_rpath}"
        BUILD_WITH_INSTALL_RPATH TRUE
    )
endfunction()

vocotype_add_worker(vocotype-streaming-worker "@VOCOTYPE_STREAMING_WORKER_SOURCE@")
vocotype_add_worker(vocotype-offline-worker "@VOCOTYPE_OFFLINE_WORKER_SOURCE@")

foreach(_vocotype_private_target funasr fst glog yaml-cpp)
    if(TARGET ${_vocotype_private_target})
        set_target_properties(${_vocotype_private_target} PROPERTIES
            BUILD_RPATH "$<$<PLATFORM_ID:Darwin>:@loader_path>$<$<NOT:$<PLATFORM_ID:Darwin>>:$ORIGIN>"
            INSTALL_RPATH "$<$<PLATFORM_ID:Darwin>:@loader_path>$<$<NOT:$<PLATFORM_ID:Darwin>>:$ORIGIN>"
            BUILD_WITH_INSTALL_RPATH TRUE
        )
    endif()
endforeach()
