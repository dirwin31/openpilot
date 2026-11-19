// Keeps bluetoothd from accepting ATT over BR/EDR (PSM 31) so iOS uses LE for GATT.
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdint.h>
#include <sys/socket.h>
struct l2addr { sa_family_t family; uint16_t psm; uint8_t bdaddr[6]; uint16_t cid; uint8_t type; } __attribute__((packed));
static int blocked[1024];
int bind(int fd, const struct sockaddr *addr, socklen_t len) {
  static int (*real)(int, const struct sockaddr *, socklen_t);
  if (!real) real = dlsym(RTLD_NEXT, "bind");
  if (addr && addr->sa_family == 31 && len >= (socklen_t)sizeof(struct l2addr) && fd >= 0 && fd < 1024) {
    const struct l2addr *l2 = (const struct l2addr *)addr;
    blocked[fd] = l2->psm == 31 && l2->cid == 0 && l2->type == 0;
  }
  return real(fd, addr, len);
}
int listen(int fd, int backlog) {
  static int (*real)(int, int);
  if (!real) real = dlsym(RTLD_NEXT, "listen");
  if (fd >= 0 && fd < 1024 && blocked[fd]) return 0;
  return real(fd, backlog);
}
