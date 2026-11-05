#include "jobs/events.h"

#include <algorithm>
#include <stdexcept>
#include <utility>

namespace jobs {

int EventBus::subscribe(const std::string& topic, Handler handler)
{
    if (!handler)
        throw std::invalid_argument("an empty handler");
    int id = next_id_++;
    subs_.push_back(Subscription{id, topic, std::move(handler)});
    return id;
}

bool EventBus::unsubscribe(int id)
{
    auto at = std::find_if(subs_.begin(), subs_.end(),
                           [id](const Subscription& s) { return s.id == id; });
    if (at == subs_.end())
        return false;
    subs_.erase(at);
    return true;
}

int EventBus::publish(const Event& event) const
{
    int called = 0;
    for (const auto& s : subs_) {
        if (s.topic == "*" || s.topic == event.topic) {
            s.handler(event);
            ++called;
        }
    }
    return called;
}

} // namespace jobs
