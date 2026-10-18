#include "win_support.hpp"
#include "vocotype/core/config.hpp"
#include "vocotype/core/dispatcher.hpp"
#include <iostream>
#include <stdexcept>
using namespace vocotype::windows;
int wmain(int argc,wchar_t** argv) {
  try {
    if(argc==2 && std::wstring_view(argv[1])==L"--help") { std::cout<<"vocotype-core.exe --config FILE (private JSONL stdio transport)\n"; return 0; }
    if((argc!=3 && argc!=5) || std::wstring_view(argv[1])!=L"--config") throw std::runtime_error("--config FILE is required");
    auto config=vocotype::core::load_config(std::filesystem::path(argv[2]),false);
    if(argc==5) {
      if(std::wstring_view(argv[3])!=L"--role")throw std::runtime_error("invalid core role");
      const std::wstring role=argv[4];
      if(role==L"final")config.streaming_asr.enabled=false;
      else if(role==L"preview")config.offline_asr.enabled=false;
      else throw std::runtime_error("unknown core role");
    }
    vocotype::core::CoreDispatcher core(std::move(config));
    std::cout<<Json({{"type","ready"},{"success",true},{"backend","cpp"},{"transport","private-named-pipe"}}).dump()<<'\n'<<std::flush;
    // The parent owns both ends; no public socket or unauthenticated listener.
    for(;;) {
      std::string line; char c=0;
      while(std::cin.get(c) && c!='\n') { if(line.size()>=1024*1024) throw std::runtime_error("request_too_large"); line+=c; }
      if(line.empty() && !std::cin) return 0;
      try {
        auto request=Json::parse(line);
        if(request.value("type","")=="stop") return 0;
        std::cout<<core.dispatch(request).dump()<<'\n'<<std::flush;
      } catch(const std::exception& e) { std::cout<<Json({{"success",false},{"error",e.what()}}).dump()<<'\n'<<std::flush; }
    }
  } catch(const std::exception& e) { std::cout<<Json({{"type","ready"},{"success",false},{"error",e.what()}}).dump()<<'\n'<<std::flush; return 1; }
}
