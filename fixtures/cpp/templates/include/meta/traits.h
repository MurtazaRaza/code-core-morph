// C++11 type traits written the old way: SFINAE and the detection idiom.
#ifndef META_TRAITS_H
#define META_TRAITS_H

#include <cstddef>
#include <string>
#include <type_traits>
#include <utility>

namespace meta {

// void_t is C++17; this is the C++11 workaround.
template <typename...>
struct make_void {
    typedef void type;
};

// has_size<T>: does T have a size() member?
template <typename T, typename = void>
struct has_size : std::false_type {};

template <typename T>
struct has_size<T, typename make_void<decltype(std::declval<const T&>().size())>::type>
    : std::true_type {};

// is_iterable<T>: does T have begin() and end()?
template <typename T, typename = void>
struct is_iterable : std::false_type {};

template <typename T>
struct is_iterable<T, typename make_void<decltype(std::declval<const T&>().begin()),
                                         decltype(std::declval<const T&>().end())>::type>
    : std::true_type {};

// is_string<T>: std::string and C strings.
template <typename T>
struct is_string
    : std::integral_constant<bool,
                             std::is_same<typename std::decay<T>::type, std::string>::value ||
                                 std::is_same<typename std::decay<T>::type, const char*>::value ||
                                 std::is_same<typename std::decay<T>::type, char*>::value> {};

// is_container<T>: iterable, has size(), and is not a string.
template <typename T>
struct is_container
    : std::integral_constant<bool, is_iterable<T>::value && has_size<T>::value &&
                                       !is_string<T>::value> {};

// is_pair<T>
template <typename T>
struct is_pair : std::false_type {};

template <typename A, typename B>
struct is_pair<std::pair<A, B> > : std::true_type {};

// enable_if shortcuts, C++11 style (std::enable_if_t is C++14).
template <bool B, typename T = void>
struct enable_if_c {
    typedef typename std::enable_if<B, T>::type type;
};

}  // namespace meta

#endif  // META_TRAITS_H
