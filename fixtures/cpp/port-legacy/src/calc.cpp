#include "calc/calc.h"

#include <cstring>
#include <stdexcept>
#include <string>

#include "calc/parser.h"
#include "stack.h"

struct calc_session
{
    calc::Stack<calc::Rational> history;
};

namespace {

int copy_out(const std::string& text, char* out, size_t out_size)
{
    if (text.size() + 1 > out_size)
        return CALC_BUFFER_TOO_SMALL;
    std::memcpy(out, text.c_str(), text.size() + 1);
    return CALC_OK;
}

calc::Rational ans_of(const calc_session* session)
{
    return session->history.empty() ? calc::Rational() : session->history.top();
}

} // namespace

extern "C" {

calc_session* calc_open(void)
{
    return new calc_session();
}

void calc_close(calc_session* session)
{
    delete session;
}

int calc_eval(calc_session* session, const char* text, char* out, size_t out_size,
              size_t* error_pos)
{
    calc::Expr* tree = 0;
    try {
        tree = calc::parse(text, ans_of(session));
    } catch (const calc::ParseError& e) {
        if (error_pos)
            *error_pos = e.pos();
        return CALC_SYNTAX_ERROR;
    }
    calc::Rational result;
    try {
        result = tree->eval();
    } catch (const std::domain_error&) {
        delete tree;
        return CALC_DIVISION_BY_ZERO;
    }
    delete tree;
    int status = copy_out(result.str(), out, out_size);
    if (status == CALC_OK)
        session->history.push(result);
    return status;
}

int calc_undo(calc_session* session)
{
    if (session->history.empty())
        return CALC_NO_RESULT;
    session->history.pop();
    return CALC_OK;
}

size_t calc_history_size(const calc_session* session)
{
    return session->history.size();
}

const char* calc_status_text(int status)
{
    switch (status) {
    case CALC_OK:
        return "ok";
    case CALC_SYNTAX_ERROR:
        return "syntax error";
    case CALC_DIVISION_BY_ZERO:
        return "division by zero";
    case CALC_BUFFER_TOO_SMALL:
        return "buffer too small";
    case CALC_NO_RESULT:
        return "no result";
    default:
        return "unknown status";
    }
}

} // extern "C"
