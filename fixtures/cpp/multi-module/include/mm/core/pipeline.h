#ifndef MM_CORE_PIPELINE_H
#define MM_CORE_PIPELINE_H

#include <string>
#include <vector>

#include "mm/util/strings.h"

namespace mm {
namespace core {

// A text transformation step.
class Stage {
public:
    virtual ~Stage() {}
    virtual std::string name() const = 0;
    virtual std::string apply(const std::string& text) const = 0;
};

class LowerCaseStage : public Stage {
public:
    virtual std::string name() const { return "lower"; }
    virtual std::string apply(const std::string& text) const;
};

class TrimStage : public Stage {
public:
    virtual std::string name() const { return "trim"; }
    virtual std::string apply(const std::string& text) const;
};

class ReplaceStage : public Stage {
public:
    ReplaceStage(const std::string& from, const std::string& to) : from_(from), to_(to) {}
    virtual std::string name() const { return "replace"; }
    virtual std::string apply(const std::string& text) const;

private:
    std::string from_;
    std::string to_;
};

// Runs stages in order. Owns them: add() takes ownership.
class Pipeline {
public:
    Pipeline() {}
    ~Pipeline();

    Pipeline& add(Stage* stage);
    std::string run(const std::string& text) const;
    util::StringList stageNames() const;
    int size() const { return static_cast<int>(stages_.size()); }

private:
    Pipeline(const Pipeline&);
    Pipeline& operator=(const Pipeline&);

    std::vector<Stage*> stages_;
};

// Builds a pipeline from "lower,trim,replace:a:b"; NULL if a stage is unknown.
Pipeline* parsePipeline(const std::string& spec);

}  // namespace core
}  // namespace mm

#endif  // MM_CORE_PIPELINE_H
