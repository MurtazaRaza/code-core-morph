#ifndef MM_UTIL_TOKENIZER_H
#define MM_UTIL_TOKENIZER_H

#include <string>

#include "mm/util/strings.h"

namespace mm {
namespace util {

enum TokenKind { kWord, kNumber, kPunctuation };

struct Token {
    TokenKind kind;
    std::string text;
    int line;
};

typedef std::vector<Token> TokenList;

// Splits text into words, numbers and punctuation, tracking line numbers.
class Tokenizer {
public:
    explicit Tokenizer(const std::string& text);

    bool next(Token* token);  // false at the end; token may be NULL to skip
    TokenList all();

private:
    std::string text_;
    std::string::size_type pos_;
    int line_;
};

}  // namespace util
}  // namespace mm

#endif  // MM_UTIL_TOKENIZER_H
