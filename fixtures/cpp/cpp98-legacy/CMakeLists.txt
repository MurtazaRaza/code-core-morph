cmake_minimum_required(VERSION 3.21)
project(Ledger CXX)

# Written for C++98 compilers. Like most build files of its time, it never names a standard.

add_library(ledger
    src/money.cpp
    src/format.cpp
    src/parse.cpp
    src/inventory.cpp
    src/transaction.cpp
    src/text.cpp
)
target_include_directories(ledger PUBLIC include)

enable_testing()
add_subdirectory(tests)
