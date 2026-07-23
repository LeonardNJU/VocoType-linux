#pragma once

#include <atomic>
#include <condition_variable>
#include <cstddef>
#include <mutex>
#include <string>

#include "vocotype/core/config.hpp"
#include "vocotype/core/dispatcher.hpp"

namespace vocotype::core {

class UnixJsonServer {
public:
  UnixJsonServer(ServerConfig config, const CoreDispatcher &dispatcher);
  UnixJsonServer(const UnixJsonServer &) = delete;
  UnixJsonServer &operator=(const UnixJsonServer &) = delete;
  ~UnixJsonServer();

  void run();
  void stop() noexcept;
  [[nodiscard]] const std::string &socket_path() const noexcept;

private:
  void cleanup_socket_path() const;
  void handle_client(int client_fd) noexcept;
  [[nodiscard]] std::string read_request(int client_fd) const;
  static void send_response(int client_fd, const std::string &response);
  void client_started();
  void client_finished();
  void wait_for_clients() noexcept;

  ServerConfig config_;
  const CoreDispatcher &dispatcher_;
  std::atomic<bool> running_{false};
  std::mutex clients_mutex_;
  std::condition_variable clients_cv_;
  std::size_t active_clients_ = 0;
};

} // namespace vocotype::core
