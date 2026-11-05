#include "jobs/job.h"

#include <stdexcept>

#include "jobs/ids.h"

namespace jobs {

std::unique_ptr<Job> make_job(const std::string& name, int priority)
{
    if (name.empty())
        throw std::invalid_argument("a job needs a name");
    auto job = std::make_unique<Job>();
    job->id = next_id();
    job->name = name;
    job->priority = priority;
    return job;
}

std::string describe(const Job& job)
{
    return "#" + std::to_string(job.id) + " " + job.name + " (priority " +
           std::to_string(job.priority) + ")";
}

} // namespace jobs
