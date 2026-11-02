#include "inventory/factory.h"

#include <cstdlib>
#include <sstream>

namespace inventory {

namespace {

std::vector<std::string> split(const std::string& text, char separator) {
    std::vector<std::string> parts;
    std::string current;
    for (size_t i = 0; i < text.size(); ++i) {
        if (text[i] == separator) {
            parts.push_back(current);
            current.clear();
        } else {
            current += text[i];
        }
    }
    parts.push_back(current);
    return parts;
}

bool toNumber(const std::string& text, double* out) {
    if (text.empty()) {
        return false;
    }
    char* end = NULL;
    double value = std::strtod(text.c_str(), &end);
    if (end == NULL || *end != '\0') {
        return false;
    }
    *out = value;
    return true;
}

}  // namespace

Item* createItem(const std::string& spec) {
    std::vector<std::string> parts = split(spec, ':');
    if (parts.size() < 4) {
        return NULL;
    }
    double price = 0.0;
    double quantity = 0.0;
    if (parts[1].empty() || !toNumber(parts[2], &price) || !toNumber(parts[3], &quantity)) {
        return NULL;
    }
    if (parts[0] == "item" && parts.size() == 4) {
        return new Item(parts[1], price, static_cast<int>(quantity));
    }
    double days = 0.0;
    if (parts[0] == "perishable" && parts.size() == 5 && toNumber(parts[4], &days)) {
        return new Perishable(parts[1], price, static_cast<int>(quantity), static_cast<int>(days));
    }
    return NULL;
}

std::vector<Item*> createItems(const std::vector<std::string>& specs) {
    std::vector<Item*> items;
    for (size_t i = 0; i < specs.size(); ++i) {
        Item* item = createItem(specs[i]);
        if (item != NULL) {
            items.push_back(item);
        }
    }
    return items;
}

void destroyItems(std::vector<Item*>& items) {
    for (size_t i = 0; i < items.size(); ++i) {
        delete items[i];
    }
    items.clear();
}

Report* makeReport(const std::string& title, const std::vector<Item*>& items) {
    Report* report = new Report;
    report->title = title;
    double total = 0.0;
    for (size_t i = 0; i < items.size(); ++i) {
        report->lines.push_back(items[i]->describe());
        total += items[i]->value();
    }
    std::ostringstream out;
    out << "total: " << total;
    report->lines.push_back(out.str());
    return report;
}

}  // namespace inventory
