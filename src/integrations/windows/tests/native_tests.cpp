#include "pipeline.hpp"
#include <fstream>
#include <iostream>
#include <thread>
using namespace vocotype::windows;
void expect(bool value,const char* message){if(!value)throw std::runtime_error(message);}
template<class F> void throws(F f,const char* message){bool failed=false;try{f();}catch(...){failed=true;}expect(failed,message);}
int wmain(int argc,wchar_t** argv) {
 try {
  expect(argc==4,"usage: tests CASE HELPER CORE");std::wstring which=argv[1];std::filesystem::path helper=argv[2],core_exe=argv[3];
  if(which==L"unicode") {
    std::wstring text=L"中文 e\u0301 \U0001f600";expect(wide(utf8(text))==text,"Unicode roundtrip");
    throws([]{wide("\xff");},"invalid UTF8 accepted");throws([]{utf8(std::wstring(1,0xd800));},"invalid surrogate accepted");
    auto longtext=std::wstring(1000,L'中')+L"\U0001f600";auto tail=preview_tail(longtext);expect(tail==L"\u2026"+std::wstring(39,L'中')+L"\U0001f600","tail dropped Unicode scalar");
    auto inputs=unicode_events(text);expect(inputs.size()==text.size()*2,"key pair count");
    for(std::size_t i=0;i<text.size();++i){expect(inputs[i*2].ki.wScan==text[i],"Unicode order");expect(inputs[i*2+1].ki.dwFlags==(KEYEVENTF_UNICODE|KEYEVENTF_KEYUP),"missing key-up");}
    std::vector<unsigned char> bytes={0,255,10,128,20};auto encoded=base64(bytes.data(),bytes.size());expect(unbase64(encoded)==bytes,"PCM roundtrip");
  }else if(which==L"process") {
    ChildProcess child;std::vector<std::wstring> args={L"--echo",L"",L"中文 space",L"C:\\space path\\",L"slash\\\"quoted",L"\U0001f600"};
    child.start(helper,args);auto response=Json::parse(child.read_line(3000));expect(response["argv"].size()==args.size()-1,"argv count");for(std::size_t i=1;i<args.size();++i)expect(response["argv"][i-1]==utf8(args[i]),"argv escaping");
    child.start(helper,{L"--fragment"});throws([&]{child.read_line(100);},"fragment should time out");expect(Json::parse(child.read_line(3000)).value("ok",false),"lost partial read on timeout");
    child.start(helper,{L"--oversized"});throws([&]{child.read_line(3000);},"response limit missing");
    child.start(helper,{L"--silent"});auto started=GetTickCount64();throws([&]{child.read_line(100);},"read timeout missing");expect(GetTickCount64()-started<2000,"unbounded read");
    child.start(helper,{L"--silent"});std::jthread cancel([&]{Sleep(100);child.cancel();});started=GetTickCount64();throws([&]{child.read_line(30000);},"cancel missing");expect(GetTickCount64()-started<2000,"cancel not prompt");cancel.join();
    child.start(helper,{L"--tree"});auto tree=Json::parse(child.read_line(3000));Handle grandchild(OpenProcess(SYNCHRONIZE,FALSE,tree.at("pid").get<DWORD>()));expect(static_cast<bool>(grandchild),"open grandchild");child.stop();expect(WaitForSingleObject(grandchild.get(),3000)==WAIT_OBJECT_0,"orphan process after stop");
    child.start(helper,{L"--echo",L"recovered"});expect(Json::parse(child.read_line(3000))["argv"][0]=="recovered","restart failed");
  }else {
    wchar_t temp[32768]{};GetTempPathW(32768,temp);auto dir=std::filesystem::path(temp)/(L"VocoType 测试 "+std::to_wstring(GetCurrentProcessId()));std::filesystem::create_directories(dir);
    struct Cleanup{std::filesystem::path p;~Cleanup(){std::error_code ec;std::filesystem::remove_all(p,ec);}} cleanup{dir};
    auto config=dir/L"配置.json";{
      std::ofstream file(config);file<<Json({{"asr",{{"native_enabled",true},{"worker_path",path_utf8(helper)},{"model_dir",path_utf8(dir)},{"use_punc",false},{"use_vad",false}}},{"normalization",{{"enabled",false}}}}).dump();
    }
    CoreClient core;core.start(core_exe,config);CaptureControl control;auto notify=[](const Json&){};
    if(which==L"pipeline") {
      for(int i=0;i<3;++i){auto result=dictate(core,nullptr,helper,{L"--capture"},control,false,notify);expect(result.value("success",false),result.dump().c_str());expect(result.value("frames",0)==16000,"truncated audio");expect(result.value("text","")=="Windows 中文录音完整 16000","final text mismatch");}
      auto empty=dictate(core,nullptr,helper,{L"--empty"},control,false,notify);expect(!empty.value("success",true),"empty recording accepted");
      auto malformed=dictate(core,nullptr,helper,{L"--bad-offset"},control,false,notify);expect(malformed.value("error","")=="invalid_pcm_packet","bad offset accepted");
      control.cancel.store(true);auto cancelled=dictate(core,nullptr,helper,{L"--silent"},control,false,notify);expect(cancelled.value("error","")=="cancelled","cancelled capture accepted");
    }else if(which==L"timeouts") {
      auto started=GetTickCount64();auto hung=dictate(core,nullptr,helper,{L"--silent"},control,false,notify);expect(hung.value("error","")=="microphone_start_timeout",hung.dump().c_str());expect(GetTickCount64()-started<8000,"startup watchdog failed");
      control.stop.store(true);started=GetTickCount64();auto stopped=dictate(core,nullptr,helper,{L"--silent"},control,false,notify);expect(stopped.value("error","")=="microphone_stop_timeout",stopped.dump().c_str());expect(GetTickCount64()-started<5000,"stop watchdog failed");
      control.stop.store(false);auto recovered=dictate(core,nullptr,helper,{L"--capture"},control,false,notify);expect(recovered.value("success",false),"record after timeout failed");
    }else throw std::runtime_error("unknown test case");
  }
  std::cout<<"PASS "<<utf8(which)<<'\n';return 0;
 }catch(const std::exception& e){std::cerr<<"FAIL "<<e.what()<<'\n';return 1;}
}
