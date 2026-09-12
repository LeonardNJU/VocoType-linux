#include "win_support.hpp"
#include <cstdint>
#include <fstream>
#include <iostream>
#include <thread>
using namespace vocotype::windows;
void emit(const Json& j){std::cout<<j.dump()<<'\n'<<std::flush;}
int wmain(int argc,wchar_t** argv) {
  try {
    std::wstring mode=argc>1?argv[1]:L"";
    if(mode==L"--echo") {Json values=Json::array();for(int i=2;i<argc;++i)values.push_back(utf8(argv[i]));emit({{"argv",values}});return 0;}
    if(mode==L"--silent") {Sleep(60000);return 0;}
    if(mode==L"--oversized") {std::cout<<std::string(1024*1024+100,'x')<<'\n'<<std::flush;Sleep(1000);return 0;}
    if(mode==L"--fragment") {std::cout<<"{\"ok\":"<<std::flush;Sleep(250);std::cout<<"true}\n"<<std::flush;return 0;}
    if(mode==L"--tree") {ChildProcess child;child.start(executable_path(),{L"--silent"});emit({{"pid",child.pid()}});Sleep(60000);return 0;}
    if(mode==L"--capture"||mode==L"--empty"||mode==L"--bad-offset"||mode==L"--no-end") {
      if(mode==L"--empty"){emit({{"type","audio_end"},{"frames",0}});return 0;}
      emit({{"type","recording"},{"sample_rate",16000}});
      std::vector<std::int16_t> block(1600,1234);
      for(std::size_t offset=0;offset<16000;offset+=block.size()) {
        emit({{"type","pcm"},{"sample_rate",16000},{"frames",block.size()},{"offset",mode==L"--bad-offset"?offset+1:offset},{"pcm16",base64(block.data(),block.size()*2)}});Sleep(10);
      }
      if(mode==L"--no-end"){Sleep(60000);return 0;}
      emit({{"type","audio_end"},{"frames",16000}});return 0;
    }
    // Explicitly test-only deterministic decoder. It verifies the WAV that the
    // production pipeline creates; it is never included in the portable app.
    emit({{"type","ready"},{"success",true}});
    std::string line;
    while(std::getline(std::cin,line)) {
      auto request=Json::parse(line);auto type=request.value("type","");
      if(type=="stop")return 0;
      if(type=="transcribe") {
        std::ifstream audio(std::filesystem::u8path(request.at("audio_path").get<std::string>()),std::ios::binary);
        std::vector<unsigned char> bytes((std::istreambuf_iterator<char>(audio)),{});
        bool correct=bytes.size()==44+32000 && std::string(bytes.begin(),bytes.begin()+4)=="RIFF";
        for(std::size_t i=44;correct&&i<bytes.size();i+=2)correct=bytes[i]==0xd2&&bytes[i+1]==0x04;
        emit({{"success",correct},{"text","Windows 中文录音完整 16000"},{"error",correct?"":"WAV integrity check failed"}});
      }else emit({{"success",true}});
    }
    return 0;
  }catch(const std::exception& e){emit({{"success",false},{"error",e.what()}});return 2;}
}
