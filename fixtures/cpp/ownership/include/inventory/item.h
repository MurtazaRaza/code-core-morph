#ifndef INVENTORY_ITEM_H
#define INVENTORY_ITEM_H

#include <string>

namespace inventory {

// A stock item. Items are heap-allocated and owned by whoever holds the pointer;
// clone() returns a new item the caller must delete.
class Item {
public:
    Item(const std::string& name, double price, int quantity);
    Item(const Item& other);
    virtual ~Item();

    const std::string& name() const { return name_; }
    double price() const { return price_; }
    int quantity() const { return quantity_; }
    void setQuantity(int quantity) { quantity_ = quantity; }

    virtual double value() const;
    virtual std::string describe() const;
    virtual Item* clone() const;

    static int liveCount();

private:
    std::string name_;
    double price_;
    int quantity_;
};

// An item whose value drops as it nears its expiry day.
class Perishable : public Item {
public:
    Perishable(const std::string& name, double price, int quantity, int daysLeft);

    int daysLeft() const { return daysLeft_; }
    virtual double value() const;
    virtual std::string describe() const;
    virtual Item* clone() const;

private:
    int daysLeft_;
};

}  // namespace inventory

#endif  // INVENTORY_ITEM_H
