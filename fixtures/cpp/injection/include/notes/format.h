#ifndef NOTES_FORMAT_H
#define NOTES_FORMAT_H

#include <string>

#include "notes/note.h"

namespace notes {

// The banner printed above every exported note. Its text is part of the export format.
extern const char* const kBanner;

std::string formatNote(const Note& note);  // banner, title, body, then "tags: a, b"

}  // namespace notes

#endif  // NOTES_FORMAT_H
