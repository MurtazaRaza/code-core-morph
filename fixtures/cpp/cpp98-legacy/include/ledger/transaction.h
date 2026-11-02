#ifndef LEDGER_TRANSACTION_H
#define LEDGER_TRANSACTION_H

#include <stdexcept>
#include <string>
#include <vector>

#include "ledger/compat.h"
#include "ledger/inventory.h"

namespace ledger {

class RollbackError : public std::runtime_error
{
public:
    explicit RollbackError(const std::string& what) : std::runtime_error(what) {}
};

// Collects stock changes and applies them all on commit(). A strict transaction destroyed with
// pending changes and no commit() throws RollbackError from its destructor, so a forgotten
// commit can't go unnoticed (unless the stack is already unwinding from another exception).
class Transaction
{
public:
    Transaction(Inventory& inventory, bool strict);
    ~Transaction();

    void add(const std::string& sku, int count);
    void remove(const std::string& sku, int count);
    // Applies every change, or none: a removal that fails rolls the earlier ones back.
    void commit();
    size_t pending() const;
    bool committed() const;

private:
    LEDGER_NONCOPYABLE(Transaction);

    Inventory& inventory_;
    bool strict_;
    bool committed_;
    std::vector<std::pair<std::string, int> > changes_;
};

} // namespace ledger

#endif
