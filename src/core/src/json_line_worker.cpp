#include "vocotype/core/json_line_worker.hpp"

#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <sys/wait.h>
#include <unistd.h>

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <stdexcept>
#include <thread>
#include <utility>

namespace vocotype::core {
namespace {

constexpr std::size_t kMaxResponseBytes = 1024U * 1024U;

std::runtime_error system_error(const std::string &action) {
  return std::runtime_error(action + ": " + std::strerror(errno));
}

void close_fd(int &descriptor) noexcept {
  if (descriptor >= 0) {
    ::close(descriptor);
    descriptor = -1;
  }
}

Json error_response(const std::string &error) {
  return {{"success", false}, {"error", error}};
}

} // namespace

JsonLineWorker::JsonLineWorker() {
  static std::once_flag sigpipe_once;
  std::call_once(sigpipe_once, [] {
    struct sigaction action{};
    action.sa_handler = SIG_IGN;
    ::sigemptyset(&action.sa_mask);
    (void)::sigaction(SIGPIPE, &action, nullptr);
  });
}

JsonLineWorker::~JsonLineWorker() { stop(); }

bool JsonLineWorker::alive_locked() noexcept {
  if (pid_ <= 0) {
    return false;
  }
  int status = 0;
  pid_t result = -1;
  do {
    result = ::waitpid(pid_, &status, WNOHANG);
  } while (result < 0 && errno == EINTR);
  if (result == 0) {
    return true;
  }
  reset_locked();
  return false;
}

bool JsonLineWorker::ready() noexcept {
  std::lock_guard lock(mutex_);
  return ready_ && alive_locked();
}

Json JsonLineWorker::start(const std::filesystem::path &executable,
                           const std::vector<std::string> &arguments,
                           int startup_timeout_ms) {
  std::lock_guard lock(mutex_);
  if (ready_ && alive_locked()) {
    return ready_response_;
  }
  stop_locked();

  try {
    int input_pipe[2] = {-1, -1};
    int output_pipe[2] = {-1, -1};
    if (::pipe2(input_pipe, O_CLOEXEC) != 0) {
      throw system_error("create worker input pipe");
    }
    if (::pipe2(output_pipe, O_CLOEXEC) != 0) {
      ::close(input_pipe[0]);
      ::close(input_pipe[1]);
      throw system_error("create worker output pipe");
    }

    const pid_t child = ::fork();
    if (child < 0) {
      ::close(input_pipe[0]);
      ::close(input_pipe[1]);
      ::close(output_pipe[0]);
      ::close(output_pipe[1]);
      throw system_error("fork worker");
    }
    if (child == 0) {
      if (::dup2(input_pipe[0], STDIN_FILENO) < 0 ||
          ::dup2(output_pipe[1], STDOUT_FILENO) < 0) {
        _exit(126);
      }
      ::close(input_pipe[0]);
      ::close(input_pipe[1]);
      ::close(output_pipe[0]);
      ::close(output_pipe[1]);

      std::vector<std::string> values;
      values.reserve(arguments.size() + 1U);
      values.push_back(executable.string());
      values.insert(values.end(), arguments.begin(), arguments.end());
      std::vector<char *> argv;
      argv.reserve(values.size() + 1U);
      for (auto &value : values) {
        argv.push_back(value.data());
      }
      argv.push_back(nullptr);
      ::execv(executable.c_str(), argv.data());
      _exit(127);
    }

    ::close(input_pipe[0]);
    ::close(output_pipe[1]);
    pid_ = child;
    input_fd_ = input_pipe[1];
    output_fd_ = output_pipe[0];
    read_buffer_.clear();

    Json response = Json::parse(read_line_locked(startup_timeout_ms));
    if (response.value("type", "") != "ready" ||
        !response.value("success", false)) {
      const std::string error = response.value("error", "worker_failed");
      stop_locked();
      return error_response(error);
    }
    ready_ = true;
    ready_response_ = response;
    return response;
  } catch (const std::exception &error) {
    stop_locked();
    return error_response(error.what());
  }
}

Json JsonLineWorker::request(const Json &payload, int timeout_ms) {
  std::lock_guard lock(mutex_);
  if (!ready_ || !alive_locked()) {
    return error_response("worker_not_ready");
  }
  try {
    write_line_locked(payload.dump(), timeout_ms);
    return Json::parse(read_line_locked(timeout_ms));
  } catch (const std::exception &error) {
    stop_locked();
    return error_response(error.what());
  }
}

void JsonLineWorker::write_line_locked(const std::string &line,
                                       int timeout_ms) {
  std::string payload = line;
  payload.push_back('\n');
  const auto deadline = std::chrono::steady_clock::now() +
                        std::chrono::milliseconds(std::max(1, timeout_ms));
  std::size_t offset = 0;
  while (offset < payload.size()) {
    const auto remaining =
        std::chrono::duration_cast<std::chrono::milliseconds>(
            deadline - std::chrono::steady_clock::now());
    if (remaining.count() <= 0) {
      throw std::runtime_error("worker_request_timeout");
    }
    pollfd descriptor{input_fd_, POLLOUT, 0};
    const int result =
        ::poll(&descriptor, 1, static_cast<int>(remaining.count()));
    if (result == 0) {
      throw std::runtime_error("worker_request_timeout");
    }
    if (result < 0) {
      if (errno == EINTR) {
        continue;
      }
      throw system_error("poll worker input");
    }
    const ssize_t count =
        ::write(input_fd_, payload.data() + offset, payload.size() - offset);
    if (count < 0) {
      if (errno == EINTR) {
        continue;
      }
      throw system_error("write worker request");
    }
    offset += static_cast<std::size_t>(count);
  }
}

std::string JsonLineWorker::read_line_locked(int timeout_ms) {
  const auto deadline = std::chrono::steady_clock::now() +
                        std::chrono::milliseconds(std::max(1, timeout_ms));
  while (true) {
    const std::size_t newline = read_buffer_.find('\n');
    if (newline != std::string::npos) {
      std::string line = read_buffer_.substr(0, newline);
      read_buffer_.erase(0, newline + 1U);
      return line;
    }
    if (read_buffer_.size() > kMaxResponseBytes) {
      throw std::runtime_error("worker_response_too_large");
    }

    const auto remaining =
        std::chrono::duration_cast<std::chrono::milliseconds>(
            deadline - std::chrono::steady_clock::now());
    if (remaining.count() <= 0) {
      throw std::runtime_error("worker_request_timeout");
    }
    pollfd descriptor{output_fd_, POLLIN | POLLHUP, 0};
    const int result =
        ::poll(&descriptor, 1, static_cast<int>(remaining.count()));
    if (result == 0) {
      throw std::runtime_error("worker_request_timeout");
    }
    if (result < 0) {
      if (errno == EINTR) {
        continue;
      }
      throw system_error("poll worker output");
    }

    char buffer[8192];
    const ssize_t count = ::read(output_fd_, buffer, sizeof(buffer));
    if (count == 0) {
      throw std::runtime_error("worker_exited");
    }
    if (count < 0) {
      if (errno == EINTR) {
        continue;
      }
      throw system_error("read worker response");
    }
    read_buffer_.append(buffer, static_cast<std::size_t>(count));
  }
}

void JsonLineWorker::reset_locked() noexcept {
  close_fd(input_fd_);
  close_fd(output_fd_);
  pid_ = -1;
  ready_ = false;
  read_buffer_.clear();
  ready_response_ = Json::object();
}

void JsonLineWorker::stop_locked() noexcept {
  if (pid_ <= 0) {
    reset_locked();
    return;
  }

  const pid_t child = pid_;
  if (ready_ && input_fd_ >= 0 && output_fd_ >= 0) {
    try {
      write_line_locked(Json({{"type", "stop"}}).dump(), 200);
      (void)read_line_locked(300);
    } catch (...) {
    }
  }
  close_fd(input_fd_);
  close_fd(output_fd_);

  int status = 0;
  for (int attempt = 0; attempt < 10; ++attempt) {
    const pid_t result = ::waitpid(child, &status, WNOHANG);
    if (result == child || (result < 0 && errno == ECHILD)) {
      reset_locked();
      return;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
  }
  (void)::kill(child, SIGTERM);
  for (int attempt = 0; attempt < 10; ++attempt) {
    const pid_t result = ::waitpid(child, &status, WNOHANG);
    if (result == child || (result < 0 && errno == ECHILD)) {
      reset_locked();
      return;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
  }
  (void)::kill(child, SIGKILL);
  (void)::waitpid(child, &status, 0);
  reset_locked();
}

void JsonLineWorker::stop() noexcept {
  std::lock_guard lock(mutex_);
  stop_locked();
}

} // namespace vocotype::core
