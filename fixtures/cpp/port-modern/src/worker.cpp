#include "jobs/worker.h"

#include <thread>

namespace jobs {

int Worker::run(JobQueue& queue)
{
    int ran = 0;
    std::thread thread([&] {
        while (std::unique_ptr<Job> job = queue.pop()) {
            bus_.publish(Event{"done", job->id});
            ++ran;
            std::lock_guard<std::mutex> lock(mutex_);
            ++total_;
        }
    });
    thread.join();
    return ran;
}

int Worker::total() const
{
    std::lock_guard<std::mutex> lock(mutex_);
    return total_;
}

} // namespace jobs
