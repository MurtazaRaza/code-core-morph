#include "jobs/document.h"

namespace jobs {

Document::Document(const std::string& text) : text_(std::make_shared<const std::string>(text))
{
}

void Document::append(const std::string& more)
{
    if (more.empty())
        return;
    text_ = std::make_shared<const std::string>(*text_ + more);
}

std::size_t Document::words() const
{
    std::size_t count = 0;
    bool in_word = false;
    for (char c : *text_) {
        bool space = c == ' ' || c == '\t' || c == '\n';
        if (!space && !in_word)
            ++count;
        in_word = !space;
    }
    return count;
}

} // namespace jobs
