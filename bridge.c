#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>
#include <dlfcn.h>
#include <pthread.h>
#include <unistd.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <gtk/gtk.h>
#include <webkit/webkit.h>
#include <jsc/jsc.h>

static WebKitWebView *global_webview = NULL;
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t cond = PTHREAD_COND_INITIALIZER;

typedef struct {
    char *body;
    char *result;
    int done;
} EvalTask;

static void on_async_js_done(GObject *object, GAsyncResult *result, gpointer user_data) {
    EvalTask *task = (EvalTask *)user_data;
    GError *error = NULL;
    JSCValue *val = webkit_web_view_call_async_javascript_function_finish(WEBKIT_WEB_VIEW(object), result, &error);

    if (error) {
        char *escaped = g_strescape(error->message, NULL);
        task->result = g_strdup_printf("{\"success\":false,\"error\":\"%s\"}", escaped ? escaped : "Unknown error");
        if (escaped) g_free(escaped);
        g_error_free(error);
    } else if (val) {
        char *json = jsc_value_to_json(val, 0);
        if (json) {
            task->result = g_strdup_printf("{\"success\":true,\"result\":%s}", json);
            g_free(json);
        } else {
            char *str = jsc_value_to_string(val);
            char *escaped = str ? g_strescape(str, NULL) : NULL;
            task->result = g_strdup_printf("{\"success\":true,\"result\":\"%s\"}", escaped ? escaped : "");
            if (escaped) g_free(escaped);
            if (str) g_free(str);
        }
    } else {
        task->result = g_strdup("{\"success\":true,\"result\":null}");
    }

    pthread_mutex_lock(&lock);
    task->done = 1;
    pthread_cond_signal(&cond);
    pthread_mutex_unlock(&lock);
}

static gboolean run_script_idle(gpointer user_data) {
    EvalTask *task = (EvalTask *)user_data;
    if (!global_webview) {
        task->result = g_strdup("{\"success\":false,\"error\":\"WebView not initialized yet\"}");
        pthread_mutex_lock(&lock);
        task->done = 1;
        pthread_cond_signal(&cond);
        pthread_mutex_unlock(&lock);
        return FALSE;
    }

    char *trimmed = g_strstrip(g_strdup(task->body ? task->body : ""));
    if (strlen(trimmed) == 0) {
        g_free(trimmed);
        task->result = g_strdup("{\"success\":true,\"result\":null}");
        pthread_mutex_lock(&lock);
        task->done = 1;
        pthread_cond_signal(&cond);
        pthread_mutex_unlock(&lock);
        return FALSE;
    }

    char *clean_body;
    if (g_str_has_prefix(trimmed, "return ") || g_str_has_prefix(trimmed, "return;")) {
        clean_body = g_strdup(trimmed);
    } else {
        clean_body = g_strdup_printf("return (%s);", trimmed);
    }
    g_free(trimmed);

    webkit_web_view_call_async_javascript_function(
        global_webview,
        clean_body,
        -1,
        NULL,
        NULL,
        NULL,
        NULL,
        on_async_js_done,
        task
    );
    g_free(clean_body);
    return FALSE;
}

static void write_all(int fd, const void *buf, size_t count) {
    const char *ptr = (const char *)buf;
    while (count > 0) {
        ssize_t written = write(fd, ptr, count);
        if (written <= 0) break;
        ptr += written;
        count -= (size_t)written;
    }
}

