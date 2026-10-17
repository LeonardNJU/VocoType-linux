#include "pipeline.hpp"
#include <shellapi.h>
#include <fstream>
#include <array>
#include <iostream>
#include <thread>
using namespace vocotype::windows;
namespace {
constexpr UINT down_message=WM_APP+1,up_message=WM_APP+2,event_message=WM_APP+3,escape_message=WM_APP+4,tray_message=WM_APP+5;
HWND main_window=nullptr,pane=nullptr,result_edit=nullptr;
HHOOK hook=nullptr; HFONT font=nullptr;
std::wstring pane_text=L"VocoType Windows preview";
std::wstring last_text;
CoreClient final_core,preview_core;
CaptureControl control;
std::thread work;
std::filesystem::path config_path;
bool ready=false,preview_ready=false,active=false,loading=false,exiting=false,key_held=false,escape_held=false;
bool control_down=false,alt_down=false,win_down=false,shift_down=false;
std::array<bool,256> modifier_keys{};
std::uint64_t generation=0;
struct Target { HWND root=nullptr,focus=nullptr;DWORD thread=0,pid=0; } target;
Target focused_target() {
  Target t;t.root=GetForegroundWindow();t.thread=GetWindowThreadProcessId(t.root,&t.pid);
  GUITHREADINFO info{};info.cbSize=sizeof(info);if(t.thread&&GetGUIThreadInfo(t.thread,&info))t.focus=info.hwndFocus;return t;
}
bool matches(const Target& t){auto now=focused_target();return t.root&&t.focus&&now.root==t.root&&now.focus==t.focus&&now.thread==t.thread&&now.pid==t.pid;}
bool modifiers_down(){for(int key:{VK_SHIFT,VK_CONTROL,VK_MENU,VK_LWIN,VK_RWIN})if(GetAsyncKeyState(key)&0x8000)return true;return false;}
std::string send_to_target(std::wstring_view text,const Target& t) {
  (void)unicode_events(text); // validate the entire UTF-16 string before insertion
  for(std::size_t offset=0;offset<text.size();) {
    if(!matches(t)||modifiers_down())return "Focus or modifiers changed; full result retained. Some earlier chunks may already be inserted.";
    auto end=std::min(text.size(),offset+64);
    if(end<text.size()&&text[end-1]>=0xd800&&text[end-1]<=0xdbff)--end;
    auto keys=unicode_events(text.substr(offset,end-offset));
    const UINT sent=SendInput(static_cast<UINT>(keys.size()),keys.data(),sizeof(INPUT));
    if(sent!=keys.size())return "Windows blocked or partially accepted text insertion (possibly elevated target). Full result retained; no automatic retry.";
    offset=end;
  }
  return {};
}
void post(Json value,std::uint64_t token) {
  value["generation"]=token;auto* message=new Json(std::move(value));
  if(!PostMessageW(main_window,event_message,0,reinterpret_cast<LPARAM>(message)))delete message;
}
void status(const std::wstring& text) {
  KillTimer(main_window,1);pane_text=text;InvalidateRect(pane,nullptr,TRUE);
  POINT point{};GetCursorPos(&point);MONITORINFO monitor{sizeof(monitor)};GetMonitorInfoW(MonitorFromPoint(point,MONITOR_DEFAULTTONEAREST),&monitor);
  SetWindowPos(pane,HWND_TOPMOST,monitor.rcWork.right-470,monitor.rcWork.bottom-145,440,100,SWP_NOACTIVATE|SWP_SHOWWINDOW);
}
void show_result(){SetWindowTextW(result_edit,last_text.c_str());ShowWindow(main_window,SW_SHOWNORMAL);SetForegroundWindow(main_window);}
void boot() {
  if(active||loading||exiting)return;if(work.joinable())work.join();loading=true;ready=false;preview_ready=false;
  const auto token=++generation;status(L"正在加载本地语音模型（此时不占用麦克风）…");
  work=std::thread([token]{
    try {
      std::ifstream input(config_path); if(!input)throw std::runtime_error("Missing windows.json. Run setup-models.ps1, or pass --config FILE.");Json config=Json::parse(input);
      auto exe=executable_path().parent_path()/L"vocotype-core.exe";
      final_core.start(exe,config_path,L"final");
      bool streaming=false;
      if(config.value("asr_streaming",Json::object()).value("enabled",false)) {
        try{preview_core.start(exe,config_path,L"preview");streaming=true;}catch(const std::exception& e){post({{"type","preview_unavailable"},{"error",e.what()}},token);}
      }
      post({{"type","ready"},{"preview",streaming}},token);
    }catch(const std::exception& e){post({{"type","boot_error"},{"error",e.what()}},token);}
  });
}
void begin(bool polish) {
  if(active||exiting)return;
  if(!ready){status(L"后端尚未就绪。请查看配置；托盘菜单可重新加载。");return;}
  if(work.joinable())work.join();target=focused_target();
  control.stop.store(false);control.cancel.store(false);active=true;const auto token=++generation;
  status(L"正在启动麦克风…");
  work=std::thread([token,polish]{
    auto notify=[token](const Json& event){post(event,token);};
    std::vector<std::wstring> args;
    try {std::ifstream file(config_path);auto config=Json::parse(file);auto id=config.value("windows",Json::object()).value("device_id","");if(!id.empty())args={L"--device-id",wide(id)};}catch(...){}
    auto result=dictate(final_core,preview_ready?&preview_core:nullptr,executable_path().parent_path()/L"vocotype-wasapi-recorder.exe",args,control,polish,notify);
    result["type"]="done";post(result,token);
  });
}
void quit() {
  exiting=true;control.cancel.store(true);control.stop.store(true);final_core.cancel();preview_core.cancel();
  if(work.joinable()) {status(L"正在停止录音并关闭子进程…");SetTimer(main_window,2,100,nullptr);}
  else DestroyWindow(main_window);
}
LRESULT CALLBACK pane_proc(HWND window,UINT message,WPARAM wp,LPARAM lp) {
  if(message==WM_MOUSEACTIVATE)return MA_NOACTIVATE;
  if(message==WM_PAINT){PAINTSTRUCT paint{};HDC dc=BeginPaint(window,&paint);RECT rect{};GetClientRect(window,&rect);FillRect(dc,&rect,GetSysColorBrush(COLOR_WINDOW));InflateRect(&rect,-14,-14);SelectObject(dc,font);SetBkMode(dc,TRANSPARENT);SetTextColor(dc,GetSysColor(COLOR_WINDOWTEXT));DrawTextW(dc,pane_text.c_str(),-1,&rect,DT_WORDBREAK|DT_NOPREFIX|DT_EDITCONTROL);EndPaint(window,&paint);return 0;}
  return DefWindowProcW(window,message,wp,lp);
}
LRESULT CALLBACK window_proc(HWND window,UINT message,WPARAM wp,LPARAM lp) {
  try {
    if(message==down_message){begin(wp!=0);return 0;}
    if(message==up_message){control.stop.store(true);return 0;}
    if(message==escape_message){control.cancel.store(true);control.stop.store(true);return 0;}
    if(message==event_message){
      std::unique_ptr<Json> data(reinterpret_cast<Json*>(lp));if(data->value("generation",std::uint64_t(0))!=generation)return 0;
      auto type=data->value("type","");
      if(type=="ready"||type=="boot_error"||type=="done") {
        if(work.joinable())work.join();
        if(exiting){DestroyWindow(window);return 0;}
      }
      if(type=="ready"){loading=false;ready=true;preview_ready=data->value("preview",false);status(L"就绪：按住 F9 说话，松手上屏；Esc 取消。");SetTimer(window,1,2500,nullptr);}
      else if(type=="boot_error"){loading=false;ready=false;status(L"后端启动失败。托盘中查看详情、重新加载。");last_text=wide(data->value("error",""));show_result();}
      else if(type=="starting")status(L"正在启动麦克风…");
      else if(type=="recording")status(L"正在听… 松开 F9 结束，Esc 取消。");
      else if(type=="partial"&&active)status(preview_tail(wide(data->value("text",""))));
      else if(type=="finalizing")status(L"录音已停止，正在完成识别…");
      else if(type=="preview_unavailable"){if(active)status(L"实时预览暂不可用；完整录音仍用于最终识别。");}
      else if(type=="done") {
        active=false;ready=final_core.healthy();
        if(control.cancel.load()){if(!ready){boot();return 0;}status(L"已取消");SetTimer(window,1,1500,nullptr);return 0;}
        if(!data->value("success",false)){status(L"录音或识别失败。可重试；托盘中查看错误。");last_text=wide(data->value("error",""));show_result();return 0;}
        last_text=wide(data->value("text",""));SetWindowTextW(result_edit,last_text.c_str());
        if(last_text.empty()){status(L"没有识别到文字");SetTimer(window,1,2000,nullptr);return 0;}
        auto failure=send_to_target(last_text,target);
        if(!failure.empty()){status(L"未能完整上屏，全文已保留，避免误输入或重复输入。");show_result();}
        else{status(L"文字已发送到原窗口。托盘中可查看全文。");SetTimer(window,1,2000,nullptr);}
      }
      return 0;
    }
    if(message==tray_message && (lp==WM_RBUTTONUP || lp==WM_CONTEXTMENU)){
      HMENU menu=CreatePopupMenu();AppendMenuW(menu,MF_STRING,10,L"查看上次结果 / 错误");AppendMenuW(menu,(active||loading)?MF_GRAYED:MF_STRING,11,L"重新加载后端");AppendMenuW(menu,MF_STRING,12,L"退出 VocoType");
      POINT cursor{};GetCursorPos(&cursor);SetForegroundWindow(window);UINT command=TrackPopupMenu(menu,TPM_RETURNCMD|TPM_RIGHTBUTTON,cursor.x,cursor.y,0,window,nullptr);DestroyMenu(menu);
      if(command==10)show_result();else if(command==11)boot();else if(command==12)quit();return 0;
    }
    if(message==tray_message && lp==WM_LBUTTONDBLCLK){show_result();return 0;}
    if(message==WM_SIZE && result_edit){MoveWindow(result_edit,12,12,LOWORD(lp)-24,HIWORD(lp)-24,TRUE);return 0;}
    if(message==WM_TIMER&&wp==1){if(!active)ShowWindow(pane,SW_HIDE);KillTimer(window,1);return 0;}
    if(message==WM_CLOSE){ShowWindow(window,SW_HIDE);return 0;}
    if(message==WM_DESTROY){NOTIFYICONDATAW icon{};icon.cbSize=sizeof(icon);icon.hWnd=window;icon.uID=1;Shell_NotifyIconW(NIM_DELETE,&icon);PostQuitMessage(0);return 0;}
  }catch(const std::exception& e){last_text=wide(e.what());active=false;show_result();}
  return DefWindowProcW(window,message,wp,lp);
}
LRESULT CALLBACK keyboard_proc(int code,WPARAM message,LPARAM data) {
  if(code==HC_ACTION) {
    auto* event=reinterpret_cast<KBDLLHOOKSTRUCT*>(data);
    if(!(event->flags&LLKHF_INJECTED)) {
      const bool down=message==WM_KEYDOWN||message==WM_SYSKEYDOWN;
      // Only shortcut/modifier state; never log other keys or do I/O here.
      if(event->vkCode==VK_LCONTROL||event->vkCode==VK_RCONTROL||
         event->vkCode==VK_LMENU||event->vkCode==VK_RMENU||
         event->vkCode==VK_LWIN||event->vkCode==VK_RWIN||
         event->vkCode==VK_LSHIFT||event->vkCode==VK_RSHIFT) {
        modifier_keys[event->vkCode]=down;
        control_down=modifier_keys[VK_LCONTROL]||modifier_keys[VK_RCONTROL];
        alt_down=modifier_keys[VK_LMENU]||modifier_keys[VK_RMENU];
        win_down=modifier_keys[VK_LWIN]||modifier_keys[VK_RWIN];
        shift_down=modifier_keys[VK_LSHIFT]||modifier_keys[VK_RSHIFT];
      }
      if(event->vkCode==VK_F9) {
        if(down && !key_held && !control_down&&!alt_down&&!win_down){key_held=true;PostMessageW(main_window,down_message,shift_down?1:0,0);return 1;}
        if(key_held){if(!down){key_held=false;PostMessageW(main_window,up_message,0,0);}return 1;}
      }
      if(event->vkCode==VK_ESCAPE&&(active||escape_held)){if(down){escape_held=true;PostMessageW(main_window,escape_message,0,0);}else escape_held=false;return 1;}
    }
  }
  return CallNextHookEx(hook,code,message,data);
}
int gui() {
  Handle instance(CreateMutexW(nullptr,FALSE,L"Local\\VocoType-Windows-Preview"));check(static_cast<bool>(instance),"single instance");
  if(GetLastError()==ERROR_ALREADY_EXISTS)throw std::runtime_error("VocoType is already running in the tray");
  FreeConsole();HINSTANCE module=GetModuleHandleW(nullptr);
  WNDCLASSW cls{};cls.hInstance=module;cls.lpfnWndProc=window_proc;cls.lpszClassName=L"VocoTypeWindowsPreview";cls.hCursor=LoadCursorW(nullptr,IDC_ARROW);cls.hbrBackground=GetSysColorBrush(COLOR_WINDOW);check(RegisterClassW(&cls),"register window");
  main_window=CreateWindowExW(0,cls.lpszClassName,L"VocoType — 完整结果（Ctrl+A / Ctrl+C 复制）",WS_OVERLAPPEDWINDOW,CW_USEDEFAULT,CW_USEDEFAULT,700,400,nullptr,nullptr,module,nullptr);check(main_window!=nullptr,"create result window");
  result_edit=CreateWindowExW(WS_EX_CLIENTEDGE,L"EDIT",L"",WS_CHILD|WS_VISIBLE|WS_VSCROLL|ES_MULTILINE|ES_READONLY|ES_AUTOVSCROLL,12,12,660,330,main_window,nullptr,module,nullptr);
  font=CreateFontW(-20,0,0,0,FW_NORMAL,FALSE,FALSE,FALSE,DEFAULT_CHARSET,OUT_DEFAULT_PRECIS,CLIP_DEFAULT_PRECIS,CLEARTYPE_QUALITY,DEFAULT_PITCH,L"Segoe UI");SendMessageW(result_edit,WM_SETFONT,reinterpret_cast<WPARAM>(font),TRUE);
  cls.lpfnWndProc=pane_proc;cls.lpszClassName=L"VocoTypePreviewPane";check(RegisterClassW(&cls),"register preview");
  pane=CreateWindowExW(WS_EX_NOACTIVATE|WS_EX_TOOLWINDOW|WS_EX_TOPMOST,cls.lpszClassName,L"",WS_POPUP|WS_BORDER,0,0,440,100,nullptr,nullptr,module,nullptr);check(pane!=nullptr,"create preview");
  NOTIFYICONDATAW icon{};icon.cbSize=sizeof(icon);icon.hWnd=main_window;icon.uID=1;icon.uFlags=NIF_MESSAGE|NIF_ICON|NIF_TIP;icon.uCallbackMessage=tray_message;icon.hIcon=LoadIconW(nullptr,IDI_APPLICATION);wcscpy_s(icon.szTip,L"VocoType Windows Preview — F9");check(Shell_NotifyIconW(NIM_ADD,&icon),"create tray icon");
  control_down=(GetAsyncKeyState(VK_CONTROL)&0x8000)!=0;alt_down=(GetAsyncKeyState(VK_MENU)&0x8000)!=0;shift_down=(GetAsyncKeyState(VK_SHIFT)&0x8000)!=0;win_down=((GetAsyncKeyState(VK_LWIN)|GetAsyncKeyState(VK_RWIN))&0x8000)!=0;
  for(int key:{VK_LCONTROL,VK_RCONTROL,VK_LMENU,VK_RMENU,VK_LWIN,VK_RWIN,VK_LSHIFT,VK_RSHIFT})modifier_keys[key]=(GetAsyncKeyState(key)&0x8000)!=0;
  hook=SetWindowsHookExW(WH_KEYBOARD_LL,keyboard_proc,module,0);check(hook!=nullptr,"install F9 hook");boot();
  MSG message{};int result;while((result=GetMessageW(&message,nullptr,0,0))>0){TranslateMessage(&message);DispatchMessageW(&message);}
  UnhookWindowsHookEx(hook);control.cancel.store(true);final_core.cancel();preview_core.cancel();if(work.joinable())work.join();DestroyWindow(pane);DeleteObject(font);return result<0?1:0;
}
}
int wmain(int argc,wchar_t** argv) {
  try {
    std::wstring operation;std::filesystem::path audio;int duration=0;config_path=data_dir()/L"windows.json";
    for(int i=1;i<argc;++i){std::wstring_view arg=argv[i];
      if(arg==L"--help"){std::cout<<"VocoType Windows preview: F9 hold/release, Shift+F9 polish, Escape cancel.\n--config FILE --audio-probe --self-test --transcribe WAV --record-ms N\n";return 0;}
      if(arg==L"--config"&&i+1<argc)config_path=std::filesystem::absolute(argv[++i]);
      else if(arg==L"--transcribe"&&i+1<argc){operation=L"transcribe";audio=std::filesystem::absolute(argv[++i]);}
      else if(arg==L"--record-ms"&&i+1<argc){operation=L"record";duration=std::stoi(argv[++i]);if(duration<1||duration>900000)throw std::runtime_error("duration out of range");}
      else if(arg==L"--audio-probe")operation=L"probe";
      else if(arg==L"--self-test")operation=L"test";
      else throw std::runtime_error("unknown argument");
    }
    if(operation==L"test"){std::wstring sample=L"中文\U0001F600";if(wide(utf8(sample))!=sample)throw std::runtime_error("unicode failed");std::cout<<"{\"success\":true,\"platform\":\"windows\"}\n";return 0;}
    if(operation==L"probe"){ChildProcess mic;mic.start(executable_path().parent_path()/L"vocotype-wasapi-recorder.exe",{L"--probe"});std::cout<<mic.read_line(5000)<<'\n';return 0;}
    if(operation==L"transcribe"||operation==L"record"){
      CoreClient core;core.start(executable_path().parent_path()/L"vocotype-core.exe",config_path);CaptureControl state;auto event=[](const Json& e){std::cerr<<e.dump()<<'\n';};
      Json result=operation==L"transcribe"?transcribe_file(core,audio,state,false,event):dictate(core,nullptr,executable_path().parent_path()/L"vocotype-wasapi-recorder.exe",{L"--duration-ms",std::to_wstring(duration)},state,false,event);
      std::cout<<result.dump()<<'\n';return result.value("success",false)?0:2;
    }
    return gui();
  }catch(const std::exception& e){std::cerr<<e.what()<<'\n';if(argc==1)MessageBoxW(nullptr,wide(e.what()).c_str(),L"VocoType",MB_OK|MB_ICONERROR);return 1;}
}
