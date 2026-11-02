typedef unsigned int Id;
typedef std::map<Id, std::string> NameTable;
typedef void (*Callback)(Id, void*);

template <typename T>
struct Holder {
    typedef typename T::value_type value_type;
};
