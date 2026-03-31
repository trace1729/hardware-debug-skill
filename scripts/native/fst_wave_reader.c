#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "fstapi.h"

typedef struct {
    char **scope_ids;
    char **scope_names;
    size_t depth;
    size_t capacity;
    unsigned int scope_counter;
} HierState;

typedef struct {
    uint64_t t_start;
    uint64_t t_end;
} RangeQueryContext;

static void die(const char *message) {
    fprintf(stderr, "%s\n", message);
    exit(1);
}

static void *xcalloc(size_t n, size_t size) {
    void *ptr = calloc(n, size);
    if (ptr == NULL) {
        die("allocation failure");
    }
    return ptr;
}

static char *xstrdup(const char *src) {
    size_t len = strlen(src);
    char *dst = xcalloc(len + 1, 1);
    memcpy(dst, src, len);
    return dst;
}

static void ensure_capacity(HierState *state) {
    if (state->depth < state->capacity) {
        return;
    }
    size_t next = state->capacity == 0 ? 8 : state->capacity * 2;
    state->scope_ids = realloc(state->scope_ids, next * sizeof(*state->scope_ids));
    state->scope_names = realloc(state->scope_names, next * sizeof(*state->scope_names));
    if (state->scope_ids == NULL || state->scope_names == NULL) {
        die("allocation failure");
    }
    state->capacity = next;
}

static void json_escape_string(const char *value) {
    const unsigned char *p = (const unsigned char *)value;
    putchar('"');
    while (*p) {
        switch (*p) {
            case '\\':
                fputs("\\\\", stdout);
                break;
            case '"':
                fputs("\\\"", stdout);
                break;
            case '\n':
                fputs("\\n", stdout);
                break;
            case '\r':
                fputs("\\r", stdout);
                break;
            case '\t':
                fputs("\\t", stdout);
                break;
            default:
                if (*p < 0x20) {
                    printf("\\u%04x", *p);
                } else {
                    putchar(*p);
                }
                break;
        }
        p++;
    }
    putchar('"');
}

static char *join_scope_path(HierState *state, const char *leaf) {
    size_t total = leaf != NULL ? strlen(leaf) : 0;
    for (size_t i = 0; i < state->depth; ++i) {
        total += strlen(state->scope_names[i]) + 1;
    }
    char *buf = xcalloc(total + 1, 1);
    size_t pos = 0;
    for (size_t i = 0; i < state->depth; ++i) {
        size_t len = strlen(state->scope_names[i]);
        memcpy(buf + pos, state->scope_names[i], len);
        pos += len;
        buf[pos++] = '.';
    }
    if (leaf != NULL) {
        size_t leaf_len = strlen(leaf);
        memcpy(buf + pos, leaf, leaf_len);
        pos += leaf_len;
    } else if (pos > 0) {
        pos -= 1;
    }
    buf[pos] = 0;
    return buf;
}

static const char *scope_kind_name(unsigned char typ) {
    switch (typ) {
        case FST_ST_VCD_MODULE:
            return "module";
        case FST_ST_VCD_TASK:
            return "task";
        case FST_ST_VCD_FUNCTION:
            return "function";
        case FST_ST_VCD_BEGIN:
            return "begin";
        case FST_ST_VCD_FORK:
            return "fork";
        case FST_ST_VCD_GENERATE:
            return "generate";
        case FST_ST_VCD_STRUCT:
            return "struct";
        case FST_ST_VCD_UNION:
            return "union";
        case FST_ST_VCD_CLASS:
            return "class";
        case FST_ST_VCD_INTERFACE:
            return "interface";
        case FST_ST_VCD_PACKAGE:
            return "package";
        case FST_ST_VCD_PROGRAM:
            return "program";
        default:
            return "scope";
    }
}

static const char *value_kind_name(unsigned char typ, uint32_t bit_width) {
    if (typ == FST_VT_VCD_REAL || typ == FST_VT_VCD_REAL_PARAMETER || typ == FST_VT_SV_SHORTREAL) {
        return "real";
    }
    if (bit_width == 1) {
        return "scalar";
    }
    return "vector";
}

static void emit_scope_record(HierState *state, const char *scope_id, const char *local_name, const char *scope_kind) {
    char *full_scope_path = join_scope_path(state, local_name);
    fputs("{\"type\":\"scope\",\"scope_id\":", stdout);
    json_escape_string(scope_id);
    fputs(",\"parent_scope_id\":", stdout);
    if (state->depth == 0) {
        fputs("null", stdout);
    } else {
        json_escape_string(state->scope_ids[state->depth - 1]);
    }
    fputs(",\"scope_kind\":", stdout);
    json_escape_string(scope_kind);
    fputs(",\"local_name\":", stdout);
    json_escape_string(local_name);
    fputs(",\"full_scope_path\":", stdout);
    json_escape_string(full_scope_path);
    fputs("}\n", stdout);
    free(full_scope_path);
}

