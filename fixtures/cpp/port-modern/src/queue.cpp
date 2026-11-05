#include "jobs/queue.h"

#include <algorithm>

namespace jobs {

void JobQueue::push(std::unique_ptr<Job> job)
{
    if (!job)
        return;
    // After every job of the same or a higher priority: stable among equals.
    auto at = std::find_if(jobs_.begin(), jobs_.end(), [&](const std::unique_ptr<Job>& queued) {
        return queued->priority < job->priority;
    });
    jobs_.insert(at, std::move(job));
}

std::unique_ptr<Job> JobQueue::pop()
{
    if (jobs_.empty())
        return nullptr;
    std::unique_ptr<Job> job = std::move(jobs_.front());
    jobs_.erase(jobs_.begin());
    return job;
}

std::vector<std::string> JobQueue::names() const
{
    std::vector<std::string> out;
    out.reserve(jobs_.size());
    for (const auto& job : jobs_)
        out.push_back(job->name);
    return out;
}

} // namespace jobs
