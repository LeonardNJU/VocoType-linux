#include "win_support.hpp"
#include <sddl.h>
#include <shlobj.h>
#include <wincrypt.h>
#include <algorithm>
#include <chrono>
#include <climits>
#include <stdexcept>
#include <utility>
namespace vocotype::windows {
void check(BOOL ok,const char* action) {
  if(!ok) throw std::runtime_error(std::string(action)+" (Win32 "+std::to_string(GetLastError())+")");
}
std::wstring wide(std::string_view text) {
  if(text.empty()) return {};
  if(text.size()>INT_MAX) throw std::runtime_error("text too large");
  int n=MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,text.data(),static_cast<int>(text.size()),nullptr,0);
  check(n>0,"invalid UTF-8"); std::wstring out(static_cast<std::size_t>(n),L'\0');
  check(MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,text.data(),static_cast<int>(text.size()),out.data(),n)>0,"decode UTF-8"); return out;
}
std::string utf8(std::wstring_view text) {
  if(text.empty()) return {};
  if(text.size()>INT_MAX) throw std::runtime_error("text too large");
  int n=WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,text.data(),static_cast<int>(text.size()),nullptr,0,nullptr,nullptr);
  check(n>0,"invalid UTF-16"); std::string out(static_cast<std::size_t>(n),'\0');
  check(WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,text.data(),static_cast<int>(text.size()),out.data(),n,nullptr,nullptr)>0,"encode UTF-8"); return out;
}
std::string path_utf8(const std::filesystem::path& p) { return utf8(p.native()); }
std::filesystem::path executable_path() {
  std::wstring s(32768,L'\0'); DWORD n=GetModuleFileNameW(nullptr,s.data(),static_cast<DWORD>(s.size()));
  check(n>0 && n<s.size(),"executable path"); s.resize(n); return s;
}
std::filesystem::path data_dir() {
  PWSTR raw=nullptr;
  if(FAILED(SHGetKnownFolderPath(FOLDERID_LocalAppData,0,nullptr,&raw))) throw std::runtime_error("LocalAppData unavailable");
  std::filesystem::path p(raw); CoTaskMemFree(raw); p/=L"VocoType";
  std::filesystem::create_directories(p); return p;
}
std::wstring quote_argument(std::wstring_view text) {
  std::wstring out=L"\""; std::size_t slashes=0;
  for(wchar_t c:text) {
    if(c==L'\\') { ++slashes; continue; }
    if(c==L'\"') { out.append(slashes*2+1,L'\\'); out+=c; }
    else { out.append(slashes,L'\\'); out+=c; }
    slashes=0;
  }
  out.append(slashes*2,L'\\'); out+=L'\"'; return out;
}
std::string base64(const void* data,std::size_t bytes) {
  if(bytes==0) return {};
  if(bytes>MAXDWORD) throw std::runtime_error("PCM too large");
  DWORD n=0;
  check(CryptBinaryToStringA(static_cast<const BYTE*>(data),static_cast<DWORD>(bytes),CRYPT_STRING_BASE64|CRYPT_STRING_NOCRLF,nullptr,&n),"encode PCM");
  std::string s(n,'\0');
  check(CryptBinaryToStringA(static_cast<const BYTE*>(data),static_cast<DWORD>(bytes),CRYPT_STRING_BASE64|CRYPT_STRING_NOCRLF,s.data(),&n),"encode PCM");
  while(!s.empty() && s.back()=='\0') s.pop_back(); return s;
}
std::vector<unsigned char> unbase64(const std::string& s) {
  if(s.empty()) return {};
  if(s.size()>1024*1024) throw std::runtime_error("PCM message too large");
  DWORD n=0;
  check(CryptStringToBinaryA(s.data(),static_cast<DWORD>(s.size()),CRYPT_STRING_BASE64|CRYPT_STRING_STRICT,nullptr,&n,nullptr,nullptr),"decode PCM");
  std::vector<unsigned char> b(n);
  check(CryptStringToBinaryA(s.data(),static_cast<DWORD>(s.size()),CRYPT_STRING_BASE64|CRYPT_STRING_STRICT,b.data(),&n,nullptr,nullptr),"decode PCM"); b.resize(n); return b;
}
std::wstring preview_tail(std::wstring_view text,std::size_t limit) {
  std::size_t start=text.size(),count=0;
  while(start>0 && count<limit) {
    --start;
    if(text[start]>=0xdc00 && text[start]<=0xdfff && start>0 && text[start-1]>=0xd800 && text[start-1]<=0xdbff) --start;
    ++count;
  }
  std::wstring result(start?L"\u2026":L""); result.append(text.substr(start));
  std::replace(result.begin(),result.end(),L'\n',L' '); std::replace(result.begin(),result.end(),L'\r',L' '); return result;
}
std::vector<INPUT> unicode_events(std::wstring_view text) {
  (void)utf8(text); // reject unpaired surrogates before any input is sent
  std::vector<INPUT> result; result.reserve(text.size()*2);
  for(wchar_t c:text) {
    INPUT down{}; down.type=INPUT_KEYBOARD; down.ki.wScan=static_cast<WORD>(c); down.ki.dwFlags=KEYEVENTF_UNICODE;
    result.push_back(down); down.ki.dwFlags|=KEYEVENTF_KEYUP; result.push_back(down);
  }
  return result;
}
Security::Security() {
  HANDLE token_raw=nullptr; check(OpenProcessToken(GetCurrentProcess(),TOKEN_QUERY,&token_raw),"open process token"); Handle token(token_raw);
  DWORD bytes=0; GetTokenInformation(token.get(),TokenGroups,nullptr,0,&bytes);
  std::vector<BYTE> info(bytes); check(GetTokenInformation(token.get(),TokenGroups,info.data(),bytes,&bytes),"read logon SID");
  auto* groups=reinterpret_cast<TOKEN_GROUPS*>(info.data()); PSID sid=nullptr;
  for(DWORD i=0;i<groups->GroupCount;++i) if((groups->Groups[i].Attributes&SE_GROUP_LOGON_ID)==SE_GROUP_LOGON_ID) { sid=groups->Groups[i].Sid; break; }
  // Service runners may have no interactive logon SID; restrict to their user.
  std::vector<BYTE> user;
  if(!sid) { GetTokenInformation(token.get(),TokenUser,nullptr,0,&bytes); user.resize(bytes); check(GetTokenInformation(token.get(),TokenUser,user.data(),bytes,&bytes),"read user SID"); sid=reinterpret_cast<TOKEN_USER*>(user.data())->User.Sid; }
  LPWSTR text=nullptr; check(ConvertSidToStringSidW(sid,&text),"format SID");
  std::wstring sddl=L"D:P(A;;GA;;;SY)(A;;GA;;;"; sddl+=text; sddl+=L")"; LocalFree(text);
  check(ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl.c_str(),SDDL_REVISION_1,&descriptor_,nullptr),"private security descriptor");
  attributes.nLength=sizeof(attributes); attributes.lpSecurityDescriptor=descriptor_; attributes.bInheritHandle=FALSE;
}
namespace {
struct Pipe { Handle parent, child; };
Pipe create_pipe(bool parent_writes) {
  GUID guid{}; if(FAILED(CoCreateGuid(&guid))) throw std::runtime_error("pipe GUID failed");
  wchar_t id[40]{}; StringFromGUID2(guid,id,40);
  std::wstring name=L"\\\\.\\pipe\\VocoType-"; name+=id;
  Security security;
  Handle parent(CreateNamedPipeW(name.c_str(),(parent_writes?PIPE_ACCESS_OUTBOUND:PIPE_ACCESS_INBOUND)|FILE_FLAG_OVERLAPPED|FILE_FLAG_FIRST_PIPE_INSTANCE,
       PIPE_TYPE_BYTE|PIPE_READMODE_BYTE|PIPE_WAIT|PIPE_REJECT_REMOTE_CLIENTS,1,65536,65536,0,&security.attributes));
  check(static_cast<bool>(parent),"create private pipe");
  security.attributes.bInheritHandle=TRUE;
  Handle child(CreateFileW(name.c_str(),parent_writes?GENERIC_READ:GENERIC_WRITE,0,&security.attributes,OPEN_EXISTING,0,nullptr));
  check(static_cast<bool>(child),"connect private pipe");
  OVERLAPPED connection{}; Handle event(CreateEventW(nullptr,TRUE,FALSE,nullptr)); check(static_cast<bool>(event),"pipe event"); connection.hEvent=event.get();
  if(!ConnectNamedPipe(parent.get(),&connection)) {
    DWORD error=GetLastError();
    if(error==ERROR_IO_PENDING) { DWORD count=0; if(WaitForSingleObject(event.get(),1000)!=WAIT_OBJECT_0) { CancelIoEx(parent.get(),&connection); GetOverlappedResult(parent.get(),&connection,&count,TRUE); throw std::runtime_error("pipe connect timeout"); } check(GetOverlappedResult(parent.get(),&connection,&count,FALSE),"connect pipe"); }
    else if(error!=ERROR_PIPE_CONNECTED) throw std::runtime_error("pipe connection failed");
  }
  return {std::move(parent),std::move(child)};
}
DWORD transfer(HANDLE pipe,void* data,DWORD bytes,bool write,int timeout) {
  OVERLAPPED ov{}; Handle event(CreateEventW(nullptr,TRUE,FALSE,nullptr)); check(static_cast<bool>(event),"I/O event"); ov.hEvent=event.get();
  DWORD count=0; BOOL ok=write?WriteFile(pipe,data,bytes,&count,&ov):ReadFile(pipe,data,bytes,&count,&ov);
  if(!ok) {
    DWORD error=GetLastError();
    if(error!=ERROR_IO_PENDING) throw std::runtime_error("pipe closed (Win32 "+std::to_string(error)+")");
    DWORD waited=WaitForSingleObject(event.get(),static_cast<DWORD>(std::max(1,timeout)));
    if(waited!=WAIT_OBJECT_0) { CancelIoEx(pipe,&ov); if(GetOverlappedResult(pipe,&ov,&count,TRUE) && count>0) return count; throw std::runtime_error("worker_request_timeout"); }
    check(GetOverlappedResult(pipe,&ov,&count,FALSE),"pipe I/O");
  }
  if(count==0) throw std::runtime_error("worker_exited"); return count;
}
}
void ChildProcess::start(const std::filesystem::path& exe,const std::vector<std::wstring>& args) {
  stop(); auto in=create_pipe(true); auto out=create_pipe(false);
  SECURITY_ATTRIBUTES inherit{sizeof(SECURITY_ATTRIBUTES),nullptr,TRUE};
  Handle err(CreateFileW(L"NUL",GENERIC_WRITE,FILE_SHARE_READ|FILE_SHARE_WRITE,&inherit,OPEN_EXISTING,0,nullptr)); check(static_cast<bool>(err),"child stderr");
  SIZE_T size=0; InitializeProcThreadAttributeList(nullptr,1,0,&size); std::vector<BYTE> storage(size);
  auto* attributes=reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(storage.data());
  check(InitializeProcThreadAttributeList(attributes,1,0,&size),"process attributes");
  struct Cleanup { LPPROC_THREAD_ATTRIBUTE_LIST p; ~Cleanup(){DeleteProcThreadAttributeList(p);} } cleanup{attributes};
  HANDLE inherited[]={in.child.get(),out.child.get(),err.get()};
  check(UpdateProcThreadAttribute(attributes,0,PROC_THREAD_ATTRIBUTE_HANDLE_LIST,inherited,sizeof(inherited),nullptr,nullptr),"inherit stdio only");
  STARTUPINFOEXW startup{}; startup.StartupInfo.cb=sizeof(startup); startup.StartupInfo.dwFlags=STARTF_USESTDHANDLES;
  startup.StartupInfo.hStdInput=in.child.get(); startup.StartupInfo.hStdOutput=out.child.get(); startup.StartupInfo.hStdError=err.get(); startup.lpAttributeList=attributes;
  job_.reset(CreateJobObjectW(nullptr,nullptr)); check(static_cast<bool>(job_),"create worker job");
  JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{}; limits.BasicLimitInformation.LimitFlags=JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
  check(SetInformationJobObject(job_.get(),JobObjectExtendedLimitInformation,&limits,sizeof(limits)),"worker tree lifetime");
  std::wstring command=quote_argument(exe.native()); for(auto& arg:args) command+=L" "+quote_argument(arg);
  PROCESS_INFORMATION pi{};
  check(CreateProcessW(exe.c_str(),command.data(),nullptr,nullptr,TRUE,CREATE_NO_WINDOW|CREATE_SUSPENDED|EXTENDED_STARTUPINFO_PRESENT,nullptr,exe.parent_path().c_str(),&startup.StartupInfo,&pi),"launch worker");
  process_.reset(pi.hProcess); Handle thread(pi.hThread);
  if(!AssignProcessToJobObject(job_.get(),process_.get())) { TerminateProcess(process_.get(),1); stop(); throw std::runtime_error("cannot contain worker process"); }
  input_=std::move(in.parent); output_=std::move(out.parent);
  if(ResumeThread(thread.get())==static_cast<DWORD>(-1)) { stop(); throw std::runtime_error("cannot resume worker"); }
}
bool ChildProcess::alive() const { return process_ && WaitForSingleObject(process_.get(),0)==WAIT_TIMEOUT; }
DWORD ChildProcess::pid() const { return process_?GetProcessId(process_.get()):0; }
void ChildProcess::write_line(const std::string& line,int timeout_ms) {
  if(line.size()>1024*1024) throw std::runtime_error("worker_request_too_large");
  std::string s=line+"\n"; std::size_t offset=0; const auto deadline=GetTickCount64()+static_cast<ULONGLONG>(std::max(1,timeout_ms));
  while(offset<s.size()) { if(GetTickCount64()>=deadline) throw std::runtime_error("worker_request_timeout"); offset+=transfer(input_.get(),s.data()+offset,static_cast<DWORD>(s.size()-offset),true,static_cast<int>(deadline-GetTickCount64())); }
}
std::string ChildProcess::read_line(int timeout_ms) {
  const auto deadline=GetTickCount64()+static_cast<ULONGLONG>(std::max(1,timeout_ms));
  for(;;) {
    auto n=buffer_.find('\n');
    if(n!=std::string::npos) { if(n>1024*1024) throw std::runtime_error("worker_response_too_large"); auto s=buffer_.substr(0,n); buffer_.erase(0,n+1); return s; }
    if(buffer_.size()>1024*1024) throw std::runtime_error("worker_response_too_large");
    if(GetTickCount64()>=deadline) throw std::runtime_error("worker_request_timeout");
    char b[8192]; DWORD bytes=transfer(output_.get(),b,sizeof(b),false,static_cast<int>(deadline-GetTickCount64())); buffer_.append(b,bytes);
  }
}
void ChildProcess::stop() noexcept {
  input_.reset();
  if(job_) TerminateJobObject(job_.get(),1);
  if(process_) WaitForSingleObject(process_.get(),1000);
  job_.reset(); process_.reset(); output_.reset(); buffer_.clear();
}
}
