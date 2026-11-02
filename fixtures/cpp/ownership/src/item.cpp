#include "inventory/item.h"

#include <sstream>

namespace inventory {

namespace {
int g_live = 0;
}  // namespace

Item::Item(const std::string& name, double price, int quantity)
    : name_(name), price_(price), quantity_(quantity) {
    ++g_live;
}

Item::Item(const Item& other)
    : name_(other.name_), price_(other.price_), quantity_(other.quantity_) {
    ++g_live;
}

Item::~Item() { --g_live; }

double Item::value() const { return price_ * quantity_; }

std::string Item::describe() const {
    std::ostringstream out;
    out << name_ << " x" << quantity_ << " @ " << price_;
    return out.str();
}

Item* Item::clone() const { return new Item(*this); }

int Item::liveCount() { return g_live; }

Perishable::Perishable(const std::string& name, double price, int quantity, int daysLeft)
    : Item(name, price, quantity), daysLeft_(daysLeft) {}

double Perishable::value() const {
    if (daysLeft_ <= 0) {
        return 0.0;
    }
    double base = Item::value();
    return daysLeft_ < 3 ? base / 2.0 : base;
}

std::string Perishable::describe() const {
    std::ostringstream out;
    out << Item::describe() << " (" << daysLeft_ << " days left)";
    return out.str();
}

Item* Perishable::clone() const { return new Perishable(*this); }

}  // namespace inventory
