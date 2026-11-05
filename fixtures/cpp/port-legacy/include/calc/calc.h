#ifndef CALC_CALC_H
#define CALC_CALC_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* The status codes of the C interface. */
enum calc_status
{
    CALC_OK = 0,
    CALC_SYNTAX_ERROR = 1,
    CALC_DIVISION_BY_ZERO = 2,
    CALC_BUFFER_TOO_SMALL = 3,
    CALC_NO_RESULT = 4
};

/* A session keeps the results of earlier evaluations; "ans" is the last one (0 at first). */
typedef struct calc_session calc_session;

calc_session* calc_open(void);
void calc_close(calc_session* session);

/* Evaluates text and writes the result ("7/2") to out. On success the result becomes "ans";
   on CALC_SYNTAX_ERROR, *error_pos (unless error_pos is NULL) is the error's byte offset. */
int calc_eval(calc_session* session, const char* text, char* out, size_t out_size,
              size_t* error_pos);

/* Forgets the last result, so "ans" is the one before it again. */
int calc_undo(calc_session* session);

/* The number of results kept. */
size_t calc_history_size(const calc_session* session);

/* "ok", "syntax error", and so on; never NULL. */
const char* calc_status_text(int status);

#ifdef __cplusplus
}
#endif

#endif
