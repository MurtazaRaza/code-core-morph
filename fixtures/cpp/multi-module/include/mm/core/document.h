#ifndef MM_CORE_DOCUMENT_H
#define MM_CORE_DOCUMENT_H

#include <string>

#include "mm/util/strings.h"
#include "mm/util/tokenizer.h"

namespace mm {
namespace core {

class Document {
public:
    Document(const std::string& title, const std::string& body);

    const std::string& title() const { return title_; }
    const std::string& body() const { return body_; }
    int lineCount() const;
    util::StringList words() const;  // lower-cased
    util::TokenList tokens() const;

private:
    std::string title_;
    std::string body_;
};

}  // namespace core
}  // namespace mm

#endif  // MM_CORE_DOCUMENT_H
