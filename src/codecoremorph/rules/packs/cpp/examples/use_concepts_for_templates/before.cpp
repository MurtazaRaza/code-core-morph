template <typename T>
typename std::enable_if<std::is_integral<T>::value, T>::type
gcd(T a, T b) { return b == 0 ? a : gcd(b, a % b); }

template <typename T, typename = typename std::enable_if<std::is_floating_point<T>::value>::type>
T lerp(T a, T b, T t) { return a + (b - a) * t; }
