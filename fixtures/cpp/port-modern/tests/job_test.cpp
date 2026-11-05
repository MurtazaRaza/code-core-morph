#include <gtest/gtest.h>

#include <stdexcept>

#include "jobs/ids.h"
#include "jobs/job.h"

TEST(JobTest, MakeJobTakesTheNextId)
{
    jobs::reset_ids();
    auto a = jobs::make_job("build", 2);
    auto b = jobs::make_job("test", 1);
    EXPECT_EQ(1, a->id);
    EXPECT_EQ("build", a->name);
    EXPECT_EQ(2, a->priority);
    EXPECT_EQ(2, b->id);
}

TEST(JobTest, AJobNeedsAName)
{
    try {
        jobs::make_job("", 1);
        FAIL() << "expected std::invalid_argument";
    } catch (const std::invalid_argument& e) {
        EXPECT_STREQ("a job needs a name", e.what());
    }
}

TEST(JobTest, Describe)
{
    jobs::Job job;
    job.id = 3;
    job.name = "deploy";
    job.priority = -1;
    EXPECT_EQ("#3 deploy (priority -1)", jobs::describe(job));
}
