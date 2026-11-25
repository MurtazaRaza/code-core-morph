// codecoremorph_logging.hpp: Microsoft.Extensions.Logging.Abstractions for a C++ port of C# code
// (CodeCoreMorph, D-95). Written by CodeCoreMorph; do not edit.
//
// As in .NET, a logger is an object the code is given: ILogger, which says whether a level is
// enabled and logs a message, and ILoggerFactory, which makes one per category. NullLogger logs
// nothing. log_debug and its kin are LoggerExtensions' LogDebug and kin: they fill the message
// template's holes ("Stored {Key}") as .NET's formatter does, the n-th hole taking the n-th
// argument whatever its name, and call log, whether or not the level is enabled (the logger
// decides, as in .NET); LogError(exception, ...) and kin pass the exception first. A hole's
// alignment ({Count,5}) pads; a format ({Count:X4}) is refused. A logger gets the entry's state
// too (log_state, D-97): the template and each hole's name and value, as .NET's structured
// loggers do; a value is a ccm::DotnetValue (codecoremorph_exceptions.hpp), so a duration is a
// TimeSpan and a project's own type logs through its dotnet_value. The retirement keeps this
// file.
#ifndef CODECOREMORPH_LOGGING_HPP
#define CODECOREMORPH_LOGGING_HPP

#include <charconv>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <exception>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <type_traits>
#include <utility>
#include <vector>

#include "codecoremorph_exceptions.hpp"

