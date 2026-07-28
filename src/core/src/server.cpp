#include "vocotype/core/server.hpp"
#include "vocotype/common/posix.hpp"

#include <poll.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#include <cerrno>
#include <cstring>
#include <stdexcept>
#include <thread>
#include <utility>

namespace vocotype::core {
namespace {

class Fd final {
public:
  explicit Fd(int value = -1) : value_(value) {}
  Fd(const Fd &) = delete;
  Fd &operator=(const Fd &) = delete;
  ~Fd() {
    if (value_ >= 0) {
      ::close(value_);
    }
  }
  [[nodiscard]] int get() const noexcept { return value_; }

private:
  int value_;
};

std::runtime_error system_error(const std::string &action) {
  return std::runtime_error(action + ": " + std::strerror(errno));
}

} // namespace

UnixJsonServer::UnixJsonServer(ServerConfig config,
                               const CoreDispatcher &dispatcher)
    : config_(std::move(config)), dispatcher_(dispatcher) {}

UnixJsonServer::~UnixJsonServer() {
  stop();
  wait_for_clients();
  try {
    cleanup_socket_path();
  } catch (...) {
  }
}

const std::string &UnixJsonServer::socket_path() const noexcept {
  return config_.socket_path;
}

void UnixJsonServer::cleanup_socket_path() {
  struct stat status{};
  if (::lstat(config_.socket_path.c_str(), &status) != 0) {
    if (errno == ENOENT) {
      return;
    }
    throw system_error("lstat socket path");
  }
  if (!S_ISSOCK(status.st_mode) && !S_ISLNK(status.st_mode)) {
    throw std::runtime_error("socket path exists and is not a socket: " +
                             config_.socket_path);
  }
  if (owns_socket_ &&
      (status.st_dev != socket_device_ || status.st_ino != socket_inode_)) {
    return;
  }
  if (::unlink(config_.socket_path.c_str()) != 0 && errno != ENOENT) {
    throw system_error("unlink socket path");
  }
  owns_socket_ = false;
  socket_device_ = 0;
  socket_inode_ = 0;
}

void UnixJsonServer::run() {
  cleanup_socket_path();

  sockaddr_un address{};
  address.sun_family = AF_UNIX;
  if (config_.socket_path.size() >= sizeof(address.sun_path)) {
    throw std::runtime_error("Unix socket path is too long: " +
                             config_.socket_path);
  }
  std::memcpy(address.sun_path, config_.socket_path.c_str(),
              config_.socket_path.size() + 1);

  Fd listener(vocotype::common::create_socket_close_on_exec(
      AF_UNIX, SOCK_STREAM, 0));
  if (listener.get() < 0) {
    throw system_error("create Unix socket");
  }
  if (::bind(listener.get(), reinterpret_cast<sockaddr *>(&address),
             sizeof(address)) != 0) {
    throw system_error("bind Unix socket");
  }
  if (::chmod(config_.socket_path.c_str(), 0600) != 0) {
    throw system_error("chmod Unix socket");
  }
  struct stat bound_status{};
  if (::lstat(config_.socket_path.c_str(), &bound_status) != 0) {
    throw system_error("lstat bound Unix socket");
  }
  socket_device_ = bound_status.st_dev;
  socket_inode_ = bound_status.st_ino;
  owns_socket_ = true;
  if (::listen(listener.get(), 16) != 0) {
    throw system_error("listen on Unix socket");
  }

  running_.store(true);
  while (running_.load()) {
    pollfd descriptor{listener.get(), POLLIN, 0};
    const int poll_result = ::poll(&descriptor, 1, 100);
    if (poll_result < 0) {
      if (errno == EINTR) {
        continue;
      }
      throw system_error("poll Unix socket");
    }
    if (poll_result == 0 || (descriptor.revents & POLLIN) == 0) {
      continue;
    }
    const int client =
        vocotype::common::accept_close_on_exec(listener.get());
    if (client < 0) {
      if (errno == EINTR) {
        continue;
      }
      throw system_error("accept Unix socket client");
    }
    client_started();
    std::thread([this, client] { handle_client(client); }).detach();
  }

  wait_for_clients();
  cleanup_socket_path();
}

void UnixJsonServer::stop() noexcept { running_.store(false); }

void UnixJsonServer::client_started() {
  std::lock_guard lock(clients_mutex_);
  ++active_clients_;
}

void UnixJsonServer::client_finished() {
  std::lock_guard lock(clients_mutex_);
  if (active_clients_ > 0) {
    --active_clients_;
  }
  clients_cv_.notify_all();
}

void UnixJsonServer::wait_for_clients() noexcept {
  std::unique_lock lock(clients_mutex_);
  clients_cv_.wait(lock, [this] { return active_clients_ == 0; });
}

std::string UnixJsonServer::read_request(int client_fd) const {
  const timeval timeout{
      config_.request_timeout_ms / 1000,
      (config_.request_timeout_ms % 1000) * 1000,
  };
  ::setsockopt(client_fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
  ::setsockopt(client_fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));

  std::string request;
  request.reserve(4096);
  char buffer[8192];
  while (true) {
    const ssize_t count = ::recv(client_fd, buffer, sizeof(buffer), 0);
    if (count == 0) {
      break;
    }
    if (count < 0) {
      if (errno == EINTR) {
        continue;
      }
      if (errno == EAGAIN || errno == EWOULDBLOCK) {
        throw std::runtime_error("request_timeout");
      }
      throw system_error("receive request");
    }
    request.append(buffer, static_cast<std::size_t>(count));
    if (request.size() > config_.max_request_bytes) {
      throw std::runtime_error("request_too_large");
    }
    if (Json::accept(request)) {
      break;
    }
  }
  return request;
}

void UnixJsonServer::send_response(int client_fd, const std::string &response) {
  std::size_t sent = 0;
  while (sent < response.size()) {
    const ssize_t count = vocotype::common::send_without_sigpipe(
        client_fd, response.data() + sent, response.size() - sent);
    if (count < 0) {
      if (errno == EINTR) {
        continue;
      }
      throw system_error("send response");
    }
    sent += static_cast<std::size_t>(count);
  }
}

void UnixJsonServer::handle_client(int client_fd) noexcept {
  Fd client(client_fd);
  try {
    const std::string raw = read_request(client.get());
    if (!raw.empty()) {
      Json response;
      bool stop_after_response = false;
      try {
        const Json request = Json::parse(raw);
        if (request.is_object() && request.value("type", "") == "core_stop") {
          response = {{"success", true}, {"stopping", true}};
          stop_after_response = true;
        } else {
          response = dispatcher_.dispatch(request);
        }
      } catch (const Json::parse_error &) {
        response = {{"success", false}, {"error", "invalid_json"}};
      } catch (const Json::exception &error) {
        response = {{"success", false},
                    {"error", "invalid_request"},
                    {"details", error.what()}};
      }
      send_response(client.get(), response.dump());
      if (stop_after_response)
        stop();
    }
  } catch (const std::exception &error) {
    try {
      send_response(client.get(), Json({
                                           {"success", false},
                                           {"error", error.what()},
                                       })
                                      .dump());
    } catch (...) {
    }
  }
  client_finished();
}

} // namespace vocotype::core
