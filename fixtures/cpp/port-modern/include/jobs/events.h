#pragma once

#include <functional>
#include <string>
#include <vector>

namespace jobs {

struct Event
{
    std::string topic;
    int job_id = 0;
};

// Calls the handlers subscribed to an event's topic, or to every topic ("*").
class EventBus
{
public:
    using Handler = std::function<void(const Event&)>;

    // Returns the subscription's id, for unsubscribe(). Throws std::invalid_argument for an
    // empty handler.
    int subscribe(const std::string& topic, Handler handler);
    // False when no subscription has the id.
    bool unsubscribe(int id);
    // Calls the matching handlers in the order they subscribed; returns how many.
    int publish(const Event& event) const;

    std::size_t subscriptions() const { return subs_.size(); }

private:
    struct Subscription
    {
        int id;
        std::string topic;
        Handler handler;
    };

    std::vector<Subscription> subs_;
    int next_id_ = 1;
};

} // namespace jobs
