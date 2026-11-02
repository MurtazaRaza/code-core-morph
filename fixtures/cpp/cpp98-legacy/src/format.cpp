#include "ledger/format.h"

#define __STDC_FORMAT_MACROS
#include <ctype.h>
#include <inttypes.h>
#include <stdio.h>

#include <algorithm>
#include <functional>

namespace ledger {

std::string format_count(int64_t n)
{
    char buffer[32];
    sprintf(buffer, "%"PRId64, n);
    return buffer;
}

std::string format_id(const char* prefix, int64_t id)
{
    char buffer[64];
    sprintf(buffer, "%s-%08"PRId64, prefix, id);
    return buffer;
}

std::string upper(const std::string& text)
{
    std::string result(text);
    std::transform(result.begin(), result.end(), result.begin(), std::ptr_fun<int, int>(toupper));
    return result;
}

std::string join(const StringList& parts, const char* sep)
{
    std::string result;
    for (StringList::const_iterator it = parts.begin(); it != parts.end(); ++it) {
        if (it != parts.begin())
            result += sep;
        result += *it;
    }
    return result;
}

std::string pad_left(const std::string& text, size_t width)
{
    if (text.size() >= width)
        return text;
    return std::string(width - text.size(), ' ') + text;
}

} // namespace ledger
