#ifndef INVENTORY_ORDER_H
#define INVENTORY_ORDER_H

#include <string>

namespace inventory {

class Warehouse;

struct Customer {
    std::string name;
    double discount;  // 0.0 .. 1.0
};

struct OrderLine {
    std::string item;
    int quantity;
    double unitPrice;
};

// An order with a growable array of lines (managed by hand) and a non-owning
// pointer to its customer.
class Order {
public:
    explicit Order(const Customer* customer);
    Order(const Order& other);
    Order& operator=(const Order& other);
    ~Order();

    void addLine(const std::string& item, int quantity, double unitPrice);
    int lineCount() const { return count_; }
    const OrderLine& line(int index) const { return lines_[index]; }
    double total() const;
    const Customer* customer() const { return customer_; }

    // Takes the ordered quantities out of the warehouse; false if any line can't be filled.
    bool fulfil(Warehouse& warehouse) const;

private:
    void grow();

    const Customer* customer_;  // not owned
    OrderLine* lines_;
    int count_;
    int capacity_;
};

}  // namespace inventory

#endif  // INVENTORY_ORDER_H
