#include "toolkit/transaction.h"

#include <exception>

namespace toolkit {

Transaction::Transaction(Journal& journal)
    : journal_(journal), mark_(journal.entries.size()), committed_(false) {}

Transaction::~Transaction() {
    // Roll back when the scope is left by an exception, or without a commit.
    if (std::uncaught_exception() || !committed_) {
        journal_.entries.resize(mark_);
    }
}

void Transaction::record(const std::string& entry) { journal_.append(entry); }

}  // namespace toolkit
