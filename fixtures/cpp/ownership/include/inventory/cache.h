#ifndef INVENTORY_CACHE_H
#define INVENTORY_CACHE_H

#include <map>
#include <string>

namespace inventory {

// A least-recently-used cache of prices, built on a hand-rolled doubly linked list.
class PriceCache {
public:
    explicit PriceCache(int capacity);
    ~PriceCache();

    void put(const std::string& key, double price);
    bool get(const std::string& key, double* price);  // false if missing
    int size() const { return size_; }
    std::string oldest() const;  // empty if the cache is empty

private:
    struct Node {
        std::string key;
        double price;
        Node* prev;
        Node* next;
    };

    PriceCache(const PriceCache&);
    PriceCache& operator=(const PriceCache&);

    void unlink(Node* node);
    void pushFront(Node* node);

    int capacity_;
    int size_;
    Node* head_;  // most recent
    Node* tail_;  // least recent
    std::map<std::string, Node*> index_;
};

}  // namespace inventory

#endif  // INVENTORY_CACHE_H
