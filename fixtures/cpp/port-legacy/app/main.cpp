// tinycalc: evaluates each argument, or else each line of standard input, and prints the
// results. "ans" is the last result; ":undo" forgets it; lines starting with '#' are comments.
#include <iostream>
#include <string>

#include "calc/calc.h"
#include "calc/strutil.h"

namespace {

int run_line(calc_session* session, const std::string& raw)
{
    std::string line = calc::strutil::trim(raw);
    if (line.empty() || calc::strutil::starts_with(line, "#"))
        return 0;
    if (calc::strutil::to_lower(line) == ":undo") {
        int status = calc_undo(session);
        if (status != CALC_OK) {
            std::cerr << "error: " << calc_status_text(status) << '\n';
            return 1;
        }
        return 0;
    }
    char out[64];
    size_t pos = 0;
    int status = calc_eval(session, line.c_str(), out, sizeof out, &pos);
    if (status == CALC_SYNTAX_ERROR) {
        std::cerr << "error: " << calc_status_text(status) << " at column " << pos + 1 << '\n';
        return 1;
    }
    if (status != CALC_OK) {
        std::cerr << "error: " << calc_status_text(status) << '\n';
        return 1;
    }
    std::cout << out << '\n';
    return 0;
}

} // namespace

int main(int argc, char** argv)
{
    calc_session* session = calc_open();
    int failures = 0;
    if (argc > 1) {
        for (int i = 1; i < argc; ++i)
            failures += run_line(session, argv[i]);
    } else {
        std::string line;
        while (std::getline(std::cin, line))
            failures += run_line(session, line);
    }
    calc_close(session);
    return failures == 0 ? 0 : 1;
}
