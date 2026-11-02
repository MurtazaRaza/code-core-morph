#ifndef TOOLKIT_LOGGER_H
#define TOOLKIT_LOGGER_H

#include <memory>
#include <string>
#include <vector>

namespace toolkit {

class Sink {
public:
    virtual ~Sink() throw() {}
    virtual void write(const std::string& line) = 0;
};

class MemorySink : public Sink {
public:
    virtual void write(const std::string& line) { lines.push_back(line); }
    std::vector<std::string> lines;
};

// Owns its sink through std::auto_ptr: copying a Logger transfers the sink.
class Logger {
public:
    explicit Logger(std::auto_ptr<Sink> sink);

    void info(const std::string& message);
    void error(const std::string& message);
    int count() const throw() { return count_; }
    bool hasSink() const throw() { return sink_.get() != 0; }

    // Hands the sink back to the caller; the logger stops writing.
    std::auto_ptr<Sink> detach();

private:
    void emit(const char* level, const std::string& message);

    std::auto_ptr<Sink> sink_;
    int count_;
};

std::auto_ptr<Logger> makeMemoryLogger(MemorySink** sinkOut);

}  // namespace toolkit

#endif  // TOOLKIT_LOGGER_H
