#include <gtest/gtest.h>

#include "mm/core/pipeline.h"

namespace {

using mm::core::Pipeline;

TEST(PipelineTest, RunsStagesInOrder) {
    Pipeline pipeline;
    pipeline.add(new mm::core::TrimStage).add(new mm::core::LowerCaseStage).add(NULL);
    EXPECT_EQ(2, pipeline.size());
    EXPECT_EQ("hello", pipeline.run("  HeLLo  "));
}

TEST(PipelineTest, ParsesSpecs) {
    Pipeline* pipeline = mm::core::parsePipeline("lower, replace:o:0 ,trim");
    ASSERT_TRUE(pipeline != NULL);
    mm::util::StringList names = pipeline->stageNames();
    ASSERT_EQ(3u, names.size());
    EXPECT_EQ("replace", names[1]);
    EXPECT_EQ("f00 b00", pipeline->run(" FOO BOO "));
    delete pipeline;
}

TEST(PipelineTest, RejectsUnknownStages) {
    EXPECT_TRUE(mm::core::parsePipeline("lower,shout") == NULL);
    EXPECT_TRUE(mm::core::parsePipeline("replace:a") == NULL);
}

}  // namespace
