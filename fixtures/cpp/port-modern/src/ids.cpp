#include "jobs/ids.h"

#include <mutex>

namespace jobs {

namespace {

std::mutex& ids_mutex()
{
    static std::mutex mutex;
    return mutex;
}

int next = 1;

} // namespace

int next_id()
{
    std::lock_guard<std::mutex> lock(ids_mutex());
    return next++;
}

void reset_ids()
{
    std::lock_guard<std::mutex> lock(ids_mutex());
    next = 1;
}

} // namespace jobs
