#include "vocotype/common/posix.hpp"

#include <cerrno>
#include <fcntl.h>
#include <mutex>
#include <signal.h>
#include <sys/socket.h>
#include <unistd.h>

#if defined(__linux__)
#include <sys/prctl.h>
#endif

namespace vocotype::common {
namespace {

bool configure_socket_no_sigpipe(int descriptor) noexcept {
#if defined(SO_NOSIGPIPE)
  const int enabled = 1;
  return ::setsockopt(descriptor, SOL_SOCKET, SO_NOSIGPIPE, &enabled,
                      sizeof(enabled)) == 0;
#else
  (void)descriptor;
  return true;
#endif
}

#if !defined(__linux__) || !defined(O_CLOEXEC)
void close_pair(int descriptors[2]) noexcept {
  if (descriptors[0] >= 0)
    ::close(descriptors[0]);
  if (descriptors[1] >= 0)
    ::close(descriptors[1]);
  descriptors[0] = -1;
  descriptors[1] = -1;
}
#endif

} // namespace

bool set_close_on_exec(int descriptor) noexcept {
  const int flags = ::fcntl(descriptor, F_GETFD);
  return flags >= 0 && ::fcntl(descriptor, F_SETFD, flags | FD_CLOEXEC) == 0;
}

int create_pipe_close_on_exec(int descriptors[2]) noexcept {
  descriptors[0] = -1;
  descriptors[1] = -1;
#if defined(__linux__) && defined(O_CLOEXEC)
  return ::pipe2(descriptors, O_CLOEXEC);
#else
  if (::pipe(descriptors) != 0)
    return -1;
  if (!set_close_on_exec(descriptors[0]) ||
      !set_close_on_exec(descriptors[1])) {
    const int saved = errno;
    close_pair(descriptors);
    errno = saved;
    return -1;
  }
  return 0;
#endif
}

int create_socket_close_on_exec(int domain, int type, int protocol) noexcept {
#if defined(__linux__) && defined(SOCK_CLOEXEC)
  const int descriptor = ::socket(domain, type | SOCK_CLOEXEC, protocol);
#else
  const int descriptor = ::socket(domain, type, protocol);
  if (descriptor >= 0 && !set_close_on_exec(descriptor)) {
    const int saved = errno;
    ::close(descriptor);
    errno = saved;
    return -1;
  }
#endif
  if (descriptor >= 0)
    (void)configure_socket_no_sigpipe(descriptor);
  return descriptor;
}

int accept_close_on_exec(int listener) noexcept {
#if defined(__linux__) && defined(SOCK_CLOEXEC)
  const int descriptor = ::accept4(listener, nullptr, nullptr, SOCK_CLOEXEC);
#else
  const int descriptor = ::accept(listener, nullptr, nullptr);
  if (descriptor >= 0 && !set_close_on_exec(descriptor)) {
    const int saved = errno;
    ::close(descriptor);
    errno = saved;
    return -1;
  }
#endif
  if (descriptor >= 0)
    (void)configure_socket_no_sigpipe(descriptor);
  return descriptor;
}

ssize_t send_without_sigpipe(int descriptor, const void *data,
                             std::size_t size) noexcept {
#if defined(MSG_NOSIGNAL)
  return ::send(descriptor, data, size, MSG_NOSIGNAL);
#else
  (void)configure_socket_no_sigpipe(descriptor);
  return ::send(descriptor, data, size, 0);
#endif
}

void ignore_sigpipe_once() noexcept {
  static std::once_flag once;
  std::call_once(once, [] {
    struct sigaction action {};
    action.sa_handler = SIG_IGN;
    sigemptyset(&action.sa_mask);
    (void)::sigaction(SIGPIPE, &action, nullptr);
  });
}

void set_parent_death_signal(int signal_number) noexcept {
#if defined(__linux__) && defined(PR_SET_PDEATHSIG)
  (void)::prctl(PR_SET_PDEATHSIG, signal_number);
#else
  (void)signal_number;
#endif
}

} // namespace vocotype::common
