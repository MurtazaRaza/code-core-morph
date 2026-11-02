#include "ledger/text.h"

namespace ledger {

namespace {

const unsigned long kReplacement = 0xFFFD;

void append_utf16(Utf16& out, unsigned long code_point)
{
    if (code_point < 0x10000) {
        out.push_back(static_cast<char16_t>(code_point));
        return;
    }
    code_point -= 0x10000;
    out.push_back(static_cast<char16_t>(0xD800 + (code_point >> 10)));
    out.push_back(static_cast<char16_t>(0xDC00 + (code_point & 0x3FF)));
}

void append_utf8(std::string& out, unsigned long code_point)
{
    if (code_point < 0x80) {
        out += static_cast<char>(code_point);
    } else if (code_point < 0x800) {
        out += static_cast<char>(0xC0 | (code_point >> 6));
        out += static_cast<char>(0x80 | (code_point & 0x3F));
    } else if (code_point < 0x10000) {
        out += static_cast<char>(0xE0 | (code_point >> 12));
        out += static_cast<char>(0x80 | ((code_point >> 6) & 0x3F));
        out += static_cast<char>(0x80 | (code_point & 0x3F));
    } else {
        out += static_cast<char>(0xF0 | (code_point >> 18));
        out += static_cast<char>(0x80 | ((code_point >> 12) & 0x3F));
        out += static_cast<char>(0x80 | ((code_point >> 6) & 0x3F));
        out += static_cast<char>(0x80 | (code_point & 0x3F));
    }
}

// Length of the sequence a lead byte starts, or 0 for a byte that can't start one.
int sequence_length(unsigned char lead)
{
    if (lead < 0x80)
        return 1;
    if ((lead & 0xE0) == 0xC0)
        return 2;
    if ((lead & 0xF0) == 0xE0)
        return 3;
    if ((lead & 0xF8) == 0xF0)
        return 4;
    return 0;
}

} // namespace

Utf16 utf8_to_utf16(const std::string& utf8)
{
    Utf16 out;
    register size_t i = 0;
    while (i < utf8.size()) {
        unsigned char lead = static_cast<unsigned char>(utf8[i]);
        int length = sequence_length(lead);
        if (length == 0 || i + length > utf8.size()) {
            append_utf16(out, kReplacement);
            ++i;
            continue;
        }
        unsigned long code_point = length == 1 ? lead : lead & (0x7F >> length);
        bool valid = true;
        for (int k = 1; k < length; ++k) {
            unsigned char next = static_cast<unsigned char>(utf8[i + k]);
            if ((next & 0xC0) != 0x80) {
                valid = false;
                break;
            }
            code_point = (code_point << 6) | (next & 0x3F);
        }
        if (!valid) {
            append_utf16(out, kReplacement);
            ++i;
            continue;
        }
        append_utf16(out, code_point);
        i += length;
    }
    return out;
}

std::string utf16_to_utf8(const Utf16& utf16)
{
    std::string out;
    auto int written = 0;
    for (size_t i = 0; i < utf16.size(); ++i) {
        unsigned long unit = utf16[i];
        if (unit >= 0xD800 && unit <= 0xDBFF && i + 1 < utf16.size()) {
            unsigned long low = utf16[i + 1];
            if (low >= 0xDC00 && low <= 0xDFFF) {
                append_utf8(out, 0x10000 + ((unit - 0xD800) << 10) + (low - 0xDC00));
                ++i;
                ++written;
                continue;
            }
        }
        if (unit >= 0xD800 && unit <= 0xDFFF)
            unit = kReplacement;
        append_utf8(out, unit);
        ++written;
    }
    return written > 0 ? out : std::string();
}

size_t utf8_length(const std::string& utf8)
{
    size_t count = 0;
    for (std::string::const_iterator it = utf8.begin(); it != utf8.end(); ++it) {
        if ((static_cast<unsigned char>(*it) & 0xC0) != 0x80)
            ++count;
    }
    return count;
}

} // namespace ledger
