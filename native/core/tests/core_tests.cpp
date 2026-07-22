#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <algorithm>
#include <chrono>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>

#include "vocotype/core/config.hpp"
#include "vocotype/core/dispatcher.hpp"
#include "vocotype/core/server.hpp"

namespace {

using vocotype::core::AppConfig;
using vocotype::core::CoreDispatcher;
using vocotype::core::Json;
using vocotype::core::UnixJsonServer;

void require(bool condition, const std::string &message) {
  if (!condition) {
    throw std::runtime_error(message);
  }
}

std::string request_socket(const std::string &path,
                           const std::string &request) {
  const int descriptor = ::socket(AF_UNIX, SOCK_STREAM, 0);
  if (descriptor < 0) {
    throw std::runtime_error("failed to create client socket");
  }
  sockaddr_un address{};
  address.sun_family = AF_UNIX;
  std::copy(path.begin(), path.end(), address.sun_path);
  if (::connect(descriptor, reinterpret_cast<sockaddr *>(&address),
                sizeof(address)) != 0) {
    ::close(descriptor);
    throw std::runtime_error("failed to connect client socket");
  }
  if (::send(descriptor, request.data(), request.size(), 0) < 0) {
    ::close(descriptor);
    throw std::runtime_error("failed to send request");
  }
  std::string response;
  char buffer[4096];
  while (true) {
    const ssize_t count = ::recv(descriptor, buffer, sizeof(buffer), 0);
    if (count < 0) {
      ::close(descriptor);
      throw std::runtime_error("failed to receive response");
    }
    if (count == 0) {
      break;
    }
    response.append(buffer, static_cast<std::size_t>(count));
  }
  ::close(descriptor);
  return response;
}

void test_config_merge() {
  const Json merged = vocotype::core::deep_merge(
      vocotype::core::default_config_json(),
      {{"slm", {{"enabled", true}, {"model", "test-model"}}}});
  const AppConfig config = vocotype::core::parse_config(merged);
  require(config.slm.enabled, "SLM enabled override was lost");
  require(config.slm.model == "test-model", "SLM model override was lost");
  require(config.slm.timeout_ms == 20000,
          "SLM default timeout was not preserved");
}

void test_dispatcher() {
  AppConfig config = vocotype::core::parse_config(Json::object());
  CoreDispatcher dispatcher(config);
  const Json ping = dispatcher.dispatch({{"type", "ping"}});
  require(ping.value("pong", false), "ping did not return pong");
  require(ping.value("backend", "") == "cpp", "ping backend is not cpp");

  const Json polished = dispatcher.dispatch({
      {"type", "polish_text"},
      {"text", "原文"},
  });
  require(polished.value("success", false), "disabled polish should succeed");
  require(polished.value("text", "") == "原文", "disabled polish changed text");
  require(polished.value("reason", "") == "disabled", "wrong disabled reason");

  const Json transcribe = dispatcher.dispatch({{"type", "transcribe"}});
  require(!transcribe.value("success", true),
          "unimplemented ASR reported success");
  require(transcribe.value("error", "") == "native_final_asr_not_connected",
          "ASR boundary error is not explicit");
}

void test_socket_server() {
  AppConfig config = vocotype::core::parse_config(Json::object());
  config.server.socket_path =
      "/tmp/vocotype-core-test-" + std::to_string(::getpid()) + ".sock";
  CoreDispatcher dispatcher(config);
  UnixJsonServer server(config.server, dispatcher);
  std::exception_ptr server_error;
  std::thread thread([&] {
    try {
      server.run();
    } catch (...) {
      server_error = std::current_exception();
    }
  });

  for (int attempt = 0; attempt < 100; ++attempt) {
    if (std::filesystem::exists(config.server.socket_path)) {
      break;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
  require(std::filesystem::exists(config.server.socket_path),
          "server socket was not created");

  const Json response = Json::parse(
      request_socket(config.server.socket_path, R"({"type":"ping"})"));
  require(response.value("pong", false), "socket ping failed");

  server.stop();
  thread.join();
  if (server_error) {
    std::rethrow_exception(server_error);
  }
  require(!std::filesystem::exists(config.server.socket_path),
          "server socket was not removed");
}

} // namespace

int main() {
  try {
    test_config_merge();
    test_dispatcher();
    test_socket_server();
    std::cout << "vocotype-core tests passed\n";
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "vocotype-core test failure: " << error.what() << '\n';
    return 1;
  }
}
