// Basic scalar and handle types shared by the geometry library.
// Legacy C++11 code: typedefs everywhere.
#ifndef GEO_TYPES_H
#define GEO_TYPES_H

#include <cstddef>
#include <map>
#include <string>
#include <vector>

namespace geo {

typedef double Scalar;
typedef unsigned int Id;
typedef std::size_t Index;

typedef std::vector<Scalar> ScalarList;
typedef std::map<std::string, Id> NameIndex;

// Comparison callback used by the sorting helpers.
typedef bool (*ScalarLess)(Scalar, Scalar);

// A function type (not a pointer): used to declare visitors.
typedef void ScalarVisitor(Scalar value, void* context);

inline bool ascending(Scalar a, Scalar b) { return a < b; }
inline bool descending(Scalar a, Scalar b) { return a > b; }

}  // namespace geo

#endif  // GEO_TYPES_H
