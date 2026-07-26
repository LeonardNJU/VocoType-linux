#pragma once

#include <cstddef>
#include <sys/types.h>

namespace vocotype::common {

[[nodiscard]] bool set_close_on_exec(int descriptor) noexcept;
[[nodiscard]] int create_pipe_close_on_exec(int descriptors[2]) noexcept;
[[nodiscard]] int create_socket_close_on_exec(int domain, int type,
                                              int protocol) noexcept;
[[nodiscard]] int accept_close_on_exec(int listener) noexcept;
[[nodiscard]] ssize_t send_without_sigpipe(int descriptor, const void *data,
                                           std::size_t size) noexcept;
void ignore_sigpipe_once() noexcept;
void set_parent_death_signal(int signal_number) noexcept;

} // namespace vocotype::common
