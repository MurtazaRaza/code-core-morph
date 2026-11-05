#pragma once

#include <cstddef>
#include <memory>
#include <string>
#include <vector>

#include "jobs/job.h"

namespace jobs {

// Jobs waiting to run: the highest priority first, and among equal priorities the first pushed.
class JobQueue
{
public:
    // Takes the job; a null one is ignored.
    void push(std::unique_ptr<Job> job);
    // The next job, or null when the queue is empty.
    std::unique_ptr<Job> pop();

    std::size_t size() const { return jobs_.size(); }
    bool empty() const { return jobs_.empty(); }
    // The names in the order pop() would return the jobs.
    std::vector<std::string> names() const;

private:
    std::vector<std::unique_ptr<Job>> jobs_; // in pop order
};

} // namespace jobs
