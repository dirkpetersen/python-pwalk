/*
 * csv_zstd.c - Simple CSV writer with zstd frame compression
 *
 * Uses John Dey's CSV format with zstd streaming compression
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dirent.h>
#include <sys/stat.h>
#include <errno.h>
#include <pthread.h>
#include <unistd.h>

#ifdef HAVE_ZSTD
#include <zstd.h>
#endif

#define MAXTHRDS 32
#define MAXPATH 4096
#define BUFFER_SIZE (512 * 1024)  // 512KB buffer

/* Thread-local buffer */
typedef struct {
    char csv_buffer[BUFFER_SIZE];
    size_t used;
    int thread_id;
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
static int ThreadCNT = 1;
static int totalTHRDS = 0;
static struct threadData tdslot[MAXTHRDS];
static ThreadBuffer buffers[MAXTHRDS];
static pthread_mutex_t mutexFD = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t mutexOutput = PTHREAD_MUTEX_INITIALIZER;
static FILE *output_file = NULL;
static int SNAPSHOT = 1;

#ifdef HAVE_ZSTD
static ZSTD_CStream *zstd_stream = NULL;
static char zstd_output[BUFFER_SIZE];
#endif

/* Flush buffer to output */
static void flush_buffer(ThreadBuffer *buf) {
    if (buf->used == 0) return;

    pthread_mutex_lock(&mutexOutput);

#ifdef HAVE_ZSTD
    if (zstd_stream) {
        ZSTD_inBuffer input = { buf->csv_buffer, buf->used, 0 };
        ZSTD_outBuffer output = { zstd_output, sizeof(zstd_output), 0 };

        size_t result = ZSTD_compressStream2(zstd_stream, &output, &input, ZSTD_e_continue);
        if (!ZSTD_isError(result)) {
            fwrite(zstd_output, 1, output.pos, output_file);
        }
    } else
#endif
    {
        fwrite(buf->csv_buffer, 1, buf->used, output_file);
    }

    buf->used = 0;
    pthread_mutex_unlock(&mutexOutput);
}

/* Write CSV line */
static void write_csv_line(ThreadBuffer *buf, const char *line, size_t len) {
    if (buf->used + len >= BUFFER_SIZE) {
        flush_buffer(buf);
    }

    memcpy(buf->csv_buffer + buf->used, line, len);
    buf->used += len;
}

/* CSV escape */
static void csv_escape(const char *in, char *out) {
    char *o = out;
    while (*in) {
        if (*in == '"') {
            *o++ = '"';
            *o++ = '"';
        } else {
            *o++ = *in;
        }
        in++;
    }
    *o = '\0';
}

/* Write file record */
static void write_record(ThreadBuffer *buf, const char *path, struct stat *st,
                        ino_t parent_inode, int depth, long fcount, long dirsum) {
    char line[MAXPATH * 2];
    char escaped_name[MAXPATH * 2];
    char escaped_ext[256];

    const char *filename = strrchr(path, '/');
    filename = filename ? filename + 1 : path;

    const char *ext = strrchr(filename, '.');
    if (ext && ext > filename && *(ext-1) != '/') {
        ext++;
    } else {
        ext = "";
    }

    csv_escape(filename, escaped_name);
    csv_escape(ext, escaped_ext);

    int len = snprintf(line, sizeof(line),
        "%lu,%lu,%d,\"%s\",\"%s\",%u,%u,%ld,%lu,%ld,%lu,\"%o\",%ld,%ld,%ld,%ld,%ld\n",
        (unsigned long)st->st_ino,
        (unsigned long)parent_inode,
        depth,
        escaped_name,
        escaped_ext,
        st->st_uid,
        st->st_gid,
        (long)st->st_size,
        (unsigned long)st->st_dev,
        (long)st->st_blocks,
        (unsigned long)st->st_nlink,
        st->st_mode,
        (long)st->st_atime,
        (long)st->st_mtime,
        (long)st->st_ctime,
        fcount,
        dirsum
    );

    write_csv_line(buf, line, len);
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
    int slot;

    if ((dirp = opendir(cur->dname)) == NULL) {
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
                    ThreadCNT++;
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

    closedir(dirp);
    write_record(cur->buf, cur->dname, &cur->pstat, cur->pinode, cur->depth, localCnt, localSz);

cleanup:
    if (cur->flag == 0) {
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
        ZSTD_initCStream(zstd_stream, 1);  // Compression level 1 (fast)
    }
#endif

    /* Write header */
    const char *header = "inode,parent-inode,directory-depth,\"filename\",\"fileExtension\","
                        "UID,GID,st_size,st_dev,st_blocks,st_nlink,\"st_mode\","
                        "st_atime,st_mtime,st_ctime,pw_fcount,pw_dirsum\n";
    fwrite(header, 1, strlen(header), output_file);

    /* Initialize */
    ThreadCNT = 1;
    totalTHRDS = 0;

    for (int i = 0; i < MAXTHRDS; i++) {
        buffers[i].used = 0;
        buffers[i].thread_id = i;
        tdslot[i].THRDid = -1;
        pthread_attr_init(&tdslot[i].tattr);
        /* Make threads DETACHED so we can use ThreadCNT to track completion */
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

    /* Start */
    Py_BEGIN_ALLOW_THREADS
    pthread_create(&tdslot[0].thread_id, &tdslot[0].tattr, traverse, (void*)&tdslot[0]);

    while (1) {
        usleep(10000);
        pthread_mutex_lock(&mutexFD);
        int active = ThreadCNT;
        pthread_mutex_unlock(&mutexFD);
        if (active == 0) break;
    }
    Py_END_ALLOW_THREADS

#ifdef HAVE_ZSTD
    if (compress && zstd_stream) {
        ZSTD_outBuffer output_buf = { zstd_output, sizeof(zstd_output), 0 };
        ZSTD_endStream(zstd_stream, &output_buf);
        fwrite(zstd_output, 1, output_buf.pos, output_file);
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