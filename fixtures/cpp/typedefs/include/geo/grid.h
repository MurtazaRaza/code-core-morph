#ifndef GEO_GRID_H
#define GEO_GRID_H

#include <string>
#include <vector>

#include "geo/types.h"
#include "geo/vector.h"

namespace geo {

// A dense 2-D grid of cells, addressed by (row, column).
class Grid {
public:
    typedef int Cell;
    typedef std::vector<Cell> Row;
    typedef std::vector<Row> Rows;
    typedef std::pair<Index, Index> Coord;
    typedef std::vector<Coord> CoordList;

    Grid(Index rows, Index columns, Cell fill);

    Index rows() const { return cells_.size(); }
    Index columns() const { return cells_.empty() ? 0 : cells_[0].size(); }

    Cell at(Index row, Index column) const;
    void set(Index row, Index column, Cell value);

    CoordList find(Cell value) const;
    CoordList neighbours(const Coord& coord) const;
    Index floodFill(const Coord& start, Cell replacement);
    Point center(const Coord& coord, Scalar cellSize) const;
    std::string render() const;

private:
    Rows cells_;
};

}  // namespace geo

#endif  // GEO_GRID_H
