#include "vocotype/desktop/ipc.hpp"
#include "vocotype/common/posix.hpp"
#include "vocotype/desktop/config.hpp"
#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <filesystem>
#include <fcntl.h>
#include <mutex>
#include <signal.h>
#include <stdexcept>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <thread>
#include <unordered_map>
#include <unistd.h>

namespace vocotype::desktop {
namespace {

std::mutex native_core_error_mutex;
std::unordered_map<std::string, std::string> native_core_errors;

std::string resolved_socket_path(const std::string &requested_socket) {
  return requested_socket.empty() ? backend_socket_path() : requested_socket;
}

void clear_native_core_error(const std::string &socket) {
  std::lock_guard lock(native_core_error_mutex);
  native_core_errors.erase(socket);
}

void remember_native_core_error(const std::string &socket,
                                const std::string &error) {
  std::lock_guard lock(native_core_error_mutex);
  native_core_errors[socket] = error;
}

std::string compact_error(std::string value) {
  std::replace(value.begin(), value.end(), '\r', ' ');
  std::replace(value.begin(), value.end(), '\n', ' ');
  while (!value.empty() && value.back() == ' ')
    value.pop_back();
  while (!value.empty() && value.front() == ' ')
    value.erase(value.begin());
  constexpr std::size_t kMaximum = 2048;
  if (value.size() > kMaximum)
    value = value.substr(0, kMaximum) + "…";
  return value;
}

std::string read_available(int descriptor) {
  std::string output;
  char buffer[1024];
  for (;;) {
    const ssize_t count = ::read(descriptor, buffer, sizeof(buffer));
    if (count > 0) {
      output.append(buffer, static_cast<std::size_t>(count));
      continue;
    }
    if (count < 0 && errno == EINTR)
      continue;
    if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
      break;
    break;
  }
  return output;
}

std::string child_failure_message(int status, const std::string &stderr_text) {
  std::string message = "native core failed to start";
  if (WIFEXITED(status))
    message += " (exit=" + std::to_string(WEXITSTATUS(status)) + ")";
  else if (WIFSIGNALED(status))
    message += " (signal=" + std::to_string(WTERMSIG(status)) + ")";
  const std::string details = compact_error(stderr_text);
  if (!details.empty())
    message += ": " + details;
  return message;
}

void detach_core_reaper(pid_t child, int stderr_descriptor) {
  const int flags = ::fcntl(stderr_descriptor, F_GETFL, 0);
  if (flags >= 0)
    (void)::fcntl(stderr_descriptor, F_SETFL, flags & ~O_NONBLOCK);
  std::thread([child, stderr_descriptor] {
    char buffer[1024];
    for (;;) {
      const ssize_t count = ::read(stderr_descriptor, buffer, sizeof(buffer));
      if (count > 0)
        continue;
      if (count < 0 && errno == EINTR)
        continue;
      break;
    }
    ::close(stderr_descriptor);
    int status = 0;
    while (::waitpid(child, &status, 0) < 0 && errno == EINTR) {
    }
  }).detach();
}

int terminate_child(pid_t child) {
  (void)::kill(child, SIGTERM);
  int status = 0;
  for (int attempt = 0; attempt < 20; ++attempt) {
    const pid_t waited = ::waitpid(child, &status, WNOHANG);
    if (waited == child)
      return status;
    if (waited < 0 && errno != EINTR)
      return status;
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
  }
  (void)::kill(child, SIGKILL);
  while (::waitpid(child, &status, 0) < 0 && errno == EINTR) {
  }
  return status;
}

Json unix_json_request_impl(const std::string &socket_path, const Json &request,
                            int timeout_ms, bool surface_startup_error) {
  const int fd = vocotype::common::create_socket_close_on_exec(
      AF_UNIX, SOCK_STREAM, 0);
  if (fd < 0)
    throw std::runtime_error("cannot create Unix socket");
  const int bounded = std::max(1, timeout_ms);
  const timeval timeout{bounded / 1000, (bounded % 1000) * 1000};
  (void)setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
  (void)setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
  sockaddr_un address{};
  address.sun_family = AF_UNIX;
  if (socket_path.size() >= sizeof(address.sun_path)) {
    ::close(fd);
    throw std::runtime_error("Unix socket path is too long");
  }
  std::memcpy(address.sun_path, socket_path.c_str(), socket_path.size() + 1);
  if (::connect(fd, reinterpret_cast<sockaddr *>(&address), sizeof(address)) !=
      0) {
    const int connect_error = errno;
    ::close(fd);
    if (surface_startup_error &&
        (connect_error == ENOENT || connect_error == ECONNREFUSED)) {
      const std::string startup_error = native_core_last_error(socket_path);
      if (!startup_error.empty())
        throw std::runtime_error(startup_error);
    }
    throw std::runtime_error(std::string("cannot connect to native core: ") +
                             std::strerror(connect_error));
  }
  clear_native_core_error(socket_path);
  const std::string payload = request.dump();
  std::size_t offset = 0;
  while (offset < payload.size()) {
    const ssize_t sent = vocotype::common::send_without_sigpipe(
        fd, payload.data() + offset, payload.size() - offset);
    if (sent < 0 && errno == EINTR)
      continue;
    if (sent <= 0) {
      ::close(fd);
      throw std::runtime_error("native core request send failed");
    }
    offset += static_cast<std::size_t>(sent);
  }
  std::string response;
  char buffer[8192];
  for (;;) {
    const ssize_t count = ::recv(fd, buffer, sizeof(buffer), 0);
    if (count < 0 && errno == EINTR)
      continue;
    if (count < 0) {
      ::close(fd);
      throw std::runtime_error("native core response timed out");
    }
    if (count == 0)
      break;
    response.append(buffer, static_cast<std::size_t>(count));
    if (response.size() > 4U * 1024U * 1024U) {
      ::close(fd);
      throw std::runtime_error("native core response is too large");
    }
  }
  ::close(fd);
  return Json::parse(response);
}

} // namespace

Json unix_json_request(const std::string &socket_path, const Json &request,
                       int timeout_ms) {
  return unix_json_request_impl(socket_path, request, timeout_ms, true);
}

std::string base64_encode(const unsigned char *data, std::size_t size) {
  static constexpr char table[] =
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  std::string output;
  output.reserve(((size + 2) / 3) * 4);
  for (std::size_t index = 0; index < size; index += 3) {
    const unsigned int a = data[index];
    const unsigned int b = index + 1 < size ? data[index + 1] : 0;
    const unsigned int c = index + 2 < size ? data[index + 2] : 0;
    const unsigned int value = (a << 16U) | (b << 8U) | c;
    output.push_back(table[(value >> 18U) & 0x3fU]);
    output.push_back(table[(value >> 12U) & 0x3fU]);
    output.push_back(index + 1 < size ? table[(value >> 6U) & 0x3fU] : '=');
    output.push_back(index + 2 < size ? table[value & 0x3fU] : '=');
  }
  return output;
}

bool native_core_ready(const std::string &requested_socket, int timeout_ms) {
  try {
    const std::string socket = resolved_socket_path(requested_socket);
    const Json response =
        unix_json_request_impl(socket, {{"type", "ping"}}, timeout_ms, false);
    return response.value("success", false) && response.value("pong", false) &&
           response.value("backend", "") == "cpp";
  } catch (const std::exception &) {
    return false;
  }
}

bool native_core_service_available() {
#if defined(__APPLE__)
  return false;
#else
  const auto home = home_path();
  for (const auto &path : {
           home / ".config/systemd/user/vocotype-fcitx5-backend.service",
           std::filesystem::path(
               "/usr/lib/systemd/user/vocotype-fcitx5-backend.service"),
           std::filesystem::path(
               "/usr/lib64/systemd/user/vocotype-fcitx5-backend.service"),
           std::filesystem::path(
               "/etc/systemd/user/vocotype-fcitx5-backend.service"),
       }) {
    if (std::filesystem::is_regular_file(path))
      return true;
  }
  return false;
#endif
}

bool run_user_service_action(const char *action) {
#if defined(__APPLE__)
  (void)action;
  return false;
#else
  const std::string manager = find_executable(
      std::string("system") + "ctl", {"/usr/bin/systemctl", "/bin/systemctl"});
  if (manager.empty())
    return false;
  const pid_t child = fork();
  if (child < 0)
    return false;
  if (child == 0) {
    const std::string runtime = "/run/user/" + std::to_string(getuid());
    if (!std::getenv("XDG_RUNTIME_DIR") &&
        std::filesystem::is_directory(runtime))
      setenv("XDG_RUNTIME_DIR", runtime.c_str(), 1);
    const std::string bus = runtime + "/bus";
    if (!std::getenv("DBUS_SESSION_BUS_ADDRESS") &&
        std::filesystem::exists(bus)) {
      const std::string address = "unix:path=" + bus;
      setenv("DBUS_SESSION_BUS_ADDRESS", address.c_str(), 1);
    }
    execl(manager.c_str(), manager.c_str(), "--user", action,
          "vocotype-fcitx5-backend.service", static_cast<char *>(nullptr));
    _exit(127);
  }
  int status = 0;
  while (waitpid(child, &status, 0) < 0 && errno == EINTR) {
  }
  return WIFEXITED(status) && WEXITSTATUS(status) == 0;
#endif
}

bool start_native_core_service(bool restart,
                               const std::string &requested_socket,
                               int wait_ms) {
  if (!native_core_service_available())
    return false;
  const std::string socket = resolved_socket_path(requested_socket);
  if (!run_user_service_action(restart ? "restart" : "start"))
    return false;
  const auto deadline =
      std::chrono::steady_clock::now() + std::chrono::milliseconds(wait_ms);
  while (std::chrono::steady_clock::now() < deadline) {
    if (native_core_ready(socket, 800))
      return true;
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
  }
  return false;
}

pid_t start_native_core(const std::string &requested_socket,
                        const std::filesystem::path &requested_config) {
  const std::string socket = resolved_socket_path(requested_socket);
  const auto config =
      requested_config.empty() ? runtime_config_path() : requested_config;
  const auto bundled = runtime_root();
  const std::string executable = find_executable(
      "vocotype-core",
      {bundled.empty() ? std::filesystem::path{} : bundled / "bin/vocotype-core",
       home_path() / ".local/lib/vocotype-streaming/bin/vocotype-core",
       home_path() / ".local/lib/vocotype-native/bin/vocotype-core",
       "/usr/libexec/vocotype-core", "/usr/lib/vocotype/vocotype-core",
       "/usr/lib64/vocotype/vocotype-core"});
  if (executable.empty())
    return -1;
  const pid_t child = fork();
  if (child < 0)
    return -1;
  if (child == 0) {
    vocotype::common::set_parent_death_signal(SIGTERM);
    if (getppid() == 1)
      _exit(125);
    const std::string config_string = config.string();
    if (std::filesystem::is_regular_file(config)) {
      execl(executable.c_str(), executable.c_str(), "--enable-final-asr",
            "--config", config_string.c_str(), "--socket-path", socket.c_str(),
            static_cast<char *>(nullptr));
    } else {
      execl(executable.c_str(), executable.c_str(), "--enable-final-asr",
            "--socket-path", socket.c_str(), static_cast<char *>(nullptr));
    }
    _exit(127);
  }
  std::thread([child] {
    int status = 0;
    while (waitpid(child, &status, 0) < 0 && errno == EINTR) {
    }
  }).detach();
  return child;
}

std::string native_core_last_error(const std::string &requested_socket) {
  const std::string socket = resolved_socket_path(requested_socket);
  std::lock_guard lock(native_core_error_mutex);
  const auto found = native_core_errors.find(socket);
  return found == native_core_errors.end() ? std::string() : found->second;
}

NativeCoreEnsureResult
ensure_native_core_status(const std::string &requested_socket,
                          const std::filesystem::path &config_path,
                          int wait_ms) {
  static std::mutex ensure_mutex;
  std::lock_guard ensure_lock(ensure_mutex);
  const std::string socket = resolved_socket_path(requested_socket);
  clear_native_core_error(socket);
  if (native_core_ready(socket))
    return {true, {}, -1};

  if (native_core_service_available() &&
      start_native_core_service(false, socket, wait_ms)) {
    clear_native_core_error(socket);
    return {true, {}, -1};
  }

  const auto config = config_path.empty() ? runtime_config_path() : config_path;
  const auto bundled = runtime_root();
  const std::string executable = find_executable(
      "vocotype-core",
      {bundled.empty() ? std::filesystem::path{} : bundled / "bin/vocotype-core",
       home_path() / ".local/lib/vocotype-streaming/bin/vocotype-core",
       home_path() / ".local/lib/vocotype-native/bin/vocotype-core",
       "/usr/libexec/vocotype-core", "/usr/lib/vocotype/vocotype-core",
       "/usr/lib64/vocotype/vocotype-core"});
  if (executable.empty()) {
    const std::string error =
        "native core failed to start: vocotype-core executable was not found";
    remember_native_core_error(socket, error);
    return {false, error, 127};
  }

  int descriptors[2];
  if (vocotype::common::create_pipe_close_on_exec(descriptors) != 0) {
    const std::string error =
        std::string("native core failed to start: cannot create stderr pipe: ") +
        std::strerror(errno);
    remember_native_core_error(socket, error);
    return {false, error, -1};
  }

  const pid_t child = fork();
  if (child < 0) {
    const int fork_error = errno;
    ::close(descriptors[0]);
    ::close(descriptors[1]);
    const std::string error =
        std::string("native core failed to start: cannot fork: ") +
        std::strerror(fork_error);
    remember_native_core_error(socket, error);
    return {false, error, -1};
  }
  if (child == 0) {
    ::close(descriptors[0]);
    (void)::dup2(descriptors[1], STDERR_FILENO);
    ::close(descriptors[1]);
    vocotype::common::set_parent_death_signal(SIGTERM);
    if (getppid() == 1)
      _exit(125);
    const std::string config_string = config.string();
    if (std::filesystem::is_regular_file(config)) {
      execl(executable.c_str(), executable.c_str(), "--enable-final-asr",
            "--config", config_string.c_str(), "--socket-path", socket.c_str(),
            static_cast<char *>(nullptr));
    } else {
      execl(executable.c_str(), executable.c_str(), "--enable-final-asr",
            "--socket-path", socket.c_str(), static_cast<char *>(nullptr));
    }
    dprintf(STDERR_FILENO, "exec %s failed: %s\n", executable.c_str(),
            std::strerror(errno));
    _exit(127);
  }

  ::close(descriptors[1]);
  const int flags = ::fcntl(descriptors[0], F_GETFL, 0);
  if (flags >= 0)
    (void)::fcntl(descriptors[0], F_SETFL, flags | O_NONBLOCK);

  std::string stderr_text;
  const int bounded_wait = std::max(1, wait_ms);
  const auto deadline = std::chrono::steady_clock::now() +
                        std::chrono::milliseconds(bounded_wait);
  while (std::chrono::steady_clock::now() < deadline) {
    stderr_text += read_available(descriptors[0]);
    if (native_core_ready(socket, 800)) {
      clear_native_core_error(socket);
      detach_core_reaper(child, descriptors[0]);
      return {true, {}, -1};
    }
    int status = 0;
    const pid_t waited = ::waitpid(child, &status, WNOHANG);
    if (waited == child) {
      stderr_text += read_available(descriptors[0]);
      ::close(descriptors[0]);
      const std::string error = child_failure_message(status, stderr_text);
      remember_native_core_error(socket, error);
      const int exit_code = WIFEXITED(status) ? WEXITSTATUS(status) : -1;
      return {false, error, exit_code};
    }
    if (waited < 0 && errno != EINTR) {
      const int wait_error = errno;
      ::close(descriptors[0]);
      const std::string error =
          std::string("native core failed to start: waitpid failed: ") +
          std::strerror(wait_error);
      remember_native_core_error(socket, error);
      return {false, error, -1};
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
  }

  if (native_core_ready(socket, 800)) {
    clear_native_core_error(socket);
    detach_core_reaper(child, descriptors[0]);
    return {true, {}, -1};
  }

  const int status = terminate_child(child);
  stderr_text += read_available(descriptors[0]);
  ::close(descriptors[0]);
  std::string error = "native core did not become ready within " +
                      std::to_string(bounded_wait) + " ms";
  const std::string details = compact_error(stderr_text);
  if (!details.empty())
    error += ": " + details;
  remember_native_core_error(socket, error);
  const int exit_code = WIFEXITED(status) ? WEXITSTATUS(status) : -1;
  return {false, error, exit_code};
}

bool ensure_native_core(const std::string &requested_socket,
                        const std::filesystem::path &config_path, int wait_ms) {
  return ensure_native_core_status(requested_socket, config_path, wait_ms).ready;
}
} // namespace vocotype::desktop
