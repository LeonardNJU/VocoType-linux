#include "vocotype/desktop/recorder_process.hpp"
#include "vocotype/common/posix.hpp"
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <fcntl.h>
#include <memory>
#include <nlohmann/json.hpp>
#include <signal.h>
#include <stdexcept>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>
namespace vocotype::desktop {
RecorderProcess::~RecorderProcess() { cancel(); }
void RecorderProcess::start(const std::string &executable,
                            EventCallback callback) {
  if (running())
    throw std::runtime_error("recorder is already running");
  int input_pipe[2]{};
  int output_pipe[2]{};
  if (vocotype::common::create_pipe_close_on_exec(input_pipe) != 0)
    throw std::runtime_error("cannot create recorder input pipe");
  if (vocotype::common::create_pipe_close_on_exec(output_pipe) != 0) {
    close(input_pipe[0]);
    close(input_pipe[1]);
    throw std::runtime_error("cannot create recorder output pipe");
  }
  const pid_t child = fork();
  if (child < 0)
    throw std::runtime_error("cannot fork recorder");
  if (child == 0) {
    dup2(input_pipe[0], STDIN_FILENO);
    dup2(output_pipe[1], STDOUT_FILENO);
    close(input_pipe[0]);
    close(input_pipe[1]);
    close(output_pipe[0]);
    close(output_pipe[1]);
    execl(executable.c_str(), executable.c_str(), static_cast<char *>(nullptr));
    _exit(127);
  }
  close(input_pipe[0]);
  close(output_pipe[1]);
  pid_ = child;
  stdin_fd_ = input_pipe[1];
  stdout_fd_ = output_pipe[0];
  audio_path_.clear();
  auto thread =
      std::make_shared<std::thread>([this, callback = std::move(callback)] {
        FILE *file = fdopen(stdout_fd_, "r");
        if (!file)
          return;
        char *line = nullptr;
        std::size_t capacity = 0;
        while (getline(&line, &capacity, file) >= 0) {
          try {
            auto value = nlohmann::json::parse(line);
            const std::string type = value.value("type", "");
            const std::string payload =
                type == "audio"     ? value.value("path", "")
                : type == "partial" ? value.value("text", "")
                                    : value.value("error", "");
            if (type == "audio")
              audio_path_ = payload;
            if (callback)
              callback(type, payload);
          } catch (const std::exception &) {
          }
        }
        free(line);
        fclose(file);
        stdout_fd_ = -1;
      });
  join_output_ = [thread] {
    if (thread->joinable())
      thread->join();
  };
}
std::string RecorderProcess::stop() {
  if (stdin_fd_ >= 0) {
    close(stdin_fd_);
    stdin_fd_ = -1;
  }
  if (join_output_) {
    join_output_();
    join_output_ = {};
  }
  if (pid_ > 0) {
    int status = 0;
    while (waitpid(pid_, &status, 0) < 0 && errno == EINTR) {
    }
    pid_ = -1;
  }
  return audio_path_;
}
void RecorderProcess::cancel() {
  if (!running() && stdin_fd_ < 0)
    return;
  if (stdin_fd_ >= 0) {
    close(stdin_fd_);
    stdin_fd_ = -1;
  }
  if (pid_ > 0)
    kill(pid_, SIGTERM);
  if (join_output_) {
    join_output_();
    join_output_ = {};
  }
  if (pid_ > 0) {
    while (waitpid(pid_, nullptr, 0) < 0 && errno == EINTR) {
    }
    pid_ = -1;
  }
  if (!audio_path_.empty()) {
    std::remove(audio_path_.c_str());
    audio_path_.clear();
  }
}
} // namespace vocotype::desktop
