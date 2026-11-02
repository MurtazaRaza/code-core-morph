#include "pkgmeta/render.h"

#include <algorithm>

namespace pkgmeta {

namespace {

const char* branch(bool last)
{
    return last ? u8"└─ " : u8"├─ ";
}

} // namespace

std::string render_tree(const Manifest& manifest)
{
    std::string out = manifest.name + " " + manifest.version.to_string() + "\n";
    for (std::size_t i = 0; i < manifest.requires.size(); ++i) {
        const Dependency& dependency = manifest.requires[i];
        out += branch(i + 1 == manifest.requires.size());
        out += dependency.name + " " + dependency.constraint + "\n";
    }
    return out;
}

void Report::add_all(const std::vector<std::string>& lines)
{
    std::for_each(lines.begin(), lines.end(),
                  [=](const std::string& line) { lines_.push_back(prefix_ + line); });
}

bool Report::append_line(std::string& out, const std::string& line)
{
    if (line.empty())
        return false;
    out += line;
    out += '\n';
    return true;
}

std::string Report::text() const
{
    std::string out;
    for (const std::string& line : lines_)
        append_line(out, line);
    return out;
}

} // namespace pkgmeta
