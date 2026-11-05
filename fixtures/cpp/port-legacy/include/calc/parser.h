#ifndef CALC_PARSER_H
#define CALC_PARSER_H

#include <cstddef>
#include <stdexcept>
#include <string>

#include "calc/expr.h"
#include "calc/rational.h"

namespace calc {

class ParseError : public std::runtime_error
{
public:
    ParseError(const std::string& message, std::size_t pos);

    // The byte offset of the offending token, or the input's length at its end.
    std::size_t pos() const { return pos_; }

private:
    std::size_t pos_;
};

// Parses "1 + 2 * (3 - ans) / -4": integers, + - * / with the usual precedence (left to right),
// unary minus, parentheses, and the name "ans", which stands for the given value. Returns a new
// tree for the caller to delete. Throws ParseError.
Expr* parse(const std::string& text, const Rational& ans);

} // namespace calc

#endif
