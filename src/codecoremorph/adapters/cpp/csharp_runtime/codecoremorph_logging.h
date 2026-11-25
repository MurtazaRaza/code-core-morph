/* codecoremorph_logging.h: the bridge's side of Microsoft.Extensions.Logging (D-95). Written by
 * CodeCoreMorph; do not edit. The retirement deletes it with the bridge.
 *
 * A C# ILogger or ILoggerFactory crosses as a context (a GCHandle the façades' support makes
 * with CcmLogging.Pass, null for null), which the native side releases when it lets go. The
 * support registers its callbacks once, before the first context crosses. A log entry crosses
 * with its state (D-97): its template (null for a message logged without one) and each hole's
 * name and value, which the support logs as .NET's state; a logged exception crosses as the
 * status whose error the support reads during the call (ccm_error_part, or the C# exception
 * itself for CCM_MANAGED), CCM_OK for none. */
#ifndef CODECOREMORPH_LOGGING_H
#define CODECOREMORPH_LOGGING_H

#include "codecoremorph_bridge.h"

#ifdef __cplusplus
extern "C" {
#endif

/* A log entry's named value: its hole's name, and the value's .NET type (null for null) and
 * invariant data. */
typedef struct ccm_log_value {
    const char *name;
    const char *type;
    const char *data;
} ccm_log_value;

typedef ccm_status (*ccm_log_enabled_fn)(void *logger, int32_t level, bool *enabled);
typedef ccm_status (*ccm_log_fn)(void *logger, int32_t level, int32_t event_id,
                                 const char *event_name, const char *message,
                                 const char *message_template, const ccm_log_value *values,
                                 int32_t count, ccm_status exception);
typedef ccm_status (*ccm_create_logger_fn)(void *factory, const char *category, void **logger);

CCM_EXPORT void ccm_logging_init(ccm_log_enabled_fn enabled, ccm_log_fn log,
                                 ccm_create_logger_fn create, ccm_release_fn release);

#ifdef __cplusplus
}

#include <memory>

#include "codecoremorph_logging.hpp"

namespace ccm::bridge {

/* The C# logger a context holds, as the ported code's ILogger; an empty pointer for null. The
 * native side owns the context from here on: take it first, before anything can throw. */
std::shared_ptr<ccm::logging::ILogger> logger(void *context);

/* The C# factory a context holds, as the ported code's ILoggerFactory; empty for null. */
std::shared_ptr<ccm::logging::ILoggerFactory> logger_factory(void *context);

}  // namespace ccm::bridge
#endif

#endif
