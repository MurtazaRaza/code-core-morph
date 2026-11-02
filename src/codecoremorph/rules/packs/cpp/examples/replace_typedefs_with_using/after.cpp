using Id = unsigned int;
using NameTable = std::map<Id, std::string>;
using Callback = void (*)(Id, void*);

template <typename T>
struct Holder {
    using value_type = typename T::value_type;
};
