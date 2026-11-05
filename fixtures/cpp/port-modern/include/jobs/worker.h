#pragma once

#include <mutex>

#include "jobs/events.h"
#include "jobs/queue.h"

namespace jobs {

// Runs jobs on a thread of its own.
class Worker
{
public:
    explicit Worker(const EventBus& bus) : bus_(bus) {}

    // Drains queue on a new thread, in pop order, publishing {"done", id} for each job, and
    // waits for the thread. Returns how many jobs ran.
    int run(JobQueue& queue);
    // Jobs run over every call of run().
    int total() const;

private:
    const EventBus& bus_;
    mutable std::mutex mutex_;
    int total_ = 0;
};

} // namespace jobs