namespace ccm::logging {

enum class LogLevel : std::int32_t {
    Trace = 0,
    Debug = 1,
    Information = 2,
    Warning = 3,
    Error = 4,
    Critical = 5,
    None = 6,
};

// .NET's EventId: a number and an optional name.
struct EventId {
    std::int32_t id = 0;
    std::optional<std::string> name;
};

// A log entry's state, as LoggerExtensions makes it (FormattedLogValues, D-97): the message (the
// template with its holes filled), the template, and each hole's name with its argument's
// value, in order. A C# logger gets them as .NET's state: the names and values, then
// "{OriginalFormat}" and the template.
struct LogState {
    std::string message;
    std::string message_template;
    std::vector<std::pair<std::string, DotnetValue>> values;
};

class ILogger {
public:
    ILogger() = default;
    ILogger(const ILogger&) = default;
    ILogger& operator=(const ILogger&) = default;
    ILogger(ILogger&&) = default;
    ILogger& operator=(ILogger&&) = default;
    virtual ~ILogger() = default;
    virtual bool is_enabled(LogLevel level) const = 0;
    // The message is the template with its holes filled; exception is the one the C# passed
    // (LogError(exception, ...)), or null.
    virtual void log(LogLevel level, const EventId& event, const std::string& message,
                     const std::exception* exception) = 0;
    // An entry with its state, which log_debug and its kin make: by default, log with its
    // message. The C# logger behind a façade gets the state.
    virtual void log_state(LogLevel level, const EventId& event, const LogState& state,
                           const std::exception* exception) {
        log(level, event, state.message, exception);
    }
};

class ILoggerFactory {
public:
    ILoggerFactory() = default;
    ILoggerFactory(const ILoggerFactory&) = default;
    ILoggerFactory& operator=(const ILoggerFactory&) = default;
    ILoggerFactory(ILoggerFactory&&) = default;
    ILoggerFactory& operator=(ILoggerFactory&&) = default;
    virtual ~ILoggerFactory() = default;
    virtual std::shared_ptr<ILogger> create_logger(const std::string& category) = 0;
};

// NullLogger.Instance: enabled for nothing, logs nothing.
class NullLogger final : public ILogger {
public:
    bool is_enabled(LogLevel /*level*/) const override { return false; }
    void log(LogLevel /*level*/, const EventId& /*event*/, const std::string& /*message*/,
             const std::exception* /*exception*/) override {}
    static std::shared_ptr<ILogger> instance() {
        static const std::shared_ptr<ILogger> logger = std::make_shared<NullLogger>();
        return logger;
    }
};

namespace detail {

// A value as .NET's invariant culture writes it in a message: null is "(null)".
inline std::string text(std::string_view value) { return std::string(value); }
inline std::string text(const std::string& value) { return value; }
inline std::string text(const char* value) { return value == nullptr ? "(null)" : value; }
inline std::string text(bool value) { return value ? "True" : "False"; }
inline std::string text(char value) { return std::string(1, value); }
inline std::string text(std::nullptr_t /*value*/) { return "(null)"; }

template <class T, std::enable_if_t<std::is_integral_v<T> && !std::is_same_v<T, bool> &&
                                        !std::is_same_v<T, char>,
                                    int> = 0>
std::string text(T value) {
    return std::to_string(value);
}

// The shortest text that reads back as the same number, as .NET Core writes it.
inline std::string text(double value) { return floating_text(value); }
inline std::string text(float value) { return floating_text(value); }

// A duration is a TimeSpan: [-][d.]hh:mm:ss[.fffffff].
template <class Rep, class Period>
std::string text(std::chrono::duration<Rep, Period> value) {
    return dotnet_value(value).text;
}

template <class T>
std::string text(const std::optional<T>& value) {
    return value ? text(*value) : "(null)";
}

// A sequence is its items, comma-separated, as .NET writes an IEnumerable argument.
template <class T>
std::string text(const std::vector<T>& values) {
    std::string out;
    for (std::size_t i = 0; i < values.size(); ++i) {
        if (i != 0) out += ", ";
        out += text(values[i]);
    }
    return out;
}

template <class T, class = void>
struct has_text : std::false_type {};
template <class T>
struct has_text<T, std::void_t<decltype(text(std::declval<const T&>()))>> : std::true_type {};

// A hole's text: a type of the project's (an enum) shows its dotnet_value's text.
template <class T>
std::string hole_text(const T& value) {
    if constexpr (has_text<T>::value) {
        return text(value);
    } else {
        DotnetValue made = dotnet_value(value);
        return made.is_null() ? "(null)" : made.shown();
    }
}

// What the state keeps of an argument: its DotnetValue; a sequence, which .NET keeps as the
// object, as its text.
template <class T>
DotnetValue hole_value(const T& value) {
    return dotnet_value(value);
}
template <class T>
DotnetValue hole_value(const std::vector<T>& values) {
    return {"System.String", text(values), {}};
}

inline std::string aligned(std::string value, std::string_view alignment) {
    if (alignment.empty()) return value;
    bool left = alignment.front() == '-';
    std::string_view digits = left ? alignment.substr(1) : alignment;
    std::size_t width = 0;
    auto parsed = std::from_chars(digits.data(), digits.data() + digits.size(), width);
    if (parsed.ec != std::errc() || parsed.ptr != digits.data() + digits.size()) {
        throw std::invalid_argument("a message template hole's alignment isn't a number");
    }
    if (value.size() >= width) return value;
    std::string pad(width - value.size(), ' ');
    return left ? value + pad : pad + value;
}

}  // namespace detail

// The state .NET's LoggerExtensions makes of a template and its arguments: the message its
// formatter writes, and each hole's name and value (a hole without an argument throws, an
// argument without a hole is left out, as in .NET).
template <class... A>
LogState state(std::string_view message_template, const A&... args) {
    LogState out{std::string(), std::string(message_template), {}};
    // Without arguments .NET logs the template as it is, holes and doubled braces too.
    if constexpr (sizeof...(A) == 0) {
        out.message = std::string(message_template);
        return out;
    } else {
        const std::vector<std::string> texts{detail::hole_text(args)...};
        std::vector<DotnetValue> values{detail::hole_value(args)...};
        std::string& message = out.message;
        std::size_t next = 0;
        for (std::size_t i = 0; i < message_template.size(); ++i) {
            char c = message_template[i];
            if ((c == '{' || c == '}') && i + 1 < message_template.size() &&
                message_template[i + 1] == c) {
                message += c;
                ++i;
                continue;
            }
            if (c != '{') {
                message += c;
                continue;
            }
            std::size_t close = message_template.find('}', i);
            if (close == std::string_view::npos) {
                message += message_template.substr(i);
                break;
            }
            std::string_view hole = message_template.substr(i + 1, close - i - 1);
            std::size_t colon = hole.find(':');
            if (colon != std::string_view::npos) {
                throw std::invalid_argument("a message template hole's format isn't supported");
            }
            std::size_t comma = hole.find(',');
            std::string_view alignment =
                comma == std::string_view::npos ? std::string_view() : hole.substr(comma + 1);
            if (next >= texts.size()) {
                throw std::invalid_argument("a message template has more holes than arguments");
            }
            message += detail::aligned(texts[next], alignment);
            out.values.emplace_back(std::string(hole.substr(0, comma)), std::move(values[next]));
            ++next;
            i = close;
        }
        return out;
    }
}

// The message .NET's formatter makes of a template and its arguments.
template <class... A>
std::string format(std::string_view message_template, const A&... args) {
    return state(message_template, args...).message;
}

// LoggerExtensions.Log(logger, level, event, exception, template, args): exception may be null.
template <class... A>
void log(ILogger& logger, LogLevel level, const EventId& event, const std::exception* exception,
         std::string_view message_template, const A&... args) {
    logger.log_state(level, event, state(message_template, args...), exception);
}

// Each level's four forms, as LoggerExtensions has them: with an event, an exception, both, or
// neither.
#define CCM_LOGGING_LEVEL(name, level)                                                        \
    template <class... A>                                                                     \
    void name(ILogger& logger, const EventId& event, const std::exception& exception,        \
              std::string_view message_template, const A&... args) {                          \
        ::ccm::logging::log(logger, LogLevel::level, event, &exception, message_template,     \
                            args...);                                                         \
    }                                                                                         \
    template <class... A>                                                                     \
    void name(ILogger& logger, const std::exception& exception,                               \
              std::string_view message_template, const A&... args) {                          \
        ::ccm::logging::log(logger, LogLevel::level, EventId{}, &exception, message_template, \
                            args...);                                                         \
    }                                                                                         \
    template <class... A>                                                                     \
    void name(ILogger& logger, const EventId& event, std::string_view message_template,      \
              const A&... args) {                                                             \
        ::ccm::logging::log(logger, LogLevel::level, event, nullptr, message_template,        \
                            args...);                                                         \
    }                                                                                         \
    template <class... A>                                                                     \
    void name(ILogger& logger, std::string_view message_template, const A&... args) {        \
        ::ccm::logging::log(logger, LogLevel::level, EventId{}, nullptr, message_template,    \
                            args...);                                                         \
    }

CCM_LOGGING_LEVEL(log_trace, Trace)
CCM_LOGGING_LEVEL(log_debug, Debug)
CCM_LOGGING_LEVEL(log_information, Information)
CCM_LOGGING_LEVEL(log_warning, Warning)
CCM_LOGGING_LEVEL(log_error, Error)
CCM_LOGGING_LEVEL(log_critical, Critical)

#undef CCM_LOGGING_LEVEL

}  // namespace ccm::logging

#endif
