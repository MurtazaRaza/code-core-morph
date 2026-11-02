#ifndef TOOLKIT_TRANSACTION_H
#define TOOLKIT_TRANSACTION_H

#include <string>
#include <vector>

namespace toolkit {

// Applies changes to a journal; if the scope ends by an exception, rolls them back.
class Journal {
public:
    void append(const std::string& entry) { entries.push_back(entry); }
    std::vector<std::string> entries;
};

class Transaction {
public:
    explicit Transaction(Journal& journal);
    ~Transaction();

    void record(const std::string& entry);
    void commit() { committed_ = true; }

private:
    Transaction(const Transaction&);
    Transaction& operator=(const Transaction&);

    Journal& journal_;
    std::size_t mark_;
    bool committed_;
};

}  // namespace toolkit

#endif  // TOOLKIT_TRANSACTION_H
