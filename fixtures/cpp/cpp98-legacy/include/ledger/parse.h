#ifndef LEDGER_PARSE_H
#define LEDGER_PARSE_H

#include <istream>
#include <string>
#include <vector>

#include "ledger/money.h"

namespace ledger {

// Reads one comma-separated field; false at the end of the input.
bool read_field(std::istream& in, std::string& field);
// "12.50" -> 1250 cents. False, and out unchanged, for anything that isn't a number.
bool parse_amount(const std::string& text, Money& out);
// A whole decimal number with nothing after it.
bool parse_count(const std::string& text, long& out);

struct Record
{
    std::string sku;
    long quantity;
    Money unit_price;
};

// "sku,quantity,price" lines. Returns the number of records read; stops at the first bad line
// and sets *bad_line (1-based) when bad_line isn't null.
size_t parse_records(std::istream& in, std::vector<Record>& records, int* bad_line);

} // namespace ledger

#endif
