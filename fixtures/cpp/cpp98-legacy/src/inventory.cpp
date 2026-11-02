#include "ledger/inventory.h"

#include <algorithm>
#include <functional>

#include "ledger/compat.h"

namespace ledger {

namespace {

bool by_count_then_sku(const StockLine& a, const StockLine& b)
{
    if (a.second != b.second)
        return a.second > b.second;
    return a.first < b.first;
}

} // namespace

Inventory::Inventory() {}

Inventory::~Inventory() {}

void Inventory::add(const std::string& sku, int count)
{
    int old_count = stock_[sku];
    stock_[sku] = old_count + count;
    on_change(sku, old_count, old_count + count);
}

void Inventory::remove(const std::string& sku, int count) throw(std::out_of_range)
{
    StockMap::iterator it = stock_.find(sku);
    if (it == stock_.end() || it->second < count)
        throw std::out_of_range("not enough stock for " + sku);
    int old_count = it->second;
    it->second -= count;
    on_change(sku, old_count, it->second);
}

int Inventory::count(const std::string& sku) const throw()
{
    StockMap::const_iterator it = stock_.find(sku);
    return it == stock_.end() ? 0 : it->second;
}

const int* Inventory::find(const std::string& sku) const
{
    StockMap::const_iterator it = stock_.find(sku);
    if (it == stock_.end())
        return nullptr;
    return &it->second;
}

size_t Inventory::size() const
{
    return stock_.size();
}

StockLines Inventory::sorted_by_count() const
{
    StockLines lines;
    for (StockMap::const_iterator it = stock_.begin(); it != stock_.end(); ++it)
        lines.push_back(std::make_pair(it->first, it->second));
    std::sort(lines.begin(), lines.end(), by_count_then_sku);
    return lines;
}

std::vector<std::string> Inventory::below(int threshold) const
{
    std::vector<std::string> skus;
    std::vector<int> counts;
    for (StockMap::const_iterator it = stock_.begin(); it != stock_.end(); ++it) {
        skus.push_back(it->first);
        counts.push_back(it->second);
    }
    std::vector<std::string> result;
    std::vector<int>::iterator found = counts.begin();
    while ((found = std::find_if(found, counts.end(), std::bind2nd(std::less<int>(), threshold)))
           != counts.end()) {
        result.push_back(skus[found - counts.begin()]);
        ++found;
    }
    return result;
}

int Inventory::total() const
{
    int sum = 0;
    for (StockMap::const_iterator it = stock_.begin(); it != stock_.end(); ++it)
        sum += it->second;
    return sum;
}

std::auto_ptr<Inventory> Inventory::clone() const
{
    std::auto_ptr<Inventory> copy(new Inventory);
    copy->stock_ = stock_;
    return copy;
}

void Inventory::on_change(const std::string&, int, int) {}

AuditedInventory::AuditedInventory() : changes_(0) {}

int AuditedInventory::changes() const
{
    return changes_;
}

void AuditedInventory::on_change(const std::string&, int old_count, int new_count)
{
    if (old_count != new_count)
        ++changes_;
}

} // namespace ledger
