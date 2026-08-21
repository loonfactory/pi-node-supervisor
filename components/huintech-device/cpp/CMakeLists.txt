cmake_minimum_required(VERSION 3.13)

project(huintech_sp485en LANGUAGES CXX)

add_library(huintech_sp485en
    src/sp485en.cpp
)

target_include_directories(huintech_sp485en
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_compile_features(huintech_sp485en PUBLIC cxx_std_11)

if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(huintech_sp485en PRIVATE
        -Wall -Wextra -Werror -pedantic -fno-exceptions -fno-rtti
    )
endif()

option(HUINTECH_BUILD_TESTS "Build host tests" ON)

if(HUINTECH_BUILD_TESTS)
    enable_testing()

    add_executable(sp485en_tests
        tests/test_sp485en.cpp
    )

    target_link_libraries(sp485en_tests PRIVATE huintech_sp485en)
    target_compile_features(sp485en_tests PRIVATE cxx_std_11)

    if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
        target_compile_options(sp485en_tests PRIVATE
            -Wall -Wextra -Werror -pedantic -fno-exceptions -fno-rtti
        )
    endif()

    add_test(NAME sp485en_tests COMMAND sp485en_tests)
endif()
