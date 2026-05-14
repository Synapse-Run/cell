/* Minimal QuickJS WASI CLI - for .cell sandbox */
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include "quickjs.h"

/* Native print() -> stdout */
static JSValue js_print(JSContext *ctx, JSValueConst this_val,
                        int argc, JSValueConst *argv) {
    for (int i = 0; i < argc; i++) {
        if (i > 0) putchar(' ');
        const char *str = JS_ToCString(ctx, argv[i]);
        if (str) { fputs(str, stdout); JS_FreeCString(ctx, str); }
    }
    putchar('\n');
    fflush(stdout);
    return JS_UNDEFINED;
}

/* Native eprint() -> stderr */
static JSValue js_eprint(JSContext *ctx, JSValueConst this_val,
                         int argc, JSValueConst *argv) {
    for (int i = 0; i < argc; i++) {
        if (i > 0) fputc(' ', stderr);
        const char *str = JS_ToCString(ctx, argv[i]);
        if (str) { fputs(str, stderr); JS_FreeCString(ctx, str); }
    }
    fputc('\n', stderr);
    fflush(stderr);
    return JS_UNDEFINED;
}

static void js_add_helpers(JSContext *ctx) {
    JSValue global = JS_GetGlobalObject(ctx);

    /* print() */
    JS_SetPropertyStr(ctx, global, "print",
        JS_NewCFunction(ctx, js_print, "print", 1));

    /* console.log / console.error */
    JSValue console = JS_NewObject(ctx);
    JS_SetPropertyStr(ctx, console, "log",
        JS_NewCFunction(ctx, js_print, "log", 1));
    JS_SetPropertyStr(ctx, console, "error",
        JS_NewCFunction(ctx, js_eprint, "error", 1));
    JS_SetPropertyStr(ctx, console, "warn",
        JS_NewCFunction(ctx, js_eprint, "warn", 1));
    JS_SetPropertyStr(ctx, global, "console", console);

    JS_FreeValue(ctx, global);
}

int main(int argc, char **argv) {
    JSRuntime *rt = JS_NewRuntime();
    JSContext *ctx = JS_NewContext(rt);
    js_add_helpers(ctx);

    const char *code = NULL;
    const char *filename = "<input>";
    int eval_flags = JS_EVAL_TYPE_GLOBAL;

    for (int i = 1; i < argc; i++) {
        if ((!strcmp(argv[i], "-e") || !strcmp(argv[i], "--eval")) && i+1 < argc) {
            code = argv[++i];
        } else if (!strcmp(argv[i], "--std")) {
            /* no-op */
        } else {
            FILE *f = fopen(argv[i], "rb");
            if (!f) { fprintf(stderr, "Cannot open: %s\n", argv[i]); return 1; }
            fseek(f, 0, SEEK_END); long len = ftell(f); fseek(f, 0, SEEK_SET);
            char *buf = malloc(len+1); fread(buf, 1, len, f); buf[len] = 0; fclose(f);
            code = buf; filename = argv[i];
        }
    }
    if (!code) {
        static char buf[1048576];
        size_t n = fread(buf, 1, sizeof(buf)-1, stdin); buf[n] = 0;
        code = buf;
    }

    JSValue result = JS_Eval(ctx, code, strlen(code), filename, eval_flags);
    if (JS_IsException(result)) {
        JSValue exc = JS_GetException(ctx);
        const char *s = JS_ToCString(ctx, exc);
        if (s) { fprintf(stderr, "%s\n", s); JS_FreeCString(ctx, s); }
        /* Print stack trace if available */
        JSValue stack = JS_GetPropertyStr(ctx, exc, "stack");
        if (!JS_IsUndefined(stack)) {
            const char *st = JS_ToCString(ctx, stack);
            if (st) { fprintf(stderr, "%s\n", st); JS_FreeCString(ctx, st); }
        }
        JS_FreeValue(ctx, stack);
        JS_FreeValue(ctx, exc);
        JS_FreeValue(ctx, result);
        JS_FreeContext(ctx); JS_FreeRuntime(rt);
        return 1;
    }
    JS_FreeValue(ctx, result);
    JS_FreeContext(ctx); JS_FreeRuntime(rt);
    return 0;
}
