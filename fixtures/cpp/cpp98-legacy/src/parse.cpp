#include "ledger/parse.h"

#include <sstream>

#include "ledger/compat.h"

namespace ledger {

bool read_field(std::istream& in, std::string& field)
{
    return std::getline(in, field, ',');
}

bool parse_amount(const std::string& text, Money& out)
{
    std::istringstream in(text);
    double units = 0;
    bool ok = in >> units;
    if (!ok)
        return false;
    char rest;
    if (in >> rest)
        return false;
    out = money_from_units(units);
    return true;
}

bool parse_count(const std::string& text, long& out)
{
    std::istringstream in(text);
    long value = 0;
    if (!(in >> value))
        return false;
    char rest;
    if (in >> rest)
        return false;
    out = value;
    return true;
}

namespace {

bool parse_line(const std::string& line, Record& record)
{
    std::istringstream fields(line);
    std::string sku, quantity, price;
    if (!read_field(fields, sku) || !read_field(fields, quantity) || !read_field(fields, price))
        return false;
    if (sku.empty())
        return false;
    record.sku = sku;
    return parse_count(quantity, record.quantity) && parse_amount(price, record.unit_price);
}

} // namespace

size_t parse_records(std::istream& in, std::vector<Record>& records, int* bad_line)
{
    if (bad_line != nullptr)
        *bad_line = 0;
    std::string line;
    int number = 0;
    size_t read = 0;
    while (std::getline(in, line)) {
        ++number;
        if (line.empty())
            continue;
        Record record;
        if (!parse_line(line, record)) {
            if (bad_line != nullptr)
                *bad_line = number;
            break;
        }
        records.push_back(record);
        ++read;
    }
    return read;
}

} // namespace ledger
