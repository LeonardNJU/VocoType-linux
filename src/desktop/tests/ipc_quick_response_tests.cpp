#include "vocotype/desktop/ipc.hpp"

#include <nlohmann/json.hpp>

#include <atomic>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <filesystem>
#include <iostream>
#include <string>
#include <thread>

#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

namespace {
using Json = nlohmann::json;

class Fd {
public:
  explicit Fd(int value = -1) : value_(value) {}
  Fd(const Fd &) = delete;
  Fd &operator=(const Fd &) = delete;
  ~Fd() {
    if (value_ >= 0)
      ::close(value_);
  }
  int get() const { return value_; }
private:
  int value_;
};

void require(bool condition, const char *message) {
  if (!condition) {
    std::cerr << "FAIL: " << message << '\n';
    std::exit(1);
  }
}

void send_all(int fd, const std::string &value) {
  std::size_t offset = 0;
  while (offset < value.size()) {
    const auto count = ::send(fd, value.data() + offset,
                              value.size() - offset, 0);
    if (count < 0 && errno == EINTR)
      continue;
    require(count > 0, "quick server response send failed");
    offset += static_cast<std::size_t>(count);
  }
}
} // namespace

int main() {
  const auto socket_path =
      std::filesystem::temp_directory_path() /
      ("vocotype-ipc-quick-response-" + std::to_string(::getpid()) + ".sock");
  std::filesystem::remove(socket_path);

  Fd listener(::socket(AF_UNIX, SOCK_STREAM, 0));
  require(listener.get() >= 0, "create quick response socket");
  sockaddr_un address{};
  address.sun_family = AF_UNIX;
  const std::string path = socket_path.string();
  require(path.size() < sizeof(address.sun_path), "test socket path too long");
  std::memcpy(address.sun_path, path.c_str(), path.size() + 1);
  require(::bind(listener.get(), reinterpret_cast<sockaddr *>(&address),
                 sizeof(address)) == 0,
          "bind quick response socket");
  require(::listen(listener.get(), 8) == 0, "listen quick response socket");

  constexpr int kRequests = 100;
  std::atomic_bool server_ok{true};
  std::thread server([&] {
    for (int index = 0; index < kRequests; ++index) {
      Fd client(::accept(listener.get(), nullptr, nullptr));
      if (client.get() < 0) {
        server_ok.store(false);
        return;
      }
      std::string request;
      char buffer[4096];
      while (!Json::accept(request)) {
        const auto count = ::recv(client.get(), buffer, sizeof(buffer), 0);
        if (count <= 0) {
          server_ok.store(false);
          return;
        }
        request.append(buffer, static_cast<std::size_t>(count));
      }
      Json parsed = Json::parse(request);
      send_all(client.get(), Json{{"success", true},
                                  {"sequence", parsed.value("sequence", -1)}}
                                 .dump());
      // Close immediately after responding. The client must simply read the
      // response; it must not depend on a subsequent write-side half-close.
    }
  });

  for (int index = 0; index < kRequests; ++index) {
    const Json response = vocotype::desktop::unix_json_request(
        path, {{"type", "quick_response"}, {"sequence", index}}, 3000);
    require(response.value("success", false), "quick response request failed");
    require(response.value("sequence", -1) == index,
            "quick response sequence mismatch");
  }
  server.join();
  std::filesystem::remove(socket_path);
  require(server_ok.load(), "quick response server failed");
  std::cout << "IPC quick-response tests passed\n";
}
