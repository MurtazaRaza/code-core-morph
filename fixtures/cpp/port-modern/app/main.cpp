// jobrun: queues one job per "name:priority" argument, runs them on a worker and prints each as
// it finishes.
#include <iostream>
#include <map>
#include <string>

#include "jobs/events.h"
#include "jobs/job.h"
#include "jobs/queue.h"
#include "jobs/worker.h"

int main(int argc, char** argv)
{
    jobs::JobQueue queue;
    std::map<int, std::string> described;
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        auto colon = arg.find(':');
        int priority = colon == std::string::npos ? 0 : std::stoi(arg.substr(colon + 1));
        auto job = jobs::make_job(arg.substr(0, colon), priority);
        described[job->id] = jobs::describe(*job);
        queue.push(std::move(job));
    }
    jobs::EventBus bus;
    bus.subscribe("done", [&](const jobs::Event& e) { std::cout << described[e.job_id] << '\n'; });
    jobs::Worker worker(bus);
    int ran = worker.run(queue);
    std::cout << ran << " jobs\n";
    return 0;
}
