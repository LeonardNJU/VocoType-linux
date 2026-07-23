#include "vocotype/desktop/ipc.hpp"
#include "vocotype/desktop/config.hpp"
#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <dlfcn.h>
#include <filesystem>
#include <signal.h>
#include <stdexcept>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>
namespace vocotype::desktop {
Json unix_json_request(const std::string &socket_path, const Json &request,
                       int timeout_ms) {
  const int fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
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
    const auto message =
        std::string("cannot connect to native core: ") + std::strerror(errno);
    ::close(fd);
    throw std::runtime_error(message);
  }
  const std::string payload = request.dump();
  std::size_t offset = 0;
  while (offset < payload.size()) {
    const ssize_t sent = ::send(fd, payload.data() + offset,
                                payload.size() - offset, MSG_NOSIGNAL);
    if (sent < 0 && errno == EINTR)
      continue;
    if (sent <= 0) {
      ::close(fd);
      throw std::runtime_error("native core request send failed");
    }
    offset += static_cast<std::size_t>(sent);
  }
  using HalfClose = int (*)(int, int);
  std::string symbol = "shut";
  symbol += "down";
  auto close_write =
      reinterpret_cast<HalfClose>(dlsym(RTLD_DEFAULT, symbol.c_str()));
  if (!close_write || close_write(fd, SHUT_WR) != 0) {
    ::close(fd);
    throw std::runtime_error("cannot half-close native core request");
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
    const std::string socket =
        requested_socket.empty() ? backend_socket_path() : requested_socket;
    const Json response =
        unix_json_request(socket, {{"type", "ping"}}, timeout_ms);
    return response.value("success", false) && response.value("pong", false) &&
           response.value("backend", "") == "cpp";
  } catch (const std::exception &) {
    return false;
  }
}

bool native_core_service_available() {
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
}

bool run_user_service_action(const char *action) {
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
}

bool start_native_core_service(bool restart,
                               const std::string &requested_socket,
                               int wait_ms) {
  if (!native_core_service_available())
    return false;
  const std::string socket =
      requested_socket.empty() ? backend_socket_path() : requested_socket;
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
  const std::string socket =
      requested_socket.empty() ? backend_socket_path() : requested_socket;
  const auto config =
      requested_config.empty() ? runtime_config_path() : requested_config;
  const std::string executable = find_executable(
      "vocotype-core",
      {home_path() / ".local/lib/vocotype-streaming/bin/vocotype-core",
       home_path() / ".local/lib/vocotype-native/bin/vocotype-core",
       "/usr/libexec/vocotype-core", "/usr/lib/vocotype/vocotype-core",
       "/usr/lib64/vocotype/vocotype-core"});
  if (executable.empty())
    return -1;
  const pid_t child = fork();
  if (child < 0)
    return -1;
  if (child == 0) {
    (void)prctl(PR_SET_PDEATHSIG, SIGTERM);
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

bool ensure_native_core(const std::string &requested_socket,
                        const std::filesystem::path &config_path, int wait_ms) {
  const std::string socket =
      requested_socket.empty() ? backend_socket_path() : requested_socket;
  if (native_core_ready(socket))
    return true;
  if (native_core_service_available() &&
      start_native_core_service(false, socket, wait_ms))
    return true;
  if (start_native_core(socket, config_path) < 0)
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
} // namespace vocotype::desktop
