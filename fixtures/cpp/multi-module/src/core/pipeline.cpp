#include "mm/core/pipeline.h"

namespace mm {
namespace core {

std::string LowerCaseStage::apply(const std::string& text) const { return util::toLower(text); }

std::string TrimStage::apply(const std::string& text) const { return util::trim(text); }

std::string ReplaceStage::apply(const std::string& text) const {
    return util::replaceAll(text, from_, to_);
}

Pipeline::~Pipeline() {
    for (std::vector<Stage*>::size_type i = 0; i < stages_.size(); ++i) {
        delete stages_[i];
    }
}

Pipeline& Pipeline::add(Stage* stage) {
    if (stage != NULL) {
        stages_.push_back(stage);
    }
    return *this;
}

std::string Pipeline::run(const std::string& text) const {
    std::string out = text;
    for (std::vector<Stage*>::const_iterator it = stages_.begin(); it != stages_.end(); ++it) {
        out = (*it)->apply(out);
    }
    return out;
}

util::StringList Pipeline::stageNames() const {
    util::StringList names;
    for (std::vector<Stage*>::size_type i = 0; i < stages_.size(); ++i) {
        names.push_back(stages_[i]->name());
    }
    return names;
}

Pipeline* parsePipeline(const std::string& spec) {
    Pipeline* pipeline = new Pipeline;
    util::StringList parts = util::split(spec, ',');
    for (util::StringList::const_iterator it = parts.begin(); it != parts.end(); ++it) {
        std::string part = util::trim(*it);
        if (part == "lower") {
            pipeline->add(new LowerCaseStage);
        } else if (part == "trim") {
            pipeline->add(new TrimStage);
        } else if (util::startsWith(part, "replace:")) {
            util::StringList args = util::split(part, ':');
            if (args.size() != 3) {
                delete pipeline;
                return NULL;
            }
            pipeline->add(new ReplaceStage(args[1], args[2]));
        } else {
            delete pipeline;
            return NULL;
        }
    }
    return pipeline;
}

}  // namespace core
}  // namespace mm
