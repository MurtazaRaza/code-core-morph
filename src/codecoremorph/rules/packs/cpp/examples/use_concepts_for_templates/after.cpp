template <std::integral T>
T gcd(T a, T b) { return b == 0 ? a : gcd(b, a % b); }

template <std::floating_point T>
T lerp(T a, T b, T t) { return a + (b - a) * t; }
