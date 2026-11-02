#include <functional>
#include <memory>
#include <stdexcept>

struct Parser {
    std::auto_ptr<Buffer> take() throw();
    void parse(const char* text) throw(std::runtime_error);
};

int sum(const std::vector<int>& v) {
    register int total = 0;
    std::for_each(v.begin(), v.end(), std::bind1st(std::plus<int>(), 1));
    return total;
}
