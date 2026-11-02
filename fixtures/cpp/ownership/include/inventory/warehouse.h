#ifndef INVENTORY_WAREHOUSE_H
#define INVENTORY_WAREHOUSE_H

#include <string>
#include <vector>

#include "inventory/item.h"

namespace inventory {

// Owns its items: add() takes ownership, the destructor deletes everything.
class Warehouse {
public:
    explicit Warehouse(const std::string& name);
    Warehouse(const Warehouse& other);
    Warehouse& operator=(const Warehouse& other);
    ~Warehouse();

    const std::string& name() const { return name_; }

    void add(Item* item);           // takes ownership
    bool remove(const std::string& name);
    Item* find(const std::string& name) const;  // non-owning; NULL if missing
    Item* release(const std::string& name);     // gives ownership back to the caller

    int size() const { return static_cast<int>(items_.size()); }
    double totalValue() const;
    std::vector<std::string> lowStock(int threshold) const;

private:
    void clear();

    std::string name_;
    std::vector<Item*> items_;
};

}  // namespace inventory

#endif  // INVENTORY_WAREHOUSE_H
