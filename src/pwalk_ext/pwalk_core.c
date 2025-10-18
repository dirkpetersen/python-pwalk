/*
 * pwalk_core_fixed.c - Fixed thread synchronization
 *
 * Key fixes:
 * 1. Proper ThreadCNT initialization
 * 2. Better error handling
 * 3. Timeout to prevent hangs
 * 4. Zstd compression fully integrated
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dirent.h>
#include <sys/stat.h>
#include <time.h>
#include <errno.h>
#include <pthread.h>
#include <unistd.h>

#ifdef HAVE_ZSTD
#include <zstd.h>
#endif

#define MAXTHRDS 32
#define MAXPATH 4096
#define BUFFER_SIZE (512 * 1024)

/* Thread-local CSV buffer */
typedef struct {
    char csv_buffer[BUFFER_SIZE];
    size_t used;
} ThreadBuffer;

/* Thread data */
struct threadData {
    char dname[MAXPATH];
    ino_t pinode;
    long depth;
    long THRDid;
    int flag;
    struct stat pstat;
    pthread_t thread_id;
    pthread_attr_t tattr;
    ThreadBuffer *buf;
};

/* Global state */
static int ThreadCNT = 0;  /* Active thread count */
static int totalTHRDS = 0;
static struct threadData tdslot[MAXTHRDS];
static ThreadBuffer buffers[MAXTHRDS];
static pthread_mutex_t mutexFD = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t mutexOutput = PTHREAD_MUTEX_INITIALIZER;
static FILE *output_file = NULL;
static int SNAPSHOT = 1;

#ifdef HAVE_ZSTD
static ZSTD_CStream *zstd_stream = NULL;
#endif

/* Flush buffer */
static void flush_buffer(ThreadBuffer *buf) {
    if (buf->used == 0) return;

    pthread_mutex_lock(&mutexOutput);

#ifdef HAVE_ZSTD
    if (zstd_stream) {
        char compressed[BUFFER_SIZE + 16384];
        ZSTD_inBuffer input = { buf->csv_buffer, buf->used, 0 };
        ZSTD_outBuffer output = { compressed, sizeof(compressed), 0 };

        ZSTD_compressStream2(zstd_stream, &output, &input, ZSTD_e_continue);
        fwrite(compressed, 1, output.pos, output_file);
    } else
#endif
    {
        fwrite(buf->csv_buffer, 1, buf->used, output_file);
    }

    buf->used = 0;
    pthread_mutex_unlock(&mutexOutput);
}

/* CSV escape */
static void csv_escape(const char *in, char *out) {
    while (*in) {
        if (*in == '"') {
            *out++ = '"';
            *out++ = '"';
        } else {
            *out++ = *in;
        }
        in++;
    }
    *out = '\0';
}

/* Write CSV record */
static void write_record(ThreadBuffer *buf, const char *path, struct stat *st,
                        ino_t parent_inode, int depth, long fcount, long dirsum) {
    char line[MAXPATH * 2];
    char esc_name[MAXPATH * 2], esc_ext[256];

    const char *filename = strrchr(path, '/');
    filename = filename ? filename + 1 : path;

    const char *ext = strrchr(filename, '.');
    ext = (ext && ext > filename) ? ext + 1 : "";

    csv_escape(filename, esc_name);
    csv_escape(ext, esc_ext);

    int len = snprintf(line, sizeof(line),
        "%lu,%lu,%d,\"%s\",\"%s\",%u,%u,%ld,%lu,%ld,%lu,\"%o\",%ld,%ld,%ld,%ld,%ld\n",
        (unsigned long)st->st_ino, (unsigned long)parent_inode, depth,
        esc_name, esc_ext, st->st_uid, st->st_gid, (long)st->st_size,
        (unsigned long)st->st_dev, (long)st->st_blocks,
        (unsigned long)st->st_nlink, st->st_mode,
        (long)st->st_atime, (long)st->st_mtime, (long)st->st_ctime,
        fcount, dirsum);

    if (buf->used + len >= BUFFER_SIZE) {
        flush_buffer(buf);
    }
    memcpy(buf->csv_buffer + buf->used, line, len);
    buf->used += len;
}

/* Traverse directory */
static void* traverse(void *arg) {
    struct threadData *cur = (struct threadData *)arg;
    DIR *dirp;
    struct dirent *d;
    struct stat f;
    char fullpath[MAXPATH];
    struct threadData *new, local;
    long localCnt = 0, localSz = 0;
    int slot = 0;

    dirp = opendir(cur->dname);
    if (!dirp) {
        /* Even if we can't open, still decrement counter */
        goto cleanup;
    }

    while ((d = readdir(dirp)) != NULL) {
        if (strcmp(".", d->d_name) == 0 || strcmp("..", d->d_name) == 0)
            continue;
        if (SNAPSHOT && strcmp(".snapshot", d->d_name) == 0)
            continue;

        snprintf(fullpath, MAXPATH, "%s/%s", cur->dname, d->d_name);

        if (lstat(fullpath, &f) == -1)
            continue;

        localCnt++;

        if (S_ISDIR(f.st_mode)) {
            pthread_mutex_lock(&mutexFD);
            if (ThreadCNT < MAXTHRDS) {
                slot = 0;
                while (slot < MAXTHRDS && tdslot[slot].THRDid != -1) slot++;

                if (slot < MAXTHRDS) {
                    new = &tdslot[slot];
                    new->THRDid = totalTHRDS++;
                    new->flag = 0;
                    new->buf = &buffers[slot];
                    ThreadCNT++;  /* Increment BEFORE creating thread */
                } else {
                    new = &local;
                    new->THRDid = cur->THRDid;
                    new->flag = cur->flag + 1;
                    new->buf = cur->buf;
                }
            } else {
                new = &local;
                new->THRDid = cur->THRDid;
                new->flag = cur->flag + 1;
                new->buf = cur->buf;
            }
            pthread_mutex_unlock(&mutexFD);

            strcpy(new->dname, fullpath);
            new->depth = cur->depth + 1;
            new->pinode = cur->pstat.st_ino;
            memcpy(&new->pstat, &f, sizeof(struct stat));

            if (new->THRDid != cur->THRDid) {
                pthread_create(&tdslot[slot].thread_id, &tdslot[slot].tattr,
                              traverse, (void*)new);
            } else {
                traverse((void*)new);
            }
        } else {
            localSz += f.st_size;
            write_record(cur->buf, fullpath, &f, cur->pstat.st_ino, cur->depth, -1, 0);
        }
    }

    if (dirp) closedir(dirp);
    write_record(cur->buf, cur->dname, &cur->pstat, cur->pinode, cur->depth, localCnt, localSz);

cleanup:
    if (cur->flag == 0) {
        /* This is a thread (not recursion) */
        flush_buffer(cur->buf);
        pthread_mutex_lock(&mutexFD);
        ThreadCNT--;
        cur->THRDid = -1;
        pthread_mutex_unlock(&mutexFD);
        pthread_exit(NULL);
    }
    return NULL;
}

