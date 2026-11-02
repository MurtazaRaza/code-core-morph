#include <gtest/gtest.h>

#include "geo/registry.h"

namespace {

struct Recorder {
    typedef std::vector<geo::Id> Ids;
    Ids ids;
};

void record(geo::Id id, const std::string&, void* context) {
    static_cast<Recorder*>(context)->ids.push_back(id);
}

TEST(RegistryTest, AddsAndFinds) {
    geo::Registry registry;
    geo::Id a = registry.add("alpha");
    geo::Id b = registry.add("beta");
    EXPECT_NE(a, b);
    EXPECT_EQ(a, registry.add("alpha"));
    EXPECT_EQ(2u, registry.size());
    EXPECT_EQ("beta", registry.nameOf(b));
    EXPECT_EQ(b, registry.find("beta"));
    EXPECT_EQ(geo::Registry::kInvalid, registry.find("gamma"));
}

TEST(RegistryTest, Removes) {
    geo::Registry registry;
    geo::Id a = registry.add("alpha");
    EXPECT_TRUE(registry.remove(a));
    EXPECT_FALSE(registry.remove(a));
    EXPECT_FALSE(registry.contains(a));
    EXPECT_EQ("", registry.nameOf(a));
}

TEST(RegistryTest, NotifiesListeners) {
    geo::Registry registry;
    Recorder recorder;
    registry.subscribe(record, &recorder);
    geo::Id a = registry.add("alpha");
    registry.add("alpha");
    geo::Id b = registry.add("beta");
    ASSERT_EQ(2u, recorder.ids.size());
    EXPECT_EQ(a, recorder.ids[0]);
    EXPECT_EQ(b, recorder.ids[1]);
}

TEST(RegistryTest, IteratesInIdOrder) {
    geo::Registry registry;
    registry.add("one");
    registry.add("two");
    std::string joined;
    for (geo::Registry::const_iterator it = registry.begin(); it != registry.end(); ++it) {
        joined += it->second;
    }
    EXPECT_EQ("onetwo", joined);
}

}  // namespace
