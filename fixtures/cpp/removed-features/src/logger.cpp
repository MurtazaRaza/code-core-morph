#include "toolkit/logger.h"

namespace toolkit {

Logger::Logger(std::auto_ptr<Sink> sink) : sink_(sink), count_(0) {}

void Logger::emit(const char* level, const std::string& message) {
    ++count_;
    if (sink_.get() != 0) {
        sink_->write(std::string(level) + ": " + message);
    }
}

void Logger::info(const std::string& message) { emit("info", message); }

void Logger::error(const std::string& message) { emit("error", message); }

std::auto_ptr<Sink> Logger::detach() { return sink_; }

std::auto_ptr<Logger> makeMemoryLogger(MemorySink** sinkOut) {
    MemorySink* sink = new MemorySink;
    if (sinkOut != 0) {
        *sinkOut = sink;
    }
    std::auto_ptr<Logger> logger(new Logger(std::auto_ptr<Sink>(sink)));
    return logger;
}

}  // namespace toolkit
