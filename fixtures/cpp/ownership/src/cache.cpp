#include "inventory/cache.h"

namespace inventory {

PriceCache::PriceCache(int capacity)
    : capacity_(capacity > 0 ? capacity : 1), size_(0), head_(NULL), tail_(NULL) {}

PriceCache::~PriceCache() {
    Node* node = head_;
    while (node != NULL) {
        Node* next = node->next;
        delete node;
        node = next;
    }
}

void PriceCache::unlink(Node* node) {
    if (node->prev != NULL) {
        node->prev->next = node->next;
    } else {
        head_ = node->next;
    }
    if (node->next != NULL) {
        node->next->prev = node->prev;
    } else {
        tail_ = node->prev;
    }
    node->prev = NULL;
    node->next = NULL;
}

void PriceCache::pushFront(Node* node) {
    node->prev = NULL;
    node->next = head_;
    if (head_ != NULL) {
        head_->prev = node;
    }
    head_ = node;
    if (tail_ == NULL) {
        tail_ = node;
    }
}

void PriceCache::put(const std::string& key, double price) {
    std::map<std::string, Node*>::iterator it = index_.find(key);
    if (it != index_.end()) {
        it->second->price = price;
        unlink(it->second);
        pushFront(it->second);
        return;
    }
    if (size_ == capacity_) {
        Node* victim = tail_;
        unlink(victim);
        index_.erase(victim->key);
        delete victim;
        --size_;
    }
    Node* node = new Node;
    node->key = key;
    node->price = price;
    node->prev = NULL;
    node->next = NULL;
    pushFront(node);
    index_[key] = node;
    ++size_;
}

bool PriceCache::get(const std::string& key, double* price) {
    std::map<std::string, Node*>::iterator it = index_.find(key);
    if (it == index_.end()) {
        return false;
    }
    unlink(it->second);
    pushFront(it->second);
    if (price != NULL) {
        *price = it->second->price;
    }
    return true;
}

std::string PriceCache::oldest() const { return tail_ == NULL ? std::string() : tail_->key; }

}  // namespace inventory
