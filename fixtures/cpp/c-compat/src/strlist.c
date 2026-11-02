#include "ccore/strlist.h"

#include <stdlib.h>
#include <string.h>

void ccore_list_init(ccore_list* list) {
    list->head = NULL;
    list->tail = NULL;
    list->length = 0;
}

void ccore_list_free(ccore_list* list) {
    ccore_node* node = list->head;
    while (node != NULL) {
        ccore_node* next = node->next;
        free(node->text);
        free(node);
        node = next;
    }
    ccore_list_init(list);
}

int ccore_list_push(ccore_list* list, const char* text) {
    size_t length = strlen(text);
    ccore_node* node = (ccore_node*)malloc(sizeof(ccore_node));
    if (node == NULL) {
        return 0;
    }
    node->text = (char*)malloc(length + 1);
    if (node->text == NULL) {
        free(node);
        return 0;
    }
    memcpy(node->text, text, length + 1);
    node->next = NULL;
    if (list->tail != NULL) {
        list->tail->next = node;
    } else {
        list->head = node;
    }
    list->tail = node;
    list->length++;
    return 1;
}

int ccore_list_remove(ccore_list* list, const char* text) {
    ccore_node* previous = NULL;
    ccore_node* node = list->head;
    while (node != NULL) {
        if (strcmp(node->text, text) == 0) {
            if (previous != NULL) {
                previous->next = node->next;
            } else {
                list->head = node->next;
            }
            if (list->tail == node) {
                list->tail = previous;
            }
            free(node->text);
            free(node);
            list->length--;
            return 1;
        }
        previous = node;
        node = node->next;
    }
    return 0;
}

void ccore_list_visit(const ccore_list* list, ccore_visit_fn visit, void* context) {
    const ccore_node* node;
    for (node = list->head; node != NULL; node = node->next) {
        visit(node->text, context);
    }
}

char* ccore_list_join(const ccore_list* list, const char* separator) {
    size_t total = 1;
    size_t sep = strlen(separator);
    const ccore_node* node;
    char* out;
    char* cursor;
    for (node = list->head; node != NULL; node = node->next) {
        total += strlen(node->text) + sep;
    }
    out = (char*)malloc(total);
    if (out == NULL) {
        return NULL;
    }
    cursor = out;
    for (node = list->head; node != NULL; node = node->next) {
        size_t length = strlen(node->text);
        memcpy(cursor, node->text, length);
        cursor += length;
        if (node->next != NULL) {
            memcpy(cursor, separator, sep);
            cursor += sep;
        }
    }
    *cursor = '\0';
    return out;
}
