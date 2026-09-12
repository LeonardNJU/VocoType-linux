#include "win_support.hpp"
#include <audioclient.h>
#include <mmdeviceapi.h>
#include <functiondiscoverykeys_devpkey.h>
#include <wrl/client.h>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <memory>
#include <sstream>
#include <thread>
using namespace vocotype::windows;
using Microsoft::WRL::ComPtr;
namespace {
void hrcheck(HRESULT hr,const char* action) {
  if(SUCCEEDED(hr)) return;
  std::ostringstream s; s<<action<<" (HRESULT 0x"<<std::hex<<static_cast<unsigned long>(hr)<<")";
  if(hr==E_ACCESSDENIED) s<<": enable microphone access for desktop apps in Windows Settings";
  if(hr==AUDCLNT_E_DEVICE_INVALIDATED) s<<": input device changed or disconnected; retry recording";
  throw std::runtime_error(s.str());
}
struct Com {
  Com(){hrcheck(CoInitializeEx(nullptr,COINIT_APARTMENTTHREADED),"initialize COM");}
  ~Com(){CoUninitialize();}
};
void emit(const Json& data){std::cout<<data.dump()<<'\n'<<std::flush; if(!std::cout) throw std::runtime_error("recorder output closed");}
Json probe() {
  Com com; ComPtr<IMMDeviceEnumerator> devices;
  hrcheck(CoCreateInstance(__uuidof(MMDeviceEnumerator),nullptr,CLSCTX_ALL,IID_PPV_ARGS(&devices)),"enumerate microphones");
  ComPtr<IMMDeviceCollection> collection; hrcheck(devices->EnumAudioEndpoints(eCapture,DEVICE_STATE_ACTIVE,&collection),"list microphones");
  UINT count=0; hrcheck(collection->GetCount(&count),"microphone count"); Json result=Json::array();
  for(UINT i=0;i<count;++i) {
    ComPtr<IMMDevice> device; hrcheck(collection->Item(i,&device),"microphone");
    LPWSTR id=nullptr; hrcheck(device->GetId(&id),"microphone ID"); std::string key=utf8(id); CoTaskMemFree(id);
    ComPtr<IPropertyStore> props; hrcheck(device->OpenPropertyStore(STGM_READ,&props),"microphone properties");
    PROPVARIANT name; PropVariantInit(&name); hrcheck(props->GetValue(PKEY_Device_FriendlyName,&name),"microphone name");
    std::string label=name.vt==VT_LPWSTR?utf8(name.pwszVal):key; PropVariantClear(&name);
    result.push_back({{"id",key},{"name",label}});
  }
  return {{"type","devices"},{"success",true},{"backend","wasapi"},{"devices",result}};
}
int record(int duration_ms,const std::wstring& endpoint) {
  auto stop=std::make_shared<std::atomic_bool>(false);
  // This thread owns shared state, not stack references. Parent EOF stops audio;
  // hung driver initialization is bounded by the parent's process watchdog.
  std::thread([stop]{std::string line; while(std::getline(std::cin,line)) { if(line=="stop" || line=="cancel") break; } stop->store(true);}).detach();
  Com com; ComPtr<IMMDeviceEnumerator> devices; ComPtr<IMMDevice> device;
  hrcheck(CoCreateInstance(__uuidof(MMDeviceEnumerator),nullptr,CLSCTX_ALL,IID_PPV_ARGS(&devices)),"enumerate microphones");
  if(endpoint.empty()) hrcheck(devices->GetDefaultAudioEndpoint(eCapture,eConsole,&device),"default microphone");
  else hrcheck(devices->GetDevice(endpoint.c_str(),&device),"selected microphone");
  ComPtr<IAudioClient> client; hrcheck(device->Activate(__uuidof(IAudioClient),CLSCTX_ALL,nullptr,reinterpret_cast<void**>(client.GetAddressOf())),"activate microphone");
  WAVEFORMATEX format{}; format.wFormatTag=WAVE_FORMAT_PCM; format.nChannels=1; format.nSamplesPerSec=16000;
  format.wBitsPerSample=16; format.nBlockAlign=2; format.nAvgBytesPerSec=32000;
  const DWORD flags=AUDCLNT_STREAMFLAGS_EVENTCALLBACK|AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM|AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY;
  hrcheck(client->Initialize(AUDCLNT_SHAREMODE_SHARED,flags,0,0,&format,nullptr),"open shared-mode microphone at 16kHz mono");
  Handle event(CreateEventW(nullptr,FALSE,FALSE,nullptr)); check(static_cast<bool>(event),"audio event");
  hrcheck(client->SetEventHandle(event.get()),"set audio event");
  ComPtr<IAudioCaptureClient> capture; hrcheck(client->GetService(IID_PPV_ARGS(&capture)),"capture service");
  if(stop->load()) { emit({{"type","audio_end"},{"frames",0}}); return 0; }
  hrcheck(client->Start(),"start microphone");
  struct StopAudio { IAudioClient* client; ~StopAudio(){client->Stop();} } stop_audio{client.Get()};
  bool first=true; std::uint64_t frames=0; const auto started=GetTickCount64(); auto last_block=started;
  const std::uint64_t limit=duration_ms>0?static_cast<std::uint64_t>(duration_ms)*16:16000ULL*900;
  while(!stop->load() && frames<limit) {
    DWORD waited=WaitForSingleObject(event.get(),50);
    if(waited==WAIT_FAILED) check(FALSE,"wait microphone");
    if(GetTickCount64()-last_block>(first?5000:3000)) throw std::runtime_error(first?"microphone_start_timeout":"microphone_audio_stalled");
    UINT32 available=0; hrcheck(capture->GetNextPacketSize(&available),"next audio packet");
    while(available>0 && frames<limit) {
      BYTE* raw=nullptr; UINT32 count=0; DWORD status=0;
      hrcheck(capture->GetBuffer(&raw,&count,&status,nullptr,nullptr),"read microphone");
      struct Release { IAudioCaptureClient* capture; UINT32 count; ~Release(){capture->ReleaseBuffer(count);} } release{capture.Get(),count};
      const auto taken=static_cast<std::size_t>(std::min<std::uint64_t>(count,limit-frames));
      std::vector<std::int16_t> samples(taken,0);
      if(!(status&AUDCLNT_BUFFERFLAGS_SILENT) && raw && taken) std::memcpy(samples.data(),raw,taken*2);
      if(taken) {
        if(first) { emit({{"type","recording"},{"sample_rate",16000},{"channels",1},{"startup_ms",GetTickCount64()-started}}); first=false; }
        emit({{"type","pcm"},{"sample_rate",16000},{"offset",frames},{"frames",taken},{"pcm16",base64(samples.data(),taken*2)},{"discontinuity",(status&AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY)!=0}});
        frames+=taken; last_block=GetTickCount64();
      }
      // ReleaseBuffer must run before asking for the next packet.
      break;
    }
  }
  hrcheck(client->Stop(),"stop microphone");
  emit({{"type","audio_end"},{"frames",frames},{"sample_rate",16000}}); return 0;
}
}
int wmain(int argc,wchar_t** argv) {
  try {
    int duration=0; std::wstring endpoint;
    for(int i=1;i<argc;++i) {
      std::wstring_view arg=argv[i];
      if(arg==L"--help"){std::cout<<"vocotype-wasapi-recorder [--probe] [--duration-ms N] [--device-id ID]\n";return 0;}
      if(arg==L"--probe"){emit(probe());return 0;}
      if(arg==L"--duration-ms" && i+1<argc){duration=std::stoi(argv[++i]); if(duration<=0||duration>900000)throw std::runtime_error("duration must be 1..900000 ms");}
      else if(arg==L"--device-id" && i+1<argc)endpoint=argv[++i];
      else throw std::runtime_error("unknown recorder argument");
    }
    return record(duration,endpoint);
  } catch(const std::exception& e){try{emit({{"type","error"},{"error",e.what()}});}catch(...){} return 2;}
}
