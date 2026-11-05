#pragma once

#include <memory>
#include <string>

namespace jobs {

struct Job
{
    int id = 0;
    std::string name;
    int priority = 0; // higher runs first
};

// A new job with the next id. Throws std::invalid_argument for an empty name.
std::unique_ptr<Job> make_job(const std::string& name, int priority);

// "#3 build (priority 2)".
std::string describe(const Job& job);

} // namespace jobs
