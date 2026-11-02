#ifndef LEDGER_INVENTORY_H
#define LEDGER_INVENTORY_H

#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace ledger {

typedef std::map<std::string, int> StockMap;
typedef std::pair<std::string, int> StockLine;
typedef std::vector<StockLine> StockLines;

class Inventory
{
public:
    Inventory();
    virtual ~Inventory();

    void add(const std::string& sku, int count);
    // Throws std::out_of_range when there is less stock than count.
    void remove(const std::string& sku, int count) throw(std::out_of_range);
    int count(const std::string& sku) const throw();
    // Null when the SKU has never been stocked.
    const int* find(const std::string& sku) const;
    size_t size() const;

    // Highest count first; equal counts in SKU order.
    StockLines sorted_by_count() const;
    // SKUs whose count is below threshold, in SKU order.
    std::vector<std::string> below(int threshold) const;
    int total() const;

    std::auto_ptr<Inventory> clone() const;

protected:
    virtual void on_change(const std::string& sku, int old_count, int new_count);

private:
    StockMap stock_;
};

// Counts every change, for audits.
class AuditedInventory : public Inventory
{
public:
    AuditedInventory();
    int changes() const;

protected:
    virtual void on_change(const std::string& sku, int old_count, int new_count);

private:
    int changes_;
};

} // namespace ledger

#endif
