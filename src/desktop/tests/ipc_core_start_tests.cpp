#include "vocotype/desktop/ipc.hpp"

#include <chrono>
#include <cerrno>
#include <sys/wait.h>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <unistd.h>

namespace {

bool require(bool condition, const std::string &message) {
  if (!condition)
    std::cerr << "FAIL: " << message << '\n';
  return condition;
}

std::filesystem::path make_temp_directory() {
  std::string pattern = "/tmp/vocotype-ipc-start-XXXXXX";
  char *created = ::mkdtemp(pattern.data());
  if (!created)
    throw std::runtime_error("mkdtemp failed");
  return created;
}

bool wait_for_socket_removal(const std::filesystem::path &socket) {
  for (int attempt = 0; attempt < 50; ++attempt) {
    if (!std::filesystem::exists(socket))
      return true;
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
  }
  return !std::filesystem::exists(socket);
}

} // namespace

int main(int argc, char **argv) {
  if (argc != 2) {
    std::cerr << "usage: ipc_core_start_tests FAKE_CORE\n";
    return 2;
  }

  const std::filesystem::path fake_core =
      std::filesystem::canonical(argv[1]);
  const std::filesystem::path temporary = make_temp_directory();
  const std::filesystem::path home = temporary / "home";
  const std::filesystem::path bin = temporary / "bin";
  std::filesystem::create_directories(home);
  std::filesystem::create_directories(bin);

  const char *old_path_value = std::getenv("PATH");
  const std::string old_path = old_path_value ? old_path_value : "";
  const std::string path = bin.string() + (old_path.empty() ? "" : ":" + old_path);
  ::setenv("HOME", home.c_str(), 1);
  ::setenv("PATH", path.c_str(), 1);
  // Force executable discovery into this fixture. Developer machines may
  // already have /usr/lib/vocotype/vocotype-core installed, and production
  // discovery intentionally prefers known package paths over PATH. Without an
  // isolated runtime root this test can silently launch the installed core
  // instead of the fake executable below.
  ::setenv("VOCOTYPE_RUNTIME_DIR", temporary.c_str(), 1);
  ::unsetenv("VOCOTYPE_CONFIG");

  bool success = true;

  const std::filesystem::path executable = bin / "vocotype-core";
  std::filesystem::create_symlink(fake_core, executable);
  const std::string success_socket =
      "/tmp/vocotype-ipc-success-" + std::to_string(::getpid()) + ".sock";
  std::filesystem::remove(success_socket);

  const auto cold_start = vocotype::desktop::ensure_native_core_status(
      success_socket, {}, 5000);
  success &= require(cold_start.ready,
                     "cold-start fixture should become ready: " +
                         cold_start.error);
  if (cold_start.ready) {
    const auto ping = vocotype::desktop::unix_json_request(
        success_socket, {{"type", "ping"}}, 1000);
    success &= require(ping.value("success", false) &&
                           ping.value("pong", false) &&
                           ping.value("backend", "") == "cpp",
                       "cold-started core should answer ping");
    (void)vocotype::desktop::unix_json_request(
        success_socket, {{"type", "shutdown"}}, 1000);
    success &= require(wait_for_socket_removal(success_socket),
                       "fake core should remove its socket on shutdown");
  }

  std::filesystem::remove(executable);
  {
    std::ofstream script(executable);
    script << "#!/bin/sh\n"
              "echo 'fixture startup failure' >&2\n"
              "exit 42\n";
  }
  std::filesystem::permissions(
      executable,
      std::filesystem::perms::owner_read |
          std::filesystem::perms::owner_write |
          std::filesystem::perms::owner_exec,
      std::filesystem::perm_options::replace);

  const std::string failure_socket =
      "/tmp/vocotype-ipc-failure-" + std::to_string(::getpid()) + ".sock";
  std::filesystem::remove(failure_socket);
  const auto failed_start = vocotype::desktop::ensure_native_core_status(
      failure_socket, {}, 3000);
  success &= require(!failed_start.ready,
                     "failing fixture must not be reported ready");
  success &= require(failed_start.exit_code == 42,
                     "failing fixture exit code should be preserved");
  success &= require(
      failed_start.error.find("fixture startup failure") != std::string::npos,
      "startup stderr should be preserved in the failure");
  success &= require(
      vocotype::desktop::native_core_last_error(failure_socket) ==
          failed_start.error,
      "last startup error should remain associated with the socket");

  try {
    (void)vocotype::desktop::unix_json_request(
        failure_socket, {{"type", "transcribe"}}, 100);
    success &= require(false, "request after failed cold-start must fail");
  } catch (const std::exception &error) {
    const std::string message = error.what();
    success &= require(
        message.find("fixture startup failure") != std::string::npos,
        "request should surface the original startup failure");
    success &= require(message.find("No such file") == std::string::npos,
                       "request must not degrade to ENOENT after startup failure");
  }

  std::filesystem::remove(failure_socket);

  // Exit 0 means the singleton lock belongs to another Core that may still
  // be starting. The client should discover its socket rather than failing.
  {
    std::ofstream script(executable);
    script << "#!/bin/sh\nexit 0\n";
  }
  const std::string race_socket =
      "/tmp/vocotype-ipc-race-" + std::to_string(::getpid()) + ".sock";
  std::filesystem::remove(race_socket);
  std::thread delayed_owner([&] {
    std::this_thread::sleep_for(std::chrono::milliseconds(400));
    const pid_t owner = ::fork();
    if (owner == 0) {
      ::execl(fake_core.c_str(), fake_core.c_str(),
              "--socket-path", race_socket.c_str(), static_cast<char *>(nullptr));
      ::_exit(127);
    }
    if (owner > 0) {
      int status = 0;
      while (::waitpid(owner, &status, 0) < 0 && errno == EINTR) {}
    }
  });
  const auto competing = vocotype::desktop::ensure_native_core_status(
      race_socket, {}, 3000);
  success &= require(competing.ready,
                     "duplicate exit 0 should wait for socket from the lock owner: " +
                         competing.error);
  if (competing.ready)
    (void)vocotype::desktop::unix_json_request(
        race_socket, {{"type", "shutdown"}}, 1000);
  delayed_owner.join();

  const std::string orphan_socket =
      "/tmp/vocotype-ipc-orphan-" + std::to_string(::getpid()) + ".sock";
  const auto missing_owner = vocotype::desktop::ensure_native_core_status(
      orphan_socket, {}, 500);
  success &= require(!missing_owner.ready && missing_owner.exit_code == 0 &&
                         missing_owner.error.find("singleton lock held") !=
                             std::string::npos,
                     "exit 0 with no reachable Core must give a clear bounded error");

  std::filesystem::remove_all(temporary);
  if (!success)
    return 1;
  std::cout << "IPC_CORE_COLD_START_OK\n";
  return 0;
}
