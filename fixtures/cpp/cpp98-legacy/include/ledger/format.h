#ifndef LEDGER_FORMAT_H
#define LEDGER_FORMAT_H

#include <stdint.h>

#include <string>
#include <vector>

namespace ledger {

typedef std::vector<std::string> StringList;

// 1234567 -> "1234567".
std::string format_count(int64_t n);
// ("INV", 42) -> "INV-00000042".
std::string format_id(const char* prefix, int64_t id);
// Upper-cases ASCII letters.
std::string upper(const std::string& text);
// Joins with sep; an empty list gives "".
std::string join(const StringList& parts, const char* sep);
// Pads on the left with spaces to width characters (never truncates).
std::string pad_left(const std::string& text, size_t width);

} // namespace ledger

#endif
