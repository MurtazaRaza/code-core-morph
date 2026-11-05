cmake_minimum_required(VERSION 3.21)
project(TinyCalc CXX)

# Written for C++98 compilers; the build never names a standard.

add_library(calc
    src/strutil.cpp
    src/rational.cpp
    src/lexer.cpp
    src/expr.cpp
    src/parser.cpp
    src/calc.cpp
)
target_include_directories(calc PUBLIC include)

add_executable(tinycalc app/main.cpp)
target_link_libraries(tinycalc PRIVATE calc)

enable_testing()
add_subdirectory(tests)
