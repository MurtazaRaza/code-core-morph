cmake_minimum_required(VERSION 3.21)
project(Inventory CXX)

set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(inventory
    src/item.cpp
    src/warehouse.cpp
    src/order.cpp
    src/factory.cpp
    src/cache.cpp
)
target_include_directories(inventory PUBLIC include)

enable_testing()
add_subdirectory(tests)
