#ifndef CALC_LEXER_H
#define CALC_LEXER_H

#include <cstddef>
#include <string>

namespace calc {

struct Token
{
    enum Kind { NUMBER, OPERATOR, LPAREN, RPAREN, NAME, END, INVALID };

    Kind kind;
    std::string text;
    std::size_t pos; // byte offset in the input
};

// Splits an expression into integers, + - * /, parentheses and names ([A-Za-z_]+, lowercased).
class Lexer
{
public:
    explicit Lexer(const std::string& text);

    // The next token: END at the end of the input, and END again after that. Any other
    // character is an INVALID token of its own.
    Token next();
    // The next token, without consuming it.
    Token peek();

    std::size_t position() const { return pos_; }

private:
    std::string text_;
    std::size_t pos_;
};

} // namespace calc

#endif
