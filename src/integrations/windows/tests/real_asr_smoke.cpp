#include "pipeline.hpp"
#include <fstream>
#include <algorithm>
#include <iostream>
#include <cstring>
using namespace vocotype::windows;
namespace {
Json require(Json response){if(!response.value("success",false))throw std::runtime_error(response.dump());return response;}
std::uint32_t u32(const unsigned char* p){return static_cast<std::uint32_t>(p[0])|(static_cast<std::uint32_t>(p[1])<<8)|(static_cast<std::uint32_t>(p[2])<<16)|(static_cast<std::uint32_t>(p[3])<<24);}
std::uint16_t u16(const unsigned char* p){return static_cast<std::uint16_t>(p[0]|(p[1]<<8));}
std::vector<unsigned char> read_fixture(const std::filesystem::path& path){
  std::ifstream file(path,std::ios::binary);std::vector<unsigned char> bytes((std::istreambuf_iterator<char>(file)),{});
  if(bytes.size()<44||bytes.size()>2000000||std::memcmp(bytes.data(),"RIFF",4)||std::memcmp(bytes.data()+8,"WAVE",4))throw std::runtime_error("invalid WAV fixture");
  bool format=false;std::vector<unsigned char> pcm;
  for(std::size_t offset=12;offset+8<=bytes.size();){
    const auto size=static_cast<std::size_t>(u32(bytes.data()+offset+4));auto start=offset+8;
    if(size>bytes.size()-start)throw std::runtime_error("truncated WAV chunk");
    if(!std::memcmp(bytes.data()+offset,"fmt ",4)){
      if(size<16||u16(bytes.data()+start)!=1||u16(bytes.data()+start+2)!=1||u32(bytes.data()+start+4)!=16000||u16(bytes.data()+start+14)!=16)throw std::runtime_error("fixture must be 16kHz mono PCM16");format=true;
    }
    if(!std::memcmp(bytes.data()+offset,"data",4))pcm.assign(bytes.begin()+start,bytes.begin()+start+size);
    offset=start+size+(size&1);
  }
  if(!format||pcm.empty()||pcm.size()%2)throw std::runtime_error("fixture has no PCM data");return pcm;
}
}
int wmain(int argc,wchar_t** argv){
  try{
    if(argc!=4)throw std::runtime_error("usage: real-asr-smoke BUNDLE CONFIG FIXTURE");
    const auto bundle=std::filesystem::absolute(argv[1]),config=std::filesystem::absolute(argv[2]),fixture=std::filesystem::absolute(argv[3]);
    ChildProcess shipping_app;shipping_app.start(bundle/L"vocotype-windows.exe",{L"--config",config.native(),L"--transcribe",fixture.native()});
    auto offline=require(Json::parse(shipping_app.read_line(240000)));shipping_app.stop();
    if(offline.value("text","").empty())throw std::runtime_error("real offline ASR returned empty text");
    std::cout<<"REAL_OFFLINE_ASR "<<offline.dump()<<'\n'<<std::flush;
    CoreClient preview;preview.start(bundle/L"vocotype-core.exe",config,L"preview");
    auto start=require(preview.request({{"type","asr_preview_start"}},30000));auto id=start.at("session_id").get<std::string>();
    auto chunk=static_cast<std::size_t>(std::clamp(start.value("chunk_samples",9600),1600,32000))*2;
    auto pcm=read_fixture(fixture);std::string final;int updates=0;
    for(std::size_t offset=0;offset<pcm.size();offset+=chunk){
      auto count=std::min(chunk,pcm.size()-offset);
      auto response=require(preview.request({{"type","asr_preview_feed"},{"session_id",id},{"pcm16",base64(pcm.data()+offset,count)},{"is_final",offset+count==pcm.size()}},30000));
      if(!response.value("text","").empty()){final=response.at("text").get<std::string>();++updates;}
    }
    require(preview.request({{"type","asr_preview_close"},{"session_id",id},{"flush",false}},5000));
    if(final.empty())throw std::runtime_error("real streaming ASR returned no text");
    Json report={{"offline",offline},{"streaming_text",final},{"streaming_updates",updates},{"fixture_frames",pcm.size()/2},{"physical_microphone_tested",false}};
    std::ofstream output(config.parent_path()/L"real-asr-report.json");output<<report.dump(2)<<'\n';if(!output)throw std::runtime_error("write ASR report failed");
    std::cout<<"REAL_STREAMING_ASR "<<report.dump()<<'\n';return 0;
  }catch(const std::exception& error){std::cerr<<"REAL_ASR_FAIL "<<error.what()<<'\n';return 1;}
}
