#pragma once
#include "win_support.hpp"
#include <atomic>
#include <cstdint>
#include <functional>
#include <mutex>
namespace vocotype::windows {
class CoreClient {
  ChildProcess child_;
  std::mutex mutex_;
public:
  void start(const std::filesystem::path& exe,const std::filesystem::path& config,const std::wstring& role=L"final");
  Json request(const Json& request,int timeout_ms=8000);
  void cancel() { child_.cancel(); }
};
struct CaptureControl { std::atomic_bool stop{false}, cancel{false}; };
using EventCallback=std::function<void(const Json&)>;
Json transcribe_file(CoreClient& core,const std::filesystem::path& path,CaptureControl& control,bool polish,const EventCallback& event);
Json dictate(CoreClient& core,CoreClient* preview,const std::filesystem::path& recorder,
             const std::vector<std::wstring>& args,CaptureControl& control,bool polish,const EventCallback& event);
}
