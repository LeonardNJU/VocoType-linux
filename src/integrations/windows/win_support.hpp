#pragma once
#include <windows.h>
#include <objbase.h>
#include <filesystem>
#include <memory>
#include <string>
#include <string_view>
#include <vector>
#include <nlohmann/json.hpp>
namespace vocotype::windows {
using Json = nlohmann::json;
class Handle {
  HANDLE value_ = nullptr;
public:
  Handle() = default;
  explicit Handle(HANDLE h) : value_(h) {}
  ~Handle() { reset(); }
  Handle(const Handle&) = delete;
  Handle& operator=(const Handle&) = delete;
  Handle(Handle&& other) noexcept : value_(other.release()) {}
  Handle& operator=(Handle&& other) noexcept { if(this != &other) reset(other.release()); return *this; }
  HANDLE get() const { return value_; }
  explicit operator bool() const { return value_ && value_ != INVALID_HANDLE_VALUE; }
  HANDLE release() { HANDLE h=value_; value_=nullptr; return h; }
  void reset(HANDLE h=nullptr) { if(*this) CloseHandle(value_); value_=h; }
};
std::wstring wide(std::string_view text);
std::string utf8(std::wstring_view text);
std::string path_utf8(const std::filesystem::path& path);
std::filesystem::path executable_path();
std::filesystem::path data_dir();
std::wstring quote_argument(std::wstring_view text);
std::string base64(const void* data, std::size_t bytes);
std::vector<unsigned char> unbase64(const std::string& text);
std::wstring preview_tail(std::wstring_view text, std::size_t limit=40);
std::vector<INPUT> unicode_events(std::wstring_view text);
void check(BOOL ok, const char* action);
class Security {
  PSECURITY_DESCRIPTOR descriptor_ = nullptr;
public:
  SECURITY_ATTRIBUTES attributes{};
  Security();
  ~Security() { if(descriptor_) LocalFree(descriptor_); }
  Security(const Security&) = delete;
};
// Private, local-only named pipes. Only stdio handles are inherited. A job
// object owns the entire child tree, including a core's ASR worker processes.
class ChildProcess {
  Handle process_, job_, input_, output_;
  Handle cancelled_{CreateEventW(nullptr, TRUE, FALSE, nullptr)};
  std::string buffer_;
public:
  ChildProcess() = default;
  ChildProcess(const ChildProcess&) = delete;
  ~ChildProcess() { stop(); }
  void start(const std::filesystem::path& exe, const std::vector<std::wstring>& args);
  bool alive() const;
  DWORD pid() const;
  std::string read_line(int timeout_ms);
  void write_line(const std::string& line, int timeout_ms);
  void stop() noexcept;
  void cancel() { if(cancelled_) SetEvent(cancelled_.get()); }
};
}
