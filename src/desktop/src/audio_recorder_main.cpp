#include "vocotype/desktop/audio.hpp"
#include "vocotype/desktop/config.hpp"
#include "vocotype/desktop/ipc.hpp"
#include "vocotype/desktop/wav.hpp"
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <deque>
#include <filesystem>
#include <iostream>
#include <mutex>
#include <poll.h>
#include <string>
#include <thread>
#include <unistd.h>
using namespace vocotype::desktop;
namespace {
std::atomic_bool *global_stop = nullptr;
void signal_handler(int) {
  if (global_stop)
    global_stop->store(true);
}
std::mutex output_mutex;
void emit(const Json &value) {
  std::lock_guard lock(output_mutex);
  std::cout << value.dump() << '\n' << std::flush;
}
struct Options {
  bool list_devices = false;
  bool probe = false;
  int duration_ms = 0;
  std::filesystem::path config;
  std::string socket = backend_socket_path();
};
Options parse(int argc, char **argv) {
  Options options;
  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    if (arg == "--list-devices")
      options.list_devices = true;
    else if (arg == "--probe")
      options.probe = true;
    else if (arg == "--duration-ms" && i + 1 < argc)
      options.duration_ms = std::stoi(argv[++i]);
    else if (arg == "--config" && i + 1 < argc)
      options.config = argv[++i];
    else if (arg == "--socket" && i + 1 < argc)
      options.socket = argv[++i];
    else if (arg == "--help") {
      std::cout << "Usage: vocotype-audio-recorder [--list-devices|--probe] "
                   "[--duration-ms N] [--config PATH] [--socket PATH]\n";
      std::exit(0);
    } else
      throw std::runtime_error("unknown argument: " + arg);
  }
  return options;
}
} // namespace
int main(int argc, char **argv) {
  try {
    const Options options = parse(argc, argv);
    if (options.list_devices || options.probe) {
      Json devices = Json::array();
      for (const auto &device : list_input_devices()) {
        devices.push_back({{"id", device.id},
                           {"name", device.name},
                           {"channels", device.max_input_channels},
                           {"sample_rate", device.default_sample_rate},
                           {"default", device.is_default}});
      }
      emit({{"type", options.probe ? "probe" : "devices"},
            {"success", true},
            {"devices", devices}});
      return 0;
    }
    AudioConfig config = load_audio_config(options.config);
    const AudioDevice device = resolve_input_device(config);
    const int sample_rate = resolve_sample_rate(device, config.sample_rate);
    std::atomic_bool stop{false};
    global_stop = &stop;
    std::signal(SIGINT, signal_handler);
    std::signal(SIGTERM, signal_handler);

    std::mutex samples_mutex;
    std::vector<std::int16_t> samples;
    std::mutex queue_mutex;
    std::condition_variable queue_cv;
    std::deque<std::vector<std::int16_t>> preview_queue;
    bool preview_done = false;
    std::string preview_session;
    std::atomic_bool preview_active{false};
    int preview_chunk_samples = 9600;
    try {
      const Json started = unix_json_request(
          options.socket, {{"type", "asr_preview_start"}}, 12000);
      if (started.value("success", false)) {
        preview_session = started.value("session_id", "");
        preview_active.store(!preview_session.empty());
        preview_chunk_samples =
            std::max(1600, started.value("chunk_samples", 9600));
      }
    } catch (const std::exception &) {
    }

    std::thread preview_thread([&] {
      std::vector<std::int16_t> pending;
      while (true) {
        std::vector<std::int16_t> block;
        {
          std::unique_lock lock(queue_mutex);
          queue_cv.wait(lock,
                        [&] { return preview_done || !preview_queue.empty(); });
          if (!preview_queue.empty()) {
            block = std::move(preview_queue.front());
            preview_queue.pop_front();
          } else if (preview_done) {
            break;
          }
        }
        pending.insert(pending.end(), block.begin(), block.end());
        while (!preview_session.empty() &&
               static_cast<int>(pending.size()) >= preview_chunk_samples) {
          std::vector<std::int16_t> chunk(
              pending.begin(), pending.begin() + preview_chunk_samples);
          pending.erase(pending.begin(),
                        pending.begin() + preview_chunk_samples);
          try {
            const auto bytes =
                reinterpret_cast<const unsigned char *>(chunk.data());
            const Json response = unix_json_request(
                options.socket,
                {{"type", "asr_preview_feed"},
                 {"session_id", preview_session},
                 {"pcm16",
                  base64_encode(bytes, chunk.size() * sizeof(std::int16_t))},
                 {"is_final", false}},
                2500);
            const std::string text = response.value("text", "");
            if (response.value("success", false) && !text.empty())
              emit({{"type", "partial"}, {"text", text}});
          } catch (const std::exception &) {
            preview_session.clear();
            preview_active.store(false);
          }
        }
      }
      if (!preview_session.empty()) {
        try {
          if (!pending.empty()) {
            const auto bytes =
                reinterpret_cast<const unsigned char *>(pending.data());
            const Json response = unix_json_request(
                options.socket,
                {{"type", "asr_preview_feed"},
                 {"session_id", preview_session},
                 {"pcm16",
                  base64_encode(bytes, pending.size() * sizeof(std::int16_t))},
                 {"is_final", true}},
                2500);
            const std::string text = response.value("text", "");
            if (response.value("success", false) && !text.empty())
              emit({{"type", "partial"}, {"text", text}});
          }
          (void)unix_json_request(options.socket,
                                  {{"type", "asr_preview_close"},
                                   {"session_id", preview_session},
                                   {"flush", false}},
                                  2500);
        } catch (const std::exception &) {
        }
      }
    });

    AudioCapture capture(device, sample_rate, config.block_ms);
    std::string capture_error;
    std::thread capture_thread([&] {
      try {
        capture.run(stop, [&](const std::vector<std::int16_t> &block) {
          {
            std::lock_guard lock(samples_mutex);
            samples.insert(samples.end(), block.begin(), block.end());
          }
          if (preview_active.load(std::memory_order_relaxed)) {
            auto converted = resample_linear(block, sample_rate, 16000);
            std::lock_guard lock(queue_mutex);
            if (preview_queue.size() < 32)
              preview_queue.push_back(std::move(converted));
            queue_cv.notify_one();
          }
        });
      } catch (const std::exception &error) {
        capture_error = error.what();
        stop.store(true);
      }
    });

    const auto started_at = std::chrono::steady_clock::now();
    while (!stop.load()) {
      if (options.duration_ms > 0 &&
          std::chrono::steady_clock::now() - started_at >=
              std::chrono::milliseconds(options.duration_ms))
        break;
      if (options.duration_ms > 0) {
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
        continue;
      }
      pollfd descriptor{STDIN_FILENO,
                        static_cast<short>(POLLIN | POLLHUP | POLLERR), 0};
      const int result = poll(&descriptor, 1, 50);
      if (result > 0 && (descriptor.revents & (POLLHUP | POLLERR)))
        break;
      if (result > 0 && (descriptor.revents & POLLIN)) {
        char discard[64];
        const ssize_t count = read(STDIN_FILENO, discard, sizeof(discard));
        if (count == 0)
          break;
      }
    }
    stop.store(true);
    capture_thread.join();
    {
      std::lock_guard lock(queue_mutex);
      preview_done = true;
    }
    queue_cv.notify_all();
    preview_thread.join();
    if (!capture_error.empty())
      throw std::runtime_error(capture_error);

    std::vector<std::int16_t> finished;
    {
      std::lock_guard lock(samples_mutex);
      finished = std::move(samples);
    }
    if (finished.empty())
      throw std::runtime_error("recording produced no audio samples");
    const auto path = create_secure_wav_path();
    write_pcm16_wav(path, finished, sample_rate);
    emit({{"type", "audio"},
          {"path", path.string()},
          {"sample_rate", sample_rate},
          {"frames", finished.size()},
          {"device_id", device.id},
          {"device_name", device.name}});
    return 0;
  } catch (const std::exception &error) {
    emit({{"type", "error"}, {"error", error.what()}});
    return 1;
  }
}
