/* A singly linked list of strings, with a callback-based iterator. Valid C. */
#ifndef CCORE_STRLIST_H
#define CCORE_STRLIST_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct ccore_node {
    char* text;
    struct ccore_node* next;
} ccore_node;

typedef struct ccore_list {
    ccore_node* head;
    ccore_node* tail;
    size_t length;
} ccore_list;

typedef void (*ccore_visit_fn)(const char* text, void* context);

void ccore_list_init(ccore_list* list);
void ccore_list_free(ccore_list* list);
int ccore_list_push(ccore_list* list, const char* text);
int ccore_list_remove(ccore_list* list, const char* text);
void ccore_list_visit(const ccore_list* list, ccore_visit_fn visit, void* context);
char* ccore_list_join(const ccore_list* list, const char* separator); /* caller frees */

#ifdef __cplusplus
}
#endif

#endif /* CCORE_STRLIST_H */
