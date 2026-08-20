#include <cerrno>
#include <csignal>
#include <cstring>
#include <string>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

namespace {
volatile sig_atomic_t running = 1;
void stop(int) { running = 0; }
}

int main(int argc, char **argv) {
  std::string socket_path;
  for (int index = 1; index + 1 < argc; ++index) {
    if (std::string(argv[index]) == "--socket-path") {
      socket_path = argv[index + 1];
      break;
    }
  }
  if (socket_path.empty())
    return 2;

  std::signal(SIGTERM, stop);
  std::signal(SIGINT, stop);
  (void)::unlink(socket_path.c_str());

  const int listener = ::socket(AF_UNIX, SOCK_STREAM, 0);
  if (listener < 0)
    return 3;
  sockaddr_un address{};
  address.sun_family = AF_UNIX;
  if (socket_path.size() >= sizeof(address.sun_path)) {
    ::close(listener);
    return 4;
  }
  std::memcpy(address.sun_path, socket_path.c_str(), socket_path.size() + 1);
  if (::bind(listener, reinterpret_cast<sockaddr *>(&address), sizeof(address)) !=
      0) {
    ::close(listener);
    return 5;
  }
  if (::listen(listener, 8) != 0) {
    ::close(listener);
    (void)::unlink(socket_path.c_str());
    return 6;
  }

  while (running) {
    const int client = ::accept(listener, nullptr, nullptr);
    if (client < 0) {
      if (errno == EINTR)
        continue;
      break;
    }
    char buffer[4096];
    const ssize_t count = ::recv(client, buffer, sizeof(buffer), 0);
    const std::string request =
        count > 0 ? std::string(buffer, static_cast<std::size_t>(count))
                  : std::string();
    const bool shutdown = request.find("\"type\":\"shutdown\"") !=
                          std::string::npos;
    const std::string response = shutdown
                                     ? "{\"success\":true}"
                                     : "{\"success\":true,\"pong\":true,\"backend\":\"cpp\"}";
    (void)::send(client, response.data(), response.size(), 0);
    ::close(client);
    if (shutdown)
      running = 0;
  }

  ::close(listener);
  (void)::unlink(socket_path.c_str());
  return 0;
}