static void emit_signal_record(HierState *state, fstHandle handle, const char *local_name, uint32_t bit_width, const char *value_kind) {
    char handle_buf[32];
    char *full_wave_path = join_scope_path(state, local_name);
    snprintf(handle_buf, sizeof(handle_buf), "%" PRIu32, handle);
    fputs("{\"type\":\"signal\",\"source_id\":", stdout);
    json_escape_string(handle_buf);
    fputs(",\"scope_id\":", stdout);
    if (state->depth == 0) {
        fputs("null", stdout);
    } else {
        json_escape_string(state->scope_ids[state->depth - 1]);
    }
    fputs(",\"full_wave_path\":", stdout);
    json_escape_string(full_wave_path);
    fputs(",\"local_name\":", stdout);
    json_escape_string(local_name);
    printf(",\"bit_width\":%" PRIu32, bit_width);
    fputs(",\"value_kind\":", stdout);
    json_escape_string(value_kind);
    fputs("}\n", stdout);
    free(full_wave_path);
}

static void push_scope(HierState *state, const char *scope_id, const char *scope_name) {
    ensure_capacity(state);
    state->scope_ids[state->depth] = xstrdup(scope_id);
    state->scope_names[state->depth] = xstrdup(scope_name);
    state->depth += 1;
}

static void pop_scope(HierState *state) {
    if (state->depth == 0) {
        return;
    }
    state->depth -= 1;
    free(state->scope_ids[state->depth]);
    free(state->scope_names[state->depth]);
    state->scope_ids[state->depth] = NULL;
    state->scope_names[state->depth] = NULL;
}

static void free_hier_state(HierState *state) {
    while (state->depth > 0) {
        pop_scope(state);
    }
    free(state->scope_ids);
    free(state->scope_names);
}

static void emit_change_record(void *user_data, uint64_t time, fstHandle facidx, const unsigned char *value) {
    (void)user_data;
    char handle_buf[32];
    snprintf(handle_buf, sizeof(handle_buf), "%" PRIu32, facidx);
    fputs("{\"type\":\"change\",\"t\":", stdout);
    printf("%" PRIu64, time);
    fputs(",\"source_id\":", stdout);
    json_escape_string(handle_buf);
    fputs(",\"value\":", stdout);
    json_escape_string((const char *)value);
    fputs("}\n", stdout);
}

static void emit_change_record_in_range(void *user_data, uint64_t time, fstHandle facidx, const unsigned char *value) {
    RangeQueryContext *ctx = (RangeQueryContext *)user_data;
    if ((ctx != NULL) && ((time < ctx->t_start) || (time > ctx->t_end))) {
        return;
    }
    emit_change_record(NULL, time, facidx, value);
}

static void emit_summary_record(fstReaderContext *ctx) {
    fputs("{\"type\":\"summary\",\"start_time\":", stdout);
    printf("%" PRIu64, fstReaderGetStartTime(ctx));
    fputs(",\"end_time\":", stdout);
    printf("%" PRIu64, fstReaderGetEndTime(ctx));
    fputs(",\"scope_count\":", stdout);
    printf("%" PRIu64, fstReaderGetScopeCount(ctx));
    fputs(",\"signal_count\":", stdout);
    printf("%" PRIu64, fstReaderGetVarCount(ctx));
    fputs("}\n", stdout);
}

static int emit_hierarchy_records(fstReaderContext *ctx) {
    fstReaderIterateHierRewind(ctx);
    HierState state = {0};
    struct fstHier *hier = NULL;
    while ((hier = fstReaderIterateHier(ctx)) != NULL) {
        if (hier->htyp == FST_HT_SCOPE) {
            char scope_id[32];
            snprintf(scope_id, sizeof(scope_id), "scope%u", state.scope_counter++);
            emit_scope_record(&state, scope_id, hier->u.scope.name, scope_kind_name(hier->u.scope.typ));
            push_scope(&state, scope_id, hier->u.scope.name);
            continue;
        }
        if (hier->htyp == FST_HT_UPSCOPE) {
            pop_scope(&state);
            continue;
        }
        if (hier->htyp == FST_HT_VAR) {
            emit_signal_record(
                &state,
                hier->u.var.handle,
                hier->u.var.name,
                hier->u.var.length,
                value_kind_name(hier->u.var.typ, hier->u.var.length)
            );
        }
    }
    free_hier_state(&state);
    return 0;
}

static int dump_fst(const char *path) {
    fstReaderContext *ctx = fstReaderOpen(path);
    if (ctx == NULL) {
        fprintf(stderr, "failed to open fst: %s\n", path);
        return 1;
    }

    emit_hierarchy_records(ctx);
    emit_summary_record(ctx);

    fstReaderSetFacProcessMaskAll(ctx);
    if (!fstReaderIterBlocks(ctx, emit_change_record, NULL, NULL)) {
        fstReaderClose(ctx);
        fprintf(stderr, "failed to iterate fst value changes: %s\n", path);
        return 1;
    }
    fstReaderClose(ctx);
    return 0;
}

