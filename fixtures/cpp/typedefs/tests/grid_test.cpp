#include <gtest/gtest.h>

#include "geo/grid.h"

namespace {

typedef geo::Grid::Coord Coord;

TEST(GridTest, FillsAndReads) {
    geo::Grid grid(2, 3, 7);
    EXPECT_EQ(2u, grid.rows());
    EXPECT_EQ(3u, grid.columns());
    EXPECT_EQ(7, grid.at(1, 2));
    grid.set(0, 1, 4);
    EXPECT_EQ(4, grid.at(0, 1));
    EXPECT_EQ("747\n777\n", grid.render());
}

TEST(GridTest, FindsAndNeighbours) {
    geo::Grid grid(3, 3, 0);
    grid.set(1, 1, 5);
    geo::Grid::CoordList found = grid.find(5);
    ASSERT_EQ(1u, found.size());
    EXPECT_EQ(Coord(1, 1), found[0]);
    EXPECT_EQ(4u, grid.neighbours(Coord(1, 1)).size());
    EXPECT_EQ(2u, grid.neighbours(Coord(0, 0)).size());
}

TEST(GridTest, FloodFills) {
    geo::Grid grid(3, 4, 0);
    grid.set(0, 2, 1);
    grid.set(1, 2, 1);
    grid.set(2, 2, 1);
    EXPECT_EQ(6u, grid.floodFill(Coord(0, 0), 2));
    EXPECT_EQ("2210\n2210\n2210\n", grid.render());
    EXPECT_EQ(0u, grid.floodFill(Coord(0, 0), 2));
}

TEST(GridTest, CellCenters) {
    geo::Grid grid(2, 2, 0);
    geo::Point p = grid.center(Coord(1, 0), 2.0);
    EXPECT_DOUBLE_EQ(1.0, p.x());
    EXPECT_DOUBLE_EQ(3.0, p.y());
}

}  // namespace
