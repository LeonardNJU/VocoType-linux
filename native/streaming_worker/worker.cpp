/**
 * VoCoType native streaming ASR worker.
 *
 * This executable is intentionally a thin process/IPC shell around FunASR's
 * official C++ ONNX runtime.  All online Paraformer frontend, CIF, overlap and
 * FSMN cache behavior remains in upstream libfunasr.
 */

#include <poll.h>
#include <unistd.h>

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <exception>
#include <iostream>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#include <nlohmann/json.hpp>
#include <glog/logging.h>

#include "com-define.h"
#include "funasrruntime.h"

namespace {

using Json = nlohmann::json;
using Clock = std::chrono::steady_clock;

struct Options {
    std::string model_dir;
    int threads = 1;
    std::vector<int> chunk_size{5, 10, 5};
    int idle_timeout_ms = 30000;
    int session_idle_timeout_ms = 15000;
};

struct OnlineSession {
    FUNASR_HANDLE handle = nullptr;
    std::string text;
    Clock::time_point last_activity{};
};

std::string next_arg(int &index, int argc, char **argv, const char *flag) {
    if (index + 1 >= argc) {
        throw std::runtime_error(std::string("missing value for ") + flag);
    }
    return argv[++index];
}

Options parse_options(int argc, char **argv) {
    Options options;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "--model-dir") {
            options.model_dir = next_arg(i, argc, argv, "--model-dir");
        } else if (arg == "--threads") {
            options.threads = std::stoi(next_arg(i, argc, argv, "--threads"));
        } else if (arg == "--chunk-size") {
            options.chunk_size.clear();
            for (int item = 0; item < 3; ++item) {
                options.chunk_size.push_back(
                    std::stoi(next_arg(i, argc, argv, "--chunk-size")));
            }
        } else if (arg == "--idle-timeout-ms") {
            options.idle_timeout_ms =
                std::stoi(next_arg(i, argc, argv, "--idle-timeout-ms"));
        } else if (arg == "--session-idle-timeout-ms") {
            options.session_idle_timeout_ms = std::stoi(
                next_arg(i, argc, argv, "--session-idle-timeout-ms"));
        } else if (arg == "--help") {
            std::cout
                << "Usage: vocotype-streaming-worker --model-dir DIR "
                   "[--threads 1] [--chunk-size 5 10 5] "
                   "[--idle-timeout-ms 30000] "
                   "[--session-idle-timeout-ms 15000]\n";
            std::exit(0);
        } else {
            throw std::runtime_error("unknown argument: " + arg);
        }
    }
    if (options.model_dir.empty()) {
        throw std::runtime_error("--model-dir is required");
    }
    if (options.threads < 1 || options.threads > 4) {
        throw std::runtime_error("--threads must be between 1 and 4");
    }
    if (options.chunk_size.size() != 3 || options.chunk_size[1] <= 0) {
        throw std::runtime_error("--chunk-size requires three valid integers");
    }
    options.idle_timeout_ms = std::max(1000, options.idle_timeout_ms);
    options.session_idle_timeout_ms =
        std::max(2000, options.session_idle_timeout_ms);
    return options;
}

int decode_base64_char(unsigned char value) {
    if (value >= 'A' && value <= 'Z') return value - 'A';
    if (value >= 'a' && value <= 'z') return value - 'a' + 26;
    if (value >= '0' && value <= '9') return value - '0' + 52;
    if (value == '+') return 62;
    if (value == '/') return 63;
    return -1;
}

std::vector<char> decode_base64(const std::string &input) {
    if (input.size() % 4 != 0) {
        throw std::runtime_error("invalid base64 length");
    }
    std::vector<char> output;
    output.reserve(input.size() / 4 * 3);
    for (std::size_t offset = 0; offset < input.size(); offset += 4) {
        int values[4] = {0, 0, 0, 0};
        int padding = 0;
        for (int index = 0; index < 4; ++index) {
            const unsigned char current = input[offset + index];
            if (current == '=') {
                ++padding;
                values[index] = 0;
            } else {
                values[index] = decode_base64_char(current);
                if (values[index] < 0 || padding != 0) {
                    throw std::runtime_error("invalid base64 payload");
                }
            }
        }
        const std::uint32_t packed =
            (static_cast<std::uint32_t>(values[0]) << 18U) |
            (static_cast<std::uint32_t>(values[1]) << 12U) |
            (static_cast<std::uint32_t>(values[2]) << 6U) |
            static_cast<std::uint32_t>(values[3]);
        output.push_back(static_cast<char>((packed >> 16U) & 0xFFU));
        if (padding < 2) output.push_back(static_cast<char>((packed >> 8U) & 0xFFU));
        if (padding < 1) output.push_back(static_cast<char>(packed & 0xFFU));
        if (padding > 2 || (padding != 0 && offset + 4 != input.size())) {
            throw std::runtime_error("invalid base64 padding");
        }
    }
    return output;
}

void emit(Json response) {
    std::cout << response.dump() << '\n' << std::flush;
}

class Worker {
  public:
    explicit Worker(Options options) : options_(std::move(options)) {
        std::map<std::string, std::string> model_path{
            {MODEL_DIR, options_.model_dir},
            {QUANTIZE, "true"},
        };
        model_handle_ =
            FunASRInit(model_path, options_.threads, ASR_ONLINE);
        if (!model_handle_) {
            throw std::runtime_error("FunASRInit returned null");
        }
        last_activity_ = Clock::now();
    }

    Worker(const Worker &) = delete;
    Worker &operator=(const Worker &) = delete;

