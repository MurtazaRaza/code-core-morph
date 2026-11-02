#ifndef INVENTORY_FACTORY_H
#define INVENTORY_FACTORY_H

#include <string>
#include <vector>

#include "inventory/item.h"

namespace inventory {

// Creates an item from a "kind:name:price:quantity[:days]" spec.
// Returns NULL for a malformed spec. The caller owns the result.
Item* createItem(const std::string& spec);

// Creates every item in specs; malformed ones are skipped. The caller owns the items.
std::vector<Item*> createItems(const std::vector<std::string>& specs);

// Deletes every item and clears the vector.
void destroyItems(std::vector<Item*>& items);

// A text report built on the heap; the caller deletes it.
struct Report {
    std::string title;
    std::vector<std::string> lines;
};

Report* makeReport(const std::string& title, const std::vector<Item*>& items);

}  // namespace inventory

#endif  // INVENTORY_FACTORY_H
