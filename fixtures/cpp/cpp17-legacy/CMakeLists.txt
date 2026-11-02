cmake_minimum_required(VERSION 3.21)
project(PkgMeta CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(pkgmeta
    src/version.cpp
    src/manifest.cpp
    src/render.cpp
    src/resolve.cpp
)
target_include_directories(pkgmeta PUBLIC include)
target_compile_features(pkgmeta PUBLIC cxx_std_17)

enable_testing()
add_subdirectory(tests)
