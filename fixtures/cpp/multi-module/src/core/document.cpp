#include "mm/core/document.h"

namespace mm {
namespace core {

Document::Document(const std::string& title, const std::string& body)
    : title_(title), body_(body) {}

int Document::lineCount() const {
    if (body_.empty()) {
        return 0;
    }
    int lines = 1;
    for (std::string::size_type i = 0; i < body_.size(); ++i) {
        if (body_[i] == '\n' && i + 1 < body_.size()) {
            ++lines;
        }
    }
    return lines;
}

util::StringList Document::words() const {
    util::StringList out;
    util::TokenList all = tokens();
    for (util::TokenList::const_iterator it = all.begin(); it != all.end(); ++it) {
        if (it->kind == util::kWord) {
            out.push_back(util::toLower(it->text));
        }
    }
    return out;
}

util::TokenList Document::tokens() const {
    util::Tokenizer tokenizer(body_);
    return tokenizer.all();
}

}  // namespace core
}  // namespace mm
