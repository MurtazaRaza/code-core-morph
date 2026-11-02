#include "geo/grid.h"

#include <sstream>

namespace geo {

namespace {
typedef int Offset;
}  // namespace

Grid::Grid(Index rows, Index columns, Cell fill) : cells_(rows, Row(columns, fill)) {}

Grid::Cell Grid::at(Index row, Index column) const { return cells_.at(row).at(column); }

void Grid::set(Index row, Index column, Cell value) { cells_.at(row).at(column) = value; }

Grid::CoordList Grid::find(Cell value) const {
    CoordList out;
    for (Index r = 0; r < rows(); ++r) {
        for (Index c = 0; c < columns(); ++c) {
            if (cells_[r][c] == value) {
                out.push_back(Coord(r, c));
            }
        }
    }
    return out;
}

Grid::CoordList Grid::neighbours(const Coord& coord) const {
    static const Offset kDeltas[4][2] = {{-1, 0}, {1, 0}, {0, -1}, {0, 1}};
    CoordList out;
    for (int i = 0; i < 4; ++i) {
        long r = static_cast<long>(coord.first) + kDeltas[i][0];
        long c = static_cast<long>(coord.second) + kDeltas[i][1];
        if (r >= 0 && c >= 0 && static_cast<Index>(r) < rows() &&
            static_cast<Index>(c) < columns()) {
            out.push_back(Coord(static_cast<Index>(r), static_cast<Index>(c)));
        }
    }
    return out;
}

Index Grid::floodFill(const Coord& start, Cell replacement) {
    Cell target = at(start.first, start.second);
    if (target == replacement) {
        return 0;
    }
    Index changed = 0;
    CoordList stack(1, start);
    while (!stack.empty()) {
        Coord current = stack.back();
        stack.pop_back();
        if (cells_[current.first][current.second] != target) {
            continue;
        }
        cells_[current.first][current.second] = replacement;
        ++changed;
        CoordList next = neighbours(current);
        stack.insert(stack.end(), next.begin(), next.end());
    }
    return changed;
}

Point Grid::center(const Coord& coord, Scalar cellSize) const {
    return Point((static_cast<Scalar>(coord.second) + 0.5) * cellSize,
                 (static_cast<Scalar>(coord.first) + 0.5) * cellSize);
}

std::string Grid::render() const {
    std::ostringstream out;
    for (Rows::const_iterator row = cells_.begin(); row != cells_.end(); ++row) {
        for (Row::const_iterator cell = row->begin(); cell != row->end(); ++cell) {
            out << *cell;
        }
        out << '\n';
    }
    return out.str();
}

}  // namespace geo
