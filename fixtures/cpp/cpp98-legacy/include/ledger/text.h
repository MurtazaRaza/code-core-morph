#ifndef LEDGER_TEXT_H
#define LEDGER_TEXT_H

#include <string>
#include <vector>

namespace ledger {

// A UTF-16 code unit, for the export format of the old reporting system.
typedef unsigned short char16_t;
typedef std::vector<char16_t> Utf16;

// Invalid UTF-8 bytes become U+FFFD.
Utf16 utf8_to_utf16(const std::string& utf8);
// Unpaired surrogates become U+FFFD.
std::string utf16_to_utf8(const Utf16& utf16);
// Number of code points in valid UTF-8.
size_t utf8_length(const std::string& utf8);

} // namespace ledger

#endif
