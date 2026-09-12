#include "pipeline.hpp"
#include <algorithm>
#include <condition_variable>
#include <cstring>
#include <thread>
namespace vocotype::windows {
namespace {
Json require_success(Json result) {
  if(!result.value("success",false)) throw std::runtime_error(result.value("error","request_failed")); return result;
}
// The shared core takes ownership of audio_path and deletes it after ASR.
// Public CLI inputs must never be transferred directly into that contract.
struct PrivateAudioCopy {
  std::filesystem::path path;
  explicit PrivateAudioCopy(const std::filesystem::path& source) {
    Handle input(CreateFileW(source.c_str(),GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,FILE_FLAG_SEQUENTIAL_SCAN,nullptr));
    check(static_cast<bool>(input),"open source audio (read-only)");
    LARGE_INTEGER size{};check(GetFileSizeEx(input.get(),&size),"source audio size");
    if(size.QuadPart<=0 || size.QuadPart>256LL*1024*1024)throw std::runtime_error("audio file must be between 1 byte and 256 MiB");
    wchar_t temp[32768]{};DWORD length=GetTempPathW(32768,temp);check(length>0&&length<32768,"temporary directory");
    GUID guid{};if(FAILED(CoCreateGuid(&guid)))throw std::runtime_error("audio copy name failed");wchar_t id[40]{};StringFromGUID2(guid,id,40);
    auto candidate=std::filesystem::path(temp)/(std::wstring(L"vocotype-owned-")+id+L".wav");
    Security security;Handle output(CreateFileW(candidate.c_str(),GENERIC_WRITE,FILE_SHARE_READ,&security.attributes,CREATE_NEW,FILE_ATTRIBUTE_TEMPORARY,nullptr));
    check(static_cast<bool>(output),"create private ASR audio copy");
    try {
      char buffer[65536];DWORD read=0;LONGLONG total=0;
      for(;;){check(ReadFile(input.get(),buffer,sizeof(buffer),&read,nullptr),"read source audio");if(!read)break;DWORD offset=0;
        while(offset<read){DWORD wrote=0;check(WriteFile(output.get(),buffer+offset,read-offset,&wrote,nullptr)&&wrote>0,"write private audio copy");offset+=wrote;}
        total+=read;
      }
      if(total!=size.QuadPart)throw std::runtime_error("source audio changed while copying");
      path=candidate;
    }catch(...){output.reset();DeleteFileW(candidate.c_str());throw;}
  }
  ~PrivateAudioCopy(){if(!path.empty())DeleteFileW(path.c_str());}
};
struct TemporaryWav {
  std::filesystem::path path;
  explicit TemporaryWav(const std::vector<std::int16_t>& pcm) {
    wchar_t temp[32768]{}; DWORD n=GetTempPathW(32768,temp); check(n>0&&n<32768,"temporary directory");
    GUID guid{}; if(FAILED(CoCreateGuid(&guid)))throw std::runtime_error("WAV name failed"); wchar_t id[40]{}; StringFromGUID2(guid,id,40);
    auto candidate=std::filesystem::path(temp)/(std::wstring(L"vocotype-")+id+L".wav");
    Security security; Handle file(CreateFileW(candidate.c_str(),GENERIC_WRITE,FILE_SHARE_READ,&security.attributes,CREATE_NEW,FILE_ATTRIBUTE_TEMPORARY,nullptr)); check(static_cast<bool>(file),"create private WAV");
    std::vector<unsigned char> header(44,0);
    auto u16=[&](int offset,std::uint16_t x){header[offset]=static_cast<unsigned char>(x);header[offset+1]=static_cast<unsigned char>(x>>8);};
    auto u32=[&](int offset,std::uint32_t x){for(int i=0;i<4;++i)header[offset+i]=static_cast<unsigned char>(x>>(8*i));};
    std::memcpy(header.data(),"RIFF",4);u32(4,36+static_cast<std::uint32_t>(pcm.size()*2));
    std::memcpy(header.data()+8,"WAVEfmt ",8);u32(16,16);u16(20,1);u16(22,1);u32(24,16000);u32(28,32000);u16(32,2);u16(34,16);
    std::memcpy(header.data()+36,"data",4);u32(40,static_cast<std::uint32_t>(pcm.size()*2));
    try {
      DWORD count=0;check(WriteFile(file.get(),header.data(),44,&count,nullptr)&&count==44,"write WAV header");
      check(WriteFile(file.get(),pcm.data(),static_cast<DWORD>(pcm.size()*2),&count,nullptr)&&count==pcm.size()*2,"write audio");
      path=candidate;
    } catch(...) {file.reset();DeleteFileW(candidate.c_str());throw;}
  }
  ~TemporaryWav(){if(!path.empty())DeleteFileW(path.c_str());}
};
}
void CoreClient::start(const std::filesystem::path& exe,const std::filesystem::path& config,const std::wstring& role) {
  std::lock_guard lock(mutex_);
  if(shutdown_.load())throw std::runtime_error("core_shutdown");
  healthy_.store(false); child_.start(exe,{L"--config",config.native(),L"--role",role});
  if(shutdown_.load()){child_.stop();throw std::runtime_error("core_shutdown");}
  auto ready=require_success(Json::parse(child_.read_line(210000)));
  if(ready.value("type","")!="ready")throw std::runtime_error("core handshake invalid");
  child_.write_line(Json({{"type",role==L"final"?"asr_prepare":"capabilities"}}).dump(),1000);
  require_success(Json::parse(child_.read_line(role==L"final"?120000:1000)));
  healthy_.store(true);
}
void CoreClient::terminate() { std::lock_guard lock(mutex_);child_.stop();healthy_.store(false); }
Json CoreClient::request(const Json& request,int timeout) {
  std::lock_guard lock(mutex_);
  try {child_.write_line(request.dump(),std::min(1000,timeout));return Json::parse(child_.read_line(timeout));}
  catch(...){child_.stop();healthy_.store(false);throw;}
}
Json transcribe_file(CoreClient& core,const std::filesystem::path& path,CaptureControl& control,bool polish,const EventCallback& event) {
  if(control.cancel.load())return {{"success",false},{"error","cancelled"}};
  event({{"type","finalizing"}});
  PrivateAudioCopy owned(path);
  auto task=require_success(core.request({{"type","transcribe_start"},{"audio_path",path_utf8(owned.path)},{"long_mode",polish}}));
  auto id=task.at("task_id").get<std::string>(); const auto deadline=GetTickCount64()+150000;
  for(;;) {
    if(control.cancel.load() || GetTickCount64()>deadline) {
      // Abort the contained decoder before releasing the temporary WAV. A
      // cancellation cannot leave an ASR process reading a deleted recording.
      core.terminate();
      return {{"success",false},{"error",control.cancel.load()?"cancelled":"transcription_timeout"}};
    }
    auto result=require_success(core.request({{"type","polish_poll"},{"task_id",id}},2000));
    auto status=result.value("status","");
    if(status=="final")return {{"success",true},{"text",result.value("final_text","")}};
    if(status=="error"||status=="cancelled")return {{"success",false},{"error",result.value("error",status)}};
    Sleep(80);
  }
}
Json dictate(CoreClient& core,CoreClient* preview,const std::filesystem::path& recorder,const std::vector<std::wstring>& args,CaptureControl& control,bool polish,const EventCallback& event) {
  struct Shared { std::mutex mutex; std::condition_variable cv;std::vector<std::int16_t> pcm;bool done=false; } shared;
  std::jthread preview_thread;
  auto finish_preview=[&]{ {std::lock_guard lock(shared.mutex);shared.done=true;} shared.cv.notify_all(); };
  try {
    ChildProcess mic; mic.start(recorder,args);
    event({{"type","starting"}});
    if(preview) preview_thread=std::jthread([&](std::stop_token stopped){
      std::string session;
      try {
        auto start=require_success(preview->request({{"type","asr_preview_start"}},8000));
        session=start.at("session_id").get<std::string>();
        std::size_t chunk=static_cast<std::size_t>(std::clamp(start.value("chunk_samples",9600),1600,32000));
        std::size_t offset=0;
        while(!stopped.stop_requested()&&!control.cancel.load()) {
          std::vector<std::int16_t> data; bool final=false;
          {
            std::unique_lock lock(shared.mutex);
            shared.cv.wait_for(lock,std::chrono::milliseconds(100),[&]{return shared.done||shared.pcm.size()-offset>=chunk;});
            if(stopped.stop_requested()||control.cancel.load())break;
            auto available=shared.pcm.size()-offset;
            if(!shared.done && available<chunk)continue;
            if(available>16000*15)throw std::runtime_error("preview lagged; final audio retained");
            if(available==0 && shared.done)break;
            auto n=std::min(chunk,available); data.assign(shared.pcm.begin()+offset,shared.pcm.begin()+offset+n);offset+=n;
            final=shared.done&&offset==shared.pcm.size();
          }
          auto response=require_success(preview->request({{"type","asr_preview_feed"},{"session_id",session},{"pcm16",base64(data.data(),data.size()*2)},{"is_final",final}},8000));
          auto text=response.value("text","");if(!text.empty())event({{"type","partial"},{"text",text}});
          if(final)break;
        }
      }catch(const std::exception& e){event({{"type","preview_unavailable"},{"error",e.what()}});}
      if(!session.empty())try{preview->request({{"type","asr_preview_close"},{"session_id",session},{"flush",false}},1000);}catch(...){}
    });
    bool stopped=false,ended=false; const auto began=GetTickCount64();auto last_pcm=began;ULONGLONG stop_deadline=0;std::size_t received=0;unsigned discontinuities=0;
    while(!ended) {
      if(control.cancel.load()){mic.stop();finish_preview();preview_thread.request_stop();return {{"success",false},{"error","cancelled"}};}
      if(control.stop.load()&&!stopped){mic.write_line("stop",500);stopped=true;stop_deadline=GetTickCount64()+2000;}
      if(stopped&&GetTickCount64()>stop_deadline)throw std::runtime_error("microphone_stop_timeout");
      if(!stopped && GetTickCount64()-last_pcm>(received?3000:5000))throw std::runtime_error(received?"microphone_audio_stalled":"microphone_start_timeout");
      Json data;
      try{data=Json::parse(mic.read_line(100));}
      catch(const std::exception& e){if(std::string(e.what())=="worker_request_timeout")continue;throw;}
      auto type=data.value("type","");
      if(type=="error")throw std::runtime_error(data.value("error","recording_failed"));
      if(type=="recording") {event(data);continue;}
      if(type=="audio_end") {if(data.value("frames",std::size_t(0))!=received)throw std::runtime_error("recording_frame_count_mismatch");ended=true;continue;}
      if(type!="pcm")continue;
      auto bytes=unbase64(data.value("pcm16",""));
      if(bytes.empty()||bytes.size()%2||data.value("sample_rate",0)!=16000||data.value("frames",std::size_t(0))!=bytes.size()/2||data.value("offset",std::size_t(0))!=received)throw std::runtime_error("invalid_pcm_packet");
      if(received+bytes.size()/2>16000*900)throw std::runtime_error("maximum_recording_duration_exceeded");
      if(data.value("discontinuity",false))++discontinuities;
      {std::lock_guard lock(shared.mutex);auto before=shared.pcm.size();shared.pcm.resize(before+bytes.size()/2);std::memcpy(shared.pcm.data()+before,bytes.data(),bytes.size());received=shared.pcm.size();}
      shared.cv.notify_all();last_pcm=GetTickCount64();
    }
    mic.stop();finish_preview();
    if(received<8000)return {{"success",false},{"error",received?"recording_too_short":"microphone_never_started"}};
    TemporaryWav wav(shared.pcm);
    auto result=transcribe_file(core,wav.path,control,polish,event);
    result["frames"]=received;result["discontinuities"]=discontinuities;
    preview_thread.request_stop();return result;
  }catch(const std::exception& e){finish_preview();preview_thread.request_stop();return {{"success",false},{"error",e.what()}};}
}
}
