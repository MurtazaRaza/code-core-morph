#pragma once

#include <cstddef>
#include <memory>
#include <string>

namespace jobs {

// A text whose copies share it until one of them changes (copy on write).
class Document
{
public:
    explicit Document(const std::string& text = "");

    std::string text() const { return *text_; }
    void append(const std::string& more);
    // Whether this and other still share one text.
    bool shares_text_with(const Document& other) const { return text_ == other.text_; }
    // Runs of characters other than spaces, tabs and newlines.
    std::size_t words() const;

private:
    std::shared_ptr<const std::string> text_;
};

} // namespace jobs