static void *http_server_thread(void *arg) {
    int port = 8081;
    const char *port_env = getenv("BRIDGE_PORT");
    if (port_env && atoi(port_env) > 0) {
        port = atoi(port_env);
    }

    int server_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (server_fd < 0) {
        perror("[SpotiFLAC Bridge] socket creation failed");
        return NULL;
    }

    int opt = 1;
    setsockopt(server_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    struct sockaddr_in address;
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = INADDR_ANY;
    address.sin_port = htons(port);

    if (bind(server_fd, (struct sockaddr *)&address, sizeof(address)) < 0) {
        perror("[SpotiFLAC Bridge] socket bind failed");
        close(server_fd);
        return NULL;
    }
    listen(server_fd, 16);
    fprintf(stderr, "\n==========================================================\n");
    fprintf(stderr, "  [SpotiFLAC Bridge] REST API active on http://0.0.0.0:%d\n", port);
    fprintf(stderr, "  Endpoints: POST /eval  |  GET /health\n");
    fprintf(stderr, "==========================================================\n\n");
    fflush(stderr);

    while (1) {
        int client_fd = accept(server_fd, NULL, NULL);
        if (client_fd < 0) continue;

        char buffer[131072];
        size_t total_read = 0;
        char *header_end = NULL;

        // Read until headers end (\r\n\r\n)
        while (total_read < sizeof(buffer) - 1) {
            ssize_t n = read(client_fd, buffer + total_read, sizeof(buffer) - 1 - total_read);
            if (n <= 0) break;
            total_read += (size_t)n;
            buffer[total_read] = '\0';
            header_end = strstr(buffer, "\r\n\r\n");
            if (header_end) break;
        }

        if (!header_end) {
            close(client_fd);
            continue;
        }

        // Check if health check
        if (strncmp(buffer, "GET /health", 11) == 0) {
            const char *res = global_webview ?
                "{\"status\":\"ok\",\"webview_ready\":true}\n" :
                "{\"status\":\"initializing\",\"webview_ready\":false}\n";
            char response[512];
            int len = snprintf(response, sizeof(response),
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: application/json; charset=utf-8\r\n"
                "Access-Control-Allow-Origin: *\r\n"
                "Content-Length: %zu\r\n"
                "Connection: close\r\n\r\n%s",
                strlen(res), res
            );
            if (len > 0) {
                write_all(client_fd, response, (size_t)len);
            }
            close(client_fd);
            continue;
        }

        // Determine Content-Length
        size_t content_length = 0;
        char *cl_header = strcasestr(buffer, "Content-Length:");
        if (cl_header && cl_header < header_end) {
            content_length = (size_t)strtoul(cl_header + 15, NULL, 10);
        }

        char *body_start = header_end + 4;
        size_t body_received = total_read - (size_t)(body_start - buffer);
        char *dyn_allocated = NULL;

        if (content_length > 0 && (size_t)(content_length + (body_start - buffer) + 16) > sizeof(buffer)) {
            size_t needed = content_length + (size_t)(body_start - buffer) + 16;
            dyn_allocated = (char *)malloc(needed);
            if (dyn_allocated) {
                memcpy(dyn_allocated, buffer, total_read);
                body_start = dyn_allocated + (body_start - buffer);

                while (body_received < content_length) {
                    ssize_t n = read(client_fd, body_start + body_received, content_length - body_received);
                    if (n <= 0) break;
                    body_received += (size_t)n;
                }
            }
        } else {
            // Continue reading until entire body is received in stack buffer
            while (body_received < content_length && total_read < sizeof(buffer) - 1) {
                ssize_t n = read(client_fd, buffer + total_read, sizeof(buffer) - 1 - total_read);
                if (n <= 0) break;
                total_read += (size_t)n;
                buffer[total_read] = '\0';
                body_received += (size_t)n;
            }
        }

        // Ensure body is null-terminated at content_length
        if (content_length > 0 && body_received >= content_length) {
            body_start[content_length] = '\0';
        } else {
            body_start[body_received] = '\0';
        }

        EvalTask task;
        task.body = body_start;
        task.result = NULL;
        task.done = 0;

        pthread_mutex_lock(&lock);
        g_idle_add(run_script_idle, &task);
        while (!task.done) {
            pthread_cond_wait(&cond, &lock);
        }
        pthread_mutex_unlock(&lock);

        char response_header[512];
        int content_len = task.result ? strlen(task.result) : 0;
        int header_len = snprintf(response_header, sizeof(response_header),
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: application/json; charset=utf-8\r\n"
            "Access-Control-Allow-Origin: *\r\n"
            "Content-Length: %d\r\n"
            "Connection: close\r\n\r\n",
            content_len
        );

        if (header_len > 0) {
            write_all(client_fd, response_header, (size_t)header_len);
        }
        if (task.result && content_len > 0) {
            write_all(client_fd, task.result, (size_t)content_len);
            g_free(task.result);
        }
        if (dyn_allocated) {
            free(dyn_allocated);
        }
        close(client_fd);
    }
    return NULL;
}

static void init_bridge_for_webview(WebKitWebView *view) {
    if (!view) return;
    pthread_mutex_lock(&lock);
    if (global_webview != NULL) {
        pthread_mutex_unlock(&lock);
        return;
    }
    global_webview = view;
    pthread_mutex_unlock(&lock);

    fprintf(stderr, "\n[SpotiFLAC Bridge] WebKitWebView hooked at %p\n", (void*)global_webview);
    fflush(stderr);

    pthread_t tid;
    pthread_create(&tid, NULL, http_server_thread, NULL);
}

// Hook g_object_new to capture WebKitWebView instance on GTK 4 / WebKitGTK 6.0
gpointer g_object_new(GType object_type, const gchar *first_property_name, ...) {
    static gpointer (*real_valist)(GType, const gchar *, va_list) = NULL;
    if (!real_valist) {
        real_valist = dlsym(RTLD_NEXT, "g_object_new_valist");
    }
    va_list args;
    va_start(args, first_property_name);
    gpointer obj = real_valist(object_type, first_property_name, args);
    va_end(args);

    if (obj) {
        const char *type_name = g_type_name(object_type);
        if (type_name && strcmp(type_name, "WebKitWebView") == 0) {
            init_bridge_for_webview(WEBKIT_WEB_VIEW(obj));
        } else if (webkit_web_view_get_type && g_type_is_a(object_type, webkit_web_view_get_type())) {
            init_bridge_for_webview(WEBKIT_WEB_VIEW(obj));
        }
    }
    return obj;
}

// Secondary hooks for WebKitWebView calls
void webkit_web_view_load_uri(WebKitWebView *web_view, const gchar *uri) {
    static void (*real_func)(WebKitWebView *, const gchar *) = NULL;
    if (!real_func) real_func = dlsym(RTLD_NEXT, "webkit_web_view_load_uri");
    init_bridge_for_webview(web_view);
    if (real_func) real_func(web_view, uri);
}

void webkit_web_view_load_alternate_html(WebKitWebView *web_view, const gchar *content, const gchar *content_uri, const gchar *base_uri) {
    static void (*real_func)(WebKitWebView *, const gchar *, const gchar *, const gchar *) = NULL;
    if (!real_func) real_func = dlsym(RTLD_NEXT, "webkit_web_view_load_alternate_html");
    init_bridge_for_webview(web_view);
    if (real_func) real_func(web_view, content, content_uri, base_uri);
}

void webkit_web_view_set_settings(WebKitWebView *web_view, WebKitSettings *settings) {
    static void (*real_func)(WebKitWebView *, WebKitSettings *) = NULL;
    if (!real_func) real_func = dlsym(RTLD_NEXT, "webkit_web_view_set_settings");
    init_bridge_for_webview(web_view);
    if (real_func) real_func(web_view, settings);
}

void webkit_web_view_set_background_color(WebKitWebView *web_view, const GdkRGBA *rgba) {
    static void (*real_func)(WebKitWebView *, const GdkRGBA *) = NULL;
    if (!real_func) real_func = dlsym(RTLD_NEXT, "webkit_web_view_set_background_color");
    init_bridge_for_webview(web_view);
    if (real_func) real_func(web_view, rgba);
}
