// mread - passive memory reader for another process via process_vm_readv
#define _GNU_SOURCE
// usage:
//   mread <pid> maps                 -> print /proc/pid/maps to stdout
//   mread <pid> read <hexaddr> <len> -> write raw bytes to stdout
//   mread <pid> serve <port>         -> TCP server: req=[u64 addr][u32 len] resp=[u8 ok][payload]
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <sys/uio.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>

static ssize_t vmread(pid_t pid, unsigned long long addr, void *buf, size_t len) {
    struct iovec liov = { buf, len };
    struct iovec riov = { (void *)addr, len };
    return process_vm_readv(pid, &liov, 1, &riov, 1, 0);
}

static ssize_t read_full(int fd, void *buf, size_t len) {
    size_t off = 0;
    while (off < len) {
        ssize_t n = read(fd, (char *)buf + off, len - off);
        if (n == 0) return (ssize_t)off;
        if (n < 0 && errno == EINTR) continue;
        if (n < 0) return -1;
        off += (size_t)n;
    }
    return (ssize_t)off;
}

static int write_full(int fd, const void *buf, size_t len) {
    size_t off = 0;
    while (off < len) {
        ssize_t n = write(fd, (const char *)buf + off, len - off);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return -1;
        off += (size_t)n;
    }
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage\n"); return 1; }
    pid_t pid = (pid_t)atoi(argv[1]);
    const char *cmd = argv[2];

    if (!strcmp(cmd, "maps")) {
        char p[64]; snprintf(p, sizeof p, "/proc/%d/maps", pid);
        FILE *f = fopen(p, "r"); if (!f) { perror("maps"); return 1; }
        char line[512];
        while (fgets(line, sizeof line, f)) fputs(line, stdout);
        fclose(f); return 0;
    }

    if (!strcmp(cmd, "read")) {
        unsigned long long addr = strtoull(argv[3], 0, 16);
        size_t len = (size_t)strtoull(argv[4], 0, 0);
        static char buf[1 << 22];
        if (len > sizeof buf) len = sizeof buf;
        ssize_t n = vmread(pid, addr, buf, len);
        if (n <= 0) { fprintf(stderr, "read fail %d\n", errno); return 2; }
        fwrite(buf, 1, n, stdout);
        return 0;
    }

    if (!strcmp(cmd, "serve")) {
        int port = atoi(argv[3]);
        int s = socket(AF_INET, SOCK_STREAM, 0);
        int one = 1; setsockopt(s, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
        struct sockaddr_in sa = {0};
        sa.sin_family = AF_INET; sa.sin_port = htons(port);
        sa.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        if (bind(s, (void*)&sa, sizeof sa) || listen(s, 4)) { perror("bind"); return 1; }
        fprintf(stderr, "mread serve pid=%d port=%d\n", pid, port);
        for (;;) {
            int c = accept(s, 0, 0);
            if (c < 0) { if (errno == EINTR) continue; perror("accept"); return 1; }
            setsockopt(c, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
            unsigned char hdr[12];
            while (read_full(c, hdr, sizeof hdr) == (ssize_t)sizeof hdr) {
                unsigned long long addr; unsigned int len;
                memcpy(&addr, hdr, 8); memcpy(&len, hdr + 8, 4);
                if (len > (1 << 22)) len = 1 << 22;
                static char buf[1 << 22];
                ssize_t n = vmread(pid, addr, buf, len);
                if (n < 0) n = 0;
                unsigned char hdr2[5] = { (n == (ssize_t)len), 0,0,0,0 };
                unsigned int nl = (unsigned int)n;
                memcpy(hdr2 + 1, &nl, 4);
                if (write_full(c, hdr2, sizeof hdr2) < 0 ||
                    (n > 0 && write_full(c, buf, (size_t)n) < 0)) break;
            }
            close(c);
        }
    }
    fprintf(stderr, "bad cmd\n"); return 1;
}
