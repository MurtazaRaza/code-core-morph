#include "mm/util/tokenizer.h"

#include <cctype>

namespace mm {
namespace util {

namespace {
bool isWordChar(char c) { return std::isalpha(static_cast<unsigned char>(c)) || c == '\''; }
bool isDigit(char c) { return std::isdigit(static_cast<unsigned char>(c)) != 0; }
}  // namespace

Tokenizer::Tokenizer(const std::string& text) : text_(text), pos_(0), line_(1) {}

bool Tokenizer::next(Token* token) {
    while (pos_ < text_.size() && std::isspace(static_cast<unsigned char>(text_[pos_]))) {
        if (text_[pos_] == '\n') {
            ++line_;
        }
        ++pos_;
    }
    if (pos_ >= text_.size()) {
        return false;
    }
    Token t;
    t.line = line_;
    std::string::size_type start = pos_;
    if (isWordChar(text_[pos_])) {
        while (pos_ < text_.size() && isWordChar(text_[pos_])) {
            ++pos_;
        }
        t.kind = kWord;
    } else if (isDigit(text_[pos_])) {
        while (pos_ < text_.size() && (isDigit(text_[pos_]) || text_[pos_] == '.')) {
            ++pos_;
        }
        t.kind = kNumber;
    } else {
        ++pos_;
        t.kind = kPunctuation;
    }
    t.text = text_.substr(start, pos_ - start);
    if (token != NULL) {
        *token = t;
    }
    return true;
}

TokenList Tokenizer::all() {
    TokenList tokens;
    Token token;
    while (next(&token)) {
        tokens.push_back(token);
    }
    return tokens;
}

}  // namespace util
}  // namespace mm
