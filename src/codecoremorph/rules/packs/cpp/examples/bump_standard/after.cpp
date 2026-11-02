#include <functional>
#include <memory>
#include <stdexcept>

struct Parser {
    std::unique_ptr<Buffer> take() noexcept;
    void parse(const char* text);
};

int sum(const std::vector<int>& v) {
    int total = 0;
    std::for_each(v.begin(), v.end(), [](int x) { return std::plus<int>()(1, x); });
    return total;
}
