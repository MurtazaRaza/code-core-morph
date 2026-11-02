add_library(mm_core document.cpp counter.cpp pipeline.cpp)
target_link_libraries(mm_core PUBLIC mm_util)
