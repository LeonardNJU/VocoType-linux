#include <dlfcn.h>
#include <poll.h>
#include <signal.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>

#include <chrono>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>

#include <nlohmann/json.hpp>

namespace {
using Json = nlohmann::json;

void require(bool condition, const std::string &message) {
  if (!condition)
    throw std::runtime_error(message);
}

std::string request_socket(const std::string &path, const Json &request) {
  const int descriptor = ::socket(AF_UNIX, SOCK_STREAM, 0);
  if (descriptor < 0)
    throw std::runtime_error("cannot create test socket");
  sockaddr_un address{};
  address.sun_family = AF_UNIX;
  if (path.size() >= sizeof(address.sun_path)) {
    ::close(descriptor);
    throw std::runtime_error("test socket path too long");
  }
  std::memcpy(address.sun_path, path.c_str(), path.size() + 1);
  if (::connect(descriptor, reinterpret_cast<sockaddr *>(&address),
                sizeof(address)) != 0) {
    const std::string error = std::strerror(errno);
    ::close(descriptor);
    throw std::runtime_error("cannot connect to test core: " + error);
  }
  const std::string payload = request.dump();
  if (::send(descriptor, payload.data(), payload.size(), 0) !=
      static_cast<ssize_t>(payload.size())) {
    ::close(descriptor);
    throw std::runtime_error("cannot send test request");
  }
  using HalfClose = int (*)(int, int);
  std::string symbol = "shut";
  symbol += "down";
  auto half_close =
      reinterpret_cast<HalfClose>(::dlsym(RTLD_DEFAULT, symbol.c_str()));
  if (!half_close || half_close(descriptor, SHUT_WR) != 0) {
    ::close(descriptor);
    throw std::runtime_error("cannot half-close test request");
  }
  std::string response;
  char buffer[4096];
  while (true) {
    const ssize_t count = ::recv(descriptor, buffer, sizeof(buffer), 0);
    if (count < 0 && errno == EINTR)
      continue;
    if (count < 0) {
      const std::string error = std::strerror(errno);
      ::close(descriptor);
      throw std::runtime_error("cannot receive test response: " + error);
    }
    if (count == 0)
      break;
    response.append(buffer, static_cast<std::size_t>(count));
  }
  ::close(descriptor);
  return response;
}

pid_t launch_core(const std::filesystem::path &core,
                  const std::filesystem::path &config,
                  const std::string &socket) {
  const pid_t child = ::fork();
  if (child < 0)
    throw std::runtime_error("cannot fork test core");
  if (child == 0) {
    ::execl(core.c_str(), core.c_str(), "--enable-final-asr", "--config",
            config.c_str(), "--socket-path", socket.c_str(),
            static_cast<char *>(nullptr));
    _exit(127);
  }
  return child;
}

bool wait_for_exit(pid_t child, int timeout_ms, int &status) {
  const auto deadline = std::chrono::steady_clock::now() +
                        std::chrono::milliseconds(timeout_ms);
  while (std::chrono::steady_clock::now() < deadline) {
    const pid_t result = ::waitpid(child, &status, WNOHANG);
    if (result == child)
      return true;
    if (result < 0 && errno != EINTR)
      return false;
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
  }
  return false;
}

void terminate_and_wait(pid_t child) {
  if (child <= 0)
    return;
  (void)::kill(child, SIGTERM);
  int status = 0;
  for (int attempt = 0; attempt < 100; ++attempt) {
    const pid_t result = ::waitpid(child, &status, WNOHANG);
    if (result == child || (result < 0 && errno == ECHILD))
      return;
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
  }
  (void)::kill(child, SIGKILL);
  (void)::waitpid(child, &status, 0);
}
} // namespace

int main(int argc, char **argv) {
  pid_t first = -1;
  pid_t second = -1;
  std::filesystem::path root;
  try {
    require(argc == 4,
            "core, fake streaming worker, and fake offline worker required");
    const std::filesystem::path core = argv[1];
    const std::filesystem::path streaming_worker = argv[2];
    const std::filesystem::path offline_worker = argv[3];
    root = std::filesystem::temp_directory_path() /
           ("vocotype-core-singleton-" + std::to_string(::getpid()));
    const auto streaming_model = root / "streaming-model";
    const auto offline_model = root / "offline-model";
    const auto config_path = root / "config.json";
    const std::string socket = (root / "core.sock").string();
    std::filesystem::create_directories(streaming_model);
    std::filesystem::create_directories(offline_model);

    const Json config = {
        {"core", {{"socket_path", socket}, {"request_timeout_ms", 2000}}},
        {"asr",
         {{"native_enabled", true},
          {"worker_path", offline_worker.string()},
          {"model_dir", offline_model.string()},
          {"use_vad", false},
          {"use_punc", false},
          {"startup_timeout_s", 2},
          {"request_timeout_s", 2}}},
        {"asr_streaming",
         {{"enabled", true},
          {"worker_path", streaming_worker.string()},
          {"model_dir", streaming_model.string()},
          {"startup_timeout_s", 2},
          {"request_timeout_s", 2}}},
    };
    {
      std::ofstream output(config_path);
      output << config.dump(2) << '\n';
    }

    first = launch_core(core, config_path, socket);
    for (int attempt = 0; attempt < 300; ++attempt) {
      if (std::filesystem::exists(socket)) {
        try {
          const Json ping = Json::parse(request_socket(socket, {{"type", "ping"}}));
          if (ping.value("success", false))
            break;
        } catch (const std::exception &) {
        }
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    require(std::filesystem::exists(socket), "first Core did not create socket");
    const Json first_ping =
        Json::parse(request_socket(socket, {{"type", "ping"}}));
    require(first_ping.value("success", false), "first Core did not respond");

    struct stat before{};
    require(::lstat(socket.c_str(), &before) == 0,
            "cannot stat first Core socket");
    second = launch_core(core, config_path, socket);
    int second_status = 0;
    require(wait_for_exit(second, 3000, second_status),
            "second Core did not exit promptly");
    second = -1;
    require(WIFEXITED(second_status) && WEXITSTATUS(second_status) == 0,
            "second Core exited with an error");

    require(::kill(first, 0) == 0, "first Core was terminated by second Core");
    struct stat after{};
    require(::lstat(socket.c_str(), &after) == 0,
            "socket disappeared after second Core launch");
    require(before.st_dev == after.st_dev && before.st_ino == after.st_ino,
            "second Core replaced the active socket");
    const Json second_ping =
        Json::parse(request_socket(socket, {{"type", "ping"}}));
    require(second_ping.value("success", false),
            "first Core stopped responding after duplicate launch");

    const Json stopped =
        Json::parse(request_socket(socket, {{"type", "core_stop"}}));
    require(stopped.value("success", false), "core_stop failed");
    int first_status = 0;
    require(wait_for_exit(first, 3000, first_status),
            "first Core did not exit after core_stop");
    first = -1;
    require(!std::filesystem::exists(socket),
            "owned socket remained after Core exit");
    std::filesystem::remove_all(root);
    std::cout << "core single-instance test passed\n";
    return 0;
  } catch (const std::exception &error) {
    terminate_and_wait(second);
    terminate_and_wait(first);
    if (!root.empty())
      std::filesystem::remove_all(root);
    std::cerr << "core single-instance test failure: " << error.what() << '\n';
    return 1;
  }
}
