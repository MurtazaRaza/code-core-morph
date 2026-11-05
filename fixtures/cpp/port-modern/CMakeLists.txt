cmake_minimum_required(VERSION 3.21)
project(Jobs CXX)

set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(Threads REQUIRED)

add_library(jobs
    src/ids.cpp
    src/job.cpp
    src/queue.cpp
    src/events.cpp
    src/document.cpp
    src/worker.cpp
)
target_include_directories(jobs PUBLIC include)
target_link_libraries(jobs PUBLIC Threads::Threads)

add_executable(jobrun app/main.cpp)
target_link_libraries(jobrun PRIVATE jobs)

enable_testing()
add_subdirectory(tests)
