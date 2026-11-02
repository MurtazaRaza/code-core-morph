#include "meta/format.h"

namespace meta {

std::string quote(const std::string& text) {
    std::string out = "\"";
    for (std::string::size_type i = 0; i < text.size(); ++i) {
        if (text[i] == '"' || text[i] == '\\') {
            out += '\\';
        }
        out += text[i];
    }
    return out + "\"";
}

}  // namespace meta