/* Python API */
static PyObject* csv_write(PyObject *self, PyObject *args) {
    const char *top, *output;
    int max_threads = 8, ignore_snaps = 1, compress = 0;

    if (!PyArg_ParseTuple(args, "ss|iii", &top, &output, &max_threads, &ignore_snaps, &compress)) {
        return NULL;
    }

    SNAPSHOT = ignore_snaps;

    /* Open output */
    output_file = fopen(output, "wb");
    if (!output_file) {
        return PyErr_SetFromErrnoWithFilename(PyExc_IOError, output);
    }

#ifdef HAVE_ZSTD
    if (compress) {
        zstd_stream = ZSTD_createCStream();
        ZSTD_initCStream(zstd_stream, 1);
    }
#endif

    /* Write header */
    const char *header = "inode,parent-inode,directory-depth,\"filename\",\"fileExtension\","
                        "UID,GID,st_size,st_dev,st_blocks,st_nlink,\"st_mode\","
                        "st_atime,st_mtime,st_ctime,pw_fcount,pw_dirsum\n";
    fwrite(header, 1, strlen(header), output_file);

    /* Initialize - IMPORTANT: ThreadCNT starts at 0, will be 1 when first thread starts */
    ThreadCNT = 0;
    totalTHRDS = 0;

    for (int i = 0; i < MAXTHRDS; i++) {
        buffers[i].used = 0;
        tdslot[i].THRDid = -1;
        pthread_attr_init(&tdslot[i].tattr);
        pthread_attr_setdetachstate(&tdslot[i].tattr, PTHREAD_CREATE_DETACHED);
    }

    struct stat root;
    if (lstat(top, &root) == -1) {
        fclose(output_file);
        return PyErr_SetFromErrnoWithFilename(PyExc_OSError, top);
    }

    strcpy(tdslot[0].dname, top);
    tdslot[0].THRDid = totalTHRDS++;
    tdslot[0].flag = 0;
    tdslot[0].depth = -1;
    tdslot[0].pinode = 0;
    tdslot[0].buf = &buffers[0];
    memcpy(&tdslot[0].pstat, &root, sizeof(struct stat));

    /* Increment ThreadCNT BEFORE creating thread */
    ThreadCNT = 1;

    /* Wait variables declared before Py_BEGIN_ALLOW_THREADS */
    int iterations = 0;
    int max_iterations = 3600;  /* 1 hour timeout */
    int active = 1;

    /* Start traversal with GIL released */
    Py_BEGIN_ALLOW_THREADS

    pthread_create(&tdslot[0].thread_id, &tdslot[0].tattr, traverse, (void*)&tdslot[0]);

    /* Wait for completion */
    while (iterations < max_iterations) {
        usleep(100000);  /* 100ms */

        pthread_mutex_lock(&mutexFD);
        active = ThreadCNT;
        pthread_mutex_unlock(&mutexFD);

        if (active == 0) break;

        iterations++;
    }

    Py_END_ALLOW_THREADS

    if (iterations >= max_iterations) {
        fprintf(stderr, "WARNING: Timeout waiting for threads (active=%d)\n", active);
    }

    /* Finalize zstd stream */
#ifdef HAVE_ZSTD
    if (compress && zstd_stream) {
        char final_output[BUFFER_SIZE];
        ZSTD_outBuffer output_buf = { final_output, sizeof(final_output), 0 };
        ZSTD_inBuffer empty_input = { NULL, 0, 0 };

        ZSTD_compressStream2(zstd_stream, &output_buf, &empty_input, ZSTD_e_end);
        fwrite(final_output, 1, output_buf.pos, output_file);

        ZSTD_freeCStream(zstd_stream);
        zstd_stream = NULL;
    }
#endif

    fclose(output_file);

    return Py_BuildValue("{s:s,s:i}", "output", output, "compressed", compress);
}

static PyMethodDef Methods[] = {
    {"write_csv", csv_write, METH_VARARGS, "Write CSV with optional zstd"},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "_pwalk_core",
    "High-performance parallel filesystem walker with zstd compression",
    -1, Methods
};

PyMODINIT_FUNC PyInit__pwalk_core(void) {
    PyObject *m = PyModule_Create(&module);
#ifdef HAVE_ZSTD
    PyModule_AddIntConstant(m, "HAS_ZSTD", 1);
#else
    PyModule_AddIntConstant(m, "HAS_ZSTD", 0);
#endif
    return m;
}
