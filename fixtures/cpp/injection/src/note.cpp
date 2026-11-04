#include "notes/note.h"

#include <algorithm>

namespace notes {

Note::Note(NoteId id, const std::string& title, const std::string& body)
    : id_(id), title_(title), body_(body) {}

void Note::addTag(const std::string& tag) {
    if (!hasTag(tag)) {
        tags_.push_back(tag);
    }
}

bool Note::hasTag(const std::string& tag) const {
    for (TagList::const_iterator it = tags_.begin(); it != tags_.end(); ++it) {
        if (*it == tag) {
            return true;
        }
    }
    return false;
}

}  // namespace notes
