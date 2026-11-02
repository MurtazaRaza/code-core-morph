#ifndef GEO_STATS_H
#define GEO_STATS_H

#include <algorithm>
#include <cmath>
#include <iterator>
#include <vector>

#include "geo/types.h"

namespace geo {

// Summary statistics over any container of numbers.
template <typename Container>
struct Summary {
    typedef typename Container::value_type value_type;
    typedef typename Container::size_type size_type;

    value_type minimum;
    value_type maximum;
    double mean;
    double stddev;
    size_type count;
};

template <typename Container>
Summary<Container> summarize(const Container& values) {
    Summary<Container> s;
    s.count = values.size();
    s.minimum = typename Container::value_type();
    s.maximum = typename Container::value_type();
    s.mean = 0.0;
    s.stddev = 0.0;
    if (values.empty()) {
        return s;
    }
    s.minimum = *std::min_element(values.begin(), values.end());
    s.maximum = *std::max_element(values.begin(), values.end());
    double sum = 0.0;
    for (typename Container::const_iterator it = values.begin(); it != values.end(); ++it) {
        sum += static_cast<double>(*it);
    }
    s.mean = sum / static_cast<double>(s.count);
    double squares = 0.0;
    for (typename Container::const_iterator it = values.begin(); it != values.end(); ++it) {
        double d = static_cast<double>(*it) - s.mean;
        squares += d * d;
    }
    s.stddev = std::sqrt(squares / static_cast<double>(s.count));
    return s;
}

// A fixed-size ring buffer for running statistics.
template <typename T, std::size_t N>
class Window {
public:
    typedef T value_type;
    typedef std::vector<T> Storage;
    typedef typename Storage::size_type size_type;

    Window() : next_(0), filled_(0) { data_.resize(N); }

    void push(const value_type& value) {
        data_[next_] = value;
        next_ = (next_ + 1) % N;
        if (filled_ < N) {
            ++filled_;
        }
    }

    size_type size() const { return filled_; }
    bool full() const { return filled_ == N; }

    Storage values() const {
        Storage out;
        for (size_type i = 0; i < filled_; ++i) {
            out.push_back(data_[(next_ + N - filled_ + i) % N]);
        }
        return out;
    }

private:
    Storage data_;
    size_type next_;
    size_type filled_;
};

ScalarList sorted(const ScalarList& values, ScalarLess less);
Scalar median(const ScalarList& values);
void visitAll(const ScalarList& values, ScalarVisitor* visitor, void* context);

}  // namespace geo

#endif  // GEO_STATS_H
