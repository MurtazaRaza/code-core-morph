#include "inventory/warehouse.h"

#include <cstddef>

namespace inventory {

Warehouse::Warehouse(const std::string& name) : name_(name) {}

Warehouse::Warehouse(const Warehouse& other) : name_(other.name_) {
    for (size_t i = 0; i < other.items_.size(); ++i) {
        items_.push_back(other.items_[i]->clone());
    }
}

Warehouse& Warehouse::operator=(const Warehouse& other) {
    if (this == &other) {
        return *this;
    }
    clear();
    name_ = other.name_;
    for (size_t i = 0; i < other.items_.size(); ++i) {
        items_.push_back(other.items_[i]->clone());
    }
    return *this;
}

Warehouse::~Warehouse() { clear(); }

void Warehouse::clear() {
    for (size_t i = 0; i < items_.size(); ++i) {
        delete items_[i];
    }
    items_.clear();
}

void Warehouse::add(Item* item) {
    if (item == NULL) {
        return;
    }
    Item* existing = find(item->name());
    if (existing != NULL) {
        existing->setQuantity(existing->quantity() + item->quantity());
        delete item;
        return;
    }
    items_.push_back(item);
}

bool Warehouse::remove(const std::string& name) {
    for (std::vector<Item*>::iterator it = items_.begin(); it != items_.end(); ++it) {
        if ((*it)->name() == name) {
            delete *it;
            items_.erase(it);
            return true;
        }
    }
    return false;
}

Item* Warehouse::find(const std::string& name) const {
    for (size_t i = 0; i < items_.size(); ++i) {
        if (items_[i]->name() == name) {
            return items_[i];
        }
    }
    return NULL;
}

Item* Warehouse::release(const std::string& name) {
    for (std::vector<Item*>::iterator it = items_.begin(); it != items_.end(); ++it) {
        if ((*it)->name() == name) {
            Item* item = *it;
            items_.erase(it);
            return item;
        }
    }
    return NULL;
}

double Warehouse::totalValue() const {
    double total = 0.0;
    for (size_t i = 0; i < items_.size(); ++i) {
        total += items_[i]->value();
    }
    return total;
}

std::vector<std::string> Warehouse::lowStock(int threshold) const {
    std::vector<std::string> names;
    for (size_t i = 0; i < items_.size(); ++i) {
        if (items_[i]->quantity() < threshold) {
            names.push_back(items_[i]->name());
        }
    }
    return names;
}

}  // namespace inventory
