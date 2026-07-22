#include <csignal>
#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>

#include "vocotype/core/config.hpp"
#include "vocotype/core/dispatcher.hpp"
#include "vocotype/core/server.hpp"

namespace {

vocotype::core::UnixJsonServer *g_server = nullptr;

void handle_signal(int) {
  if (g_server != nullptr) {
    g_server->stop();
  }
}

struct Options {
  std::filesystem::path config_path = "~/.config/vocotype/fcitx5-backend.json";
  std::string socket_path;
  bool require_config = false;
  bool enable_final_asr = false;
};

Options parse_options(int argc, char **argv) {
  Options options;
  for (int index = 1; index < argc; ++index) {
    const std::string arg = argv[index];
    auto next = [&](const char *flag) -> std::string {
      if (index + 1 >= argc) {
        throw std::runtime_error(std::string("missing value for ") + flag);
      }
      return argv[++index];
    };
    if (arg == "--config") {
      options.config_path = next("--config");
      options.require_config = true;
    } else if (arg == "--socket-path") {
      options.socket_path = next("--socket-path");
    } else if (arg == "--enable-final-asr") {
      options.enable_final_asr = true;
    } else if (arg == "--help") {
      std::cout
          << "Usage: vocotype-core [--config FILE] [--socket-path PATH] "
             "[--enable-final-asr]\n\n"
          << "Native VoCoType backend prototype with compatible Unix JSON "
             "IPC.\n";
      std::exit(0);
    } else {
      throw std::runtime_error("unknown argument: " + arg);
    }
  }
  return options;
}

} // namespace

int main(int argc, char **argv) {
  try {
    const Options options = parse_options(argc, argv);
    vocotype::core::AppConfig config = vocotype::core::load_config(
        options.config_path, !options.require_config);
    if (!options.socket_path.empty()) {
      config.server.socket_path = options.socket_path;
    }
    if (options.enable_final_asr) {
      config.offline_asr.enabled = true;
    }

    vocotype::core::CoreDispatcher dispatcher(config);
    vocotype::core::UnixJsonServer server(config.server, dispatcher);
    g_server = &server;
    std::signal(SIGINT, handle_signal);
    std::signal(SIGTERM, handle_signal);

    std::cerr << "vocotype-core: backend=cpp socket=" << server.socket_path()
              << " slm=" << (config.slm.enabled ? "enabled" : "disabled")
              << '\n';
    server.run();
    g_server = nullptr;
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "vocotype-core: " << error.what() << '\n';
    return 1;
  }
}