    ~Worker() {
        for (auto &[id, session] : sessions_) {
            if (session.handle) FunASRUninit(session.handle);
        }
        sessions_.clear();
        if (model_handle_) FunASRUninit(model_handle_);
    }

    int run() {
        emit({{"type", "ready"},
              {"success", true},
              {"sample_rate", 16000},
              {"chunk_samples", options_.chunk_size[1] * 960}});

        while (true) {
            expire_stale_sessions();
            const int wait_ms = sessions_.empty()
                                    ? remaining_idle_ms()
                                    : std::min(250, remaining_session_ms());
            if (sessions_.empty() && wait_ms <= 0) return 0;

            pollfd descriptor{STDIN_FILENO, POLLIN | POLLHUP, 0};
            const int poll_result = ::poll(&descriptor, 1, std::max(1, wait_ms));
            if (poll_result < 0) {
                if (errno == EINTR) continue;
                throw std::runtime_error("poll failed");
            }
            if (poll_result == 0) continue;
            if (descriptor.revents & (POLLERR | POLLNVAL)) return 1;

            std::string line;
            if (!std::getline(std::cin, line)) return 0;
            if (line.empty()) continue;
            last_activity_ = Clock::now();
            if (!handle_line(line)) return 0;
        }
    }

  private:
    int remaining_idle_ms() const {
        const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
            Clock::now() - last_activity_);
        return options_.idle_timeout_ms - static_cast<int>(elapsed.count());
    }

    int remaining_session_ms() const {
        int remaining = options_.session_idle_timeout_ms;
        const auto now = Clock::now();
        for (const auto &[id, session] : sessions_) {
            (void)id;
            const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
                now - session.last_activity);
            remaining = std::min(
                remaining,
                options_.session_idle_timeout_ms - static_cast<int>(elapsed.count()));
        }
        return std::max(1, remaining);
    }

    void expire_stale_sessions() {
        const auto now = Clock::now();
        bool expired_any = false;
        for (auto iterator = sessions_.begin(); iterator != sessions_.end();) {
            const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
                now - iterator->second.last_activity);
            if (elapsed.count() < options_.session_idle_timeout_ms) {
                ++iterator;
                continue;
            }
            FunASRUninit(iterator->second.handle);
            iterator = sessions_.erase(iterator);
            expired_any = true;
        }
        if (expired_any && sessions_.empty()) last_activity_ = now;
    }

    bool handle_line(const std::string &line) {
        Json request;
        try {
            request = Json::parse(line);
            const std::string type = request.value("type", "");
            if (type == "start") {
                start_session();
            } else if (type == "feed") {
                feed_session(request);
            } else if (type == "close") {
                close_session(request);
            } else if (type == "ping") {
                emit({{"success", true}});
            } else if (type == "stop") {
                emit({{"success", true}});
                return false;
            } else {
                emit({{"success", false}, {"error", "unknown_request"}});
            }
        } catch (const std::exception &error) {
            emit({{"success", false}, {"error", error.what()}});
        }
        return true;
    }

    void start_session() {
        const std::string id = std::to_string(++next_session_id_);
        FUNASR_HANDLE online =
            FunASROnlineInit(model_handle_, options_.chunk_size);
        if (!online) throw std::runtime_error("FunASROnlineInit returned null");
        sessions_.emplace(id, OnlineSession{online, "", Clock::now()});
        emit({{"success", true},
              {"session_id", id},
              {"sample_rate", 16000},
              {"chunk_samples", options_.chunk_size[1] * 960}});
    }

    void feed_session(const Json &request) {
        const std::string id = request.value("session_id", "");
        auto found = sessions_.find(id);
        if (found == sessions_.end()) {
            throw std::runtime_error("streaming_session_not_found");
        }
        const std::string encoded = request.value("pcm16", "");
        std::vector<char> pcm = decode_base64(encoded);
        if (pcm.size() % 2 != 0) {
            throw std::runtime_error("PCM16 byte count must be even");
        }
        const bool is_final = request.value("is_final", false);
        found->second.last_activity = Clock::now();
        if (!pcm.empty() || is_final) {
            FUNASR_RESULT result = FunASRInferBuffer(
                found->second.handle,
                pcm.empty() ? nullptr : pcm.data(),
                static_cast<int>(pcm.size()),
                RASR_NONE,
                nullptr,
                is_final,
                16000,
                "pcm");
            if (!result) throw std::runtime_error("FunASRInferBuffer returned null");
            const char *text = FunASRGetResult(result, 0);
            if (text) found->second.text += text;
            FunASRFreeResult(result);
        }
        emit({{"success", true},
              {"text", found->second.text},
              {"final", is_final}});
    }

    void close_session(const Json &request) {
        const std::string id = request.value("session_id", "");
        auto found = sessions_.find(id);
        if (found == sessions_.end()) {
            emit({{"success", false}, {"error", "streaming_session_not_found"}});
            return;
        }
        const std::string text = found->second.text;
        FunASRUninit(found->second.handle);
        sessions_.erase(found);
        emit({{"success", true}, {"text", text}, {"final", true}});
    }

    Options options_;
    FUNASR_HANDLE model_handle_ = nullptr;
    std::unordered_map<std::string, OnlineSession> sessions_;
    std::uint64_t next_session_id_ = 0;
    Clock::time_point last_activity_{};
};

}  // namespace

int main(int argc, char **argv) {
    google::InitGoogleLogging(argv[0]);
    FLAGS_logtostderr = true;
    try {
        Worker worker(parse_options(argc, argv));
        return worker.run();
    } catch (const std::exception &error) {
        emit({{"type", "ready"}, {"success", false}, {"error", error.what()}});
        return 1;
    }
}
