#include "ledger/transaction.h"

#include <exception>

namespace ledger {

Transaction::Transaction(Inventory& inventory, bool strict)
    : inventory_(inventory), strict_(strict), committed_(false)
{
}

Transaction::~Transaction()
{
    if (strict_ && !committed_ && !changes_.empty() && !std::uncaught_exception())
        throw RollbackError("transaction destroyed without commit()");
}

void Transaction::add(const std::string& sku, int count)
{
    changes_.push_back(std::pair<std::string, int>(sku, count));
}

void Transaction::remove(const std::string& sku, int count)
{
    changes_.push_back(std::pair<std::string, int>(sku, -count));
}

void Transaction::commit()
{
    size_t applied = 0;
    try {
        for (; applied < changes_.size(); ++applied) {
            const std::pair<std::string, int>& change = changes_[applied];
            if (change.second >= 0)
                inventory_.add(change.first, change.second);
            else
                inventory_.remove(change.first, -change.second);
        }
    } catch (const std::out_of_range&) {
        while (applied > 0) {
            --applied;
            const std::pair<std::string, int>& change = changes_[applied];
            if (change.second >= 0)
                inventory_.remove(change.first, change.second);
            else
                inventory_.add(change.first, -change.second);
        }
        throw;
    }
    committed_ = true;
}

size_t Transaction::pending() const
{
    return committed_ ? 0 : changes_.size();
}

bool Transaction::committed() const
{
    return committed_;
}

} // namespace ledger
