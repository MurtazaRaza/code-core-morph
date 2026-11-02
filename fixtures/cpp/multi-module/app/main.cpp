// mm_app: counts the words of its standard input, after an optional pipeline.
// Usage: mm_app [PIPELINE] [TOP]
#include <cstdlib>
#include <iostream>
#include <iterator>
#include <sstream>
#include <string>

#include "mm/core/counter.h"
#include "mm/core/document.h"
#include "mm/core/pipeline.h"

int main(int argc, char** argv) {
    std::string spec = argc > 1 ? argv[1] : "lower,trim";
    int top = argc > 2 ? std::atoi(argv[2]) : 10;

    mm::core::Pipeline* pipeline = mm::core::parsePipeline(spec);
    if (pipeline == NULL) {
        std::cerr << "mm_app: bad pipeline: " << spec << "\n";
        return 2;
    }

    std::ostringstream input;
    input << std::cin.rdbuf();
    mm::core::Document document("stdin", pipeline->run(input.str()));
    delete pipeline;

    mm::core::WordCounter counter;
    counter.add(document);
    mm::core::Ranking ranking = counter.top(top);
    for (mm::core::Ranking::const_iterator it = ranking.begin(); it != ranking.end(); ++it) {
        std::cout << it->second << " " << it->first << "\n";
    }
    std::cout << counter.total() << " words, " << counter.distinct() << " distinct\n";
    return 0;
}
