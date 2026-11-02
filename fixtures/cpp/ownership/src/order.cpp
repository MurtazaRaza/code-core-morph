#include "inventory/order.h"

#include "inventory/warehouse.h"

namespace inventory {

Order::Order(const Customer* customer)
    : customer_(customer), lines_(new OrderLine[2]), count_(0), capacity_(2) {}

Order::Order(const Order& other)
    : customer_(other.customer_),
      lines_(new OrderLine[other.capacity_]),
      count_(other.count_),
      capacity_(other.capacity_) {
    for (int i = 0; i < count_; ++i) {
        lines_[i] = other.lines_[i];
    }
}

Order& Order::operator=(const Order& other) {
    if (this == &other) {
        return *this;
    }
    OrderLine* fresh = new OrderLine[other.capacity_];
    for (int i = 0; i < other.count_; ++i) {
        fresh[i] = other.lines_[i];
    }
    delete[] lines_;
    lines_ = fresh;
    customer_ = other.customer_;
    count_ = other.count_;
    capacity_ = other.capacity_;
    return *this;
}

Order::~Order() { delete[] lines_; }

void Order::grow() {
    int capacity = capacity_ * 2;
    OrderLine* bigger = new OrderLine[capacity];
    for (int i = 0; i < count_; ++i) {
        bigger[i] = lines_[i];
    }
    delete[] lines_;
    lines_ = bigger;
    capacity_ = capacity;
}

void Order::addLine(const std::string& item, int quantity, double unitPrice) {
    if (count_ == capacity_) {
        grow();
    }
    OrderLine line;
    line.item = item;
    line.quantity = quantity;
    line.unitPrice = unitPrice;
    lines_[count_++] = line;
}

double Order::total() const {
    double sum = 0.0;
    for (int i = 0; i < count_; ++i) {
        sum += lines_[i].quantity * lines_[i].unitPrice;
    }
    if (customer_ != NULL) {
        sum *= 1.0 - customer_->discount;
    }
    return sum;
}

bool Order::fulfil(Warehouse& warehouse) const {
    for (int i = 0; i < count_; ++i) {
        Item* item = warehouse.find(lines_[i].item);
        if (item == NULL || item->quantity() < lines_[i].quantity) {
            return false;
        }
    }
    for (int i = 0; i < count_; ++i) {
        Item* item = warehouse.find(lines_[i].item);
        item->setQuantity(item->quantity() - lines_[i].quantity);
    }
    return true;
}

}  // namespace inventory