static int dump_meta(const char *path) {
    fstReaderContext *ctx = fstReaderOpen(path);
    if (ctx == NULL) {
        fprintf(stderr, "failed to open fst: %s\n", path);
        return 1;
    }
    emit_hierarchy_records(ctx);
    emit_summary_record(ctx);
    fstReaderClose(ctx);
    return 0;
}

static int value_at_time(const char *path, const char *handle_text, const char *time_text, const char *bit_width_text) {
    char *handle_end = NULL;
    char *time_end = NULL;
    char *width_end = NULL;
    unsigned long handle_ul = strtoul(handle_text, &handle_end, 10);
    unsigned long long time_ull = strtoull(time_text, &time_end, 10);
    unsigned long bit_width_ul = strtoul(bit_width_text, &width_end, 10);
    if ((handle_end == NULL) || (*handle_end != 0) || (time_end == NULL) || (*time_end != 0) ||
        (width_end == NULL) || (*width_end != 0) || (handle_ul == 0)) {
        fprintf(stderr, "invalid handle/time/bit-width arguments\n");
        return 1;
    }

    fstReaderContext *ctx = fstReaderOpen(path);
    if (ctx == NULL) {
        fprintf(stderr, "failed to open fst: %s\n", path);
        return 1;
    }

    size_t buf_size = (size_t)bit_width_ul + 64;
    char *buf = xcalloc(buf_size, 1);
    char *value = fstReaderGetValueFromHandleAtTime(ctx, (uint64_t)time_ull, (fstHandle)handle_ul, buf);
    fputs("{\"type\":\"value\",\"source_id\":", stdout);
    json_escape_string(handle_text);
    fputs(",\"t\":", stdout);
    printf("%" PRIu64, (uint64_t)time_ull);
    fputs(",\"found\":", stdout);
    if (value == NULL) {
        fputs("false", stdout);
        fputs(",\"value\":null}\n", stdout);
    } else {
        fputs("true", stdout);
        fputs(",\"value\":", stdout);
        json_escape_string(value);
        fputs("}\n", stdout);
    }
    free(buf);
    fstReaderClose(ctx);
    return 0;
}

static int range_query(const char *path, const char *start_text, const char *end_text, int handle_argc, char **handle_argv) {
    char *start_end = NULL;
    char *end_end = NULL;
    unsigned long long t_start = strtoull(start_text, &start_end, 10);
    unsigned long long t_end = strtoull(end_text, &end_end, 10);
    if ((start_end == NULL) || (*start_end != 0) || (end_end == NULL) || (*end_end != 0) || (t_end < t_start)) {
        fprintf(stderr, "invalid time range arguments\n");
        return 1;
    }
    if (handle_argc <= 0) {
        fprintf(stderr, "range-query requires at least one handle\n");
        return 1;
    }

    fstReaderContext *ctx = fstReaderOpen(path);
    if (ctx == NULL) {
        fprintf(stderr, "failed to open fst: %s\n", path);
        return 1;
    }

    fstHandle max_handle = fstReaderGetMaxHandle(ctx);
    fstReaderClrFacProcessMaskAll(ctx);
    for (int i = 0; i < handle_argc; ++i) {
        char *handle_end = NULL;
        unsigned long handle_ul = strtoul(handle_argv[i], &handle_end, 10);
        if ((handle_end == NULL) || (*handle_end != 0) || (handle_ul == 0) || (handle_ul > max_handle)) {
            fstReaderClose(ctx);
            fprintf(stderr, "invalid handle: %s\n", handle_argv[i]);
            return 1;
        }
        fstReaderSetFacProcessMask(ctx, (fstHandle)handle_ul);
    }

    fstReaderSetLimitTimeRange(ctx, (uint64_t)t_start, (uint64_t)t_end);
    RangeQueryContext range_ctx = {
        .t_start = (uint64_t)t_start,
        .t_end = (uint64_t)t_end,
    };
    if (!fstReaderIterBlocks(ctx, emit_change_record_in_range, &range_ctx, NULL)) {
        fstReaderClose(ctx);
        fprintf(stderr, "failed to iterate fst value changes: %s\n", path);
        return 1;
    }
    fstReaderClose(ctx);
    return 0;
}

int main(int argc, char **argv) {
    if ((argc == 3) && (strcmp(argv[1], "dump") == 0)) {
        return dump_fst(argv[2]);
    }
    if ((argc == 3) && (strcmp(argv[1], "meta") == 0)) {
        return dump_meta(argv[2]);
    }
    if ((argc == 6) && (strcmp(argv[1], "value-at-time") == 0)) {
        return value_at_time(argv[2], argv[3], argv[4], argv[5]);
    }
    if ((argc >= 6) && (strcmp(argv[1], "range-query") == 0)) {
        return range_query(argv[2], argv[3], argv[4], argc - 5, argv + 5);
    }
    fprintf(
        stderr,
        "usage: %s dump <wave.fst> | meta <wave.fst> | value-at-time <wave.fst> <handle> <time> <bit-width> | "
        "range-query <wave.fst> <t-start> <t-end> <handle> [<handle> ...]\n",
        argv[0]
    );
    return 1;
}
