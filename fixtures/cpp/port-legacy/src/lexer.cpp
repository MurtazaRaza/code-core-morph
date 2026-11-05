#include "calc/lexer.h"

#include "calc/strutil.h"

namespace calc {

namespace {

bool is_digit(char c)
{
    return c >= '0' && c <= '9';
}

bool is_name_char(char c)
{
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c == '_';
}

} // namespace

Lexer::Lexer(const std::string& text) : text_(text), pos_(0)
{
}

Token Lexer::next()
{
    while (pos_ < text_.size() && (text_[pos_] == ' ' || text_[pos_] == '\t'))
        ++pos_;
    Token token;
    token.pos = pos_;
    if (pos_ >= text_.size()) {
        token.kind = Token::END;
        return token;
    }
    std::size_t start = pos_;
    char c = text_[pos_];
    if (is_digit(c)) {
        while (pos_ < text_.size() && is_digit(text_[pos_]))
            ++pos_;
        token.kind = Token::NUMBER;
        token.text = text_.substr(start, pos_ - start);
    } else if (is_name_char(c)) {
        while (pos_ < text_.size() && is_name_char(text_[pos_]))
            ++pos_;
        token.kind = Token::NAME;
        token.text = strutil::to_lower(text_.substr(start, pos_ - start));
    } else {
        ++pos_;
        token.text = std::string(1, c);
        switch (c) {
        case '+':
        case '-':
        case '*':
        case '/':
            token.kind = Token::OPERATOR;
            break;
        case '(':
            token.kind = Token::LPAREN;
            break;
        case ')':
            token.kind = Token::RPAREN;
            break;
        default:
            token.kind = Token::INVALID;
            break;
        }
    }
    return token;
}

Token Lexer::peek()
{
    std::size_t saved = pos_;
    Token token = next();
    pos_ = saved;
    return token;
}

} // namespace calc
