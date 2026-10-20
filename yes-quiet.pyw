"""yes-quiet — 메신저 알림 일괄 ON/OFF. 앱은 끄지 않는다.

OFF 동안:
  - 목록 앱이 새로 띄우는 창(알림 팝업)을 즉시 숨긴다. 카카오톡·텔레그램 자체 팝업용.
  - 목록 앱의 소리를 음소거한다.
  - Windows 알림(토스트)을 쓰는 앱은 Windows 알림 설정을 끈다.
ON으로 돌리면 감시를 멈추고 소리·Windows 알림 설정을 원래대로 돌린다.
"""
import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import winreg
from ctypes import wintypes
from pathlib import Path

__version__ = "1.0.0"

# exe로 묶으면 아이콘은 임시 압축 해제 폴더(_MEIPASS)에, 설정·기록은 사용자별 %APPDATA%\yes-quiet에 둔다.
RES = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
DATA = Path(os.environ["APPDATA"]) / "yes-quiet"
DATA.mkdir(exist_ok=True)
CONFIG = DATA / "config.json"
NO_WINDOW = 0x08000000
TOAST_KEY = r"Software\Microsoft\Windows\CurrentVersion\Notifications\Settings"

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


# ---------- 설정 파일 ----------

def load():
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"state": "on", "apps": [], "toast_backup": {}}


def save(cfg):
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def ps(command):
    return subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True,
                          text=True, encoding="utf-8", creationflags=NO_WINDOW).stdout


# ---------- 앱 목록 ----------

def package_family(path):
    """...\\WindowsApps\\5319275A.WhatsAppDesktop_2.2639.100.0_x64__cv1g1gvanyjgm\\... -> 5319275A.WhatsAppDesktop_cv1g1gvanyjgm"""
    m = re.search(r"\\WindowsApps\\([^\\_]+)_[^\\]*__([^\\]+)\\", path, re.I)
    return f"{m.group(1)}_{m.group(2)}" if m else None


def find_aumid(name, path, start_apps):
    """Windows 알림 설정 키 이름(AppUserModelID). 스토어 앱은 패키지 이름으로, 일반 앱은 시작 메뉴 이름으로 찾는다."""
    family = package_family(path)
    for a in start_apps:
        if family and a["AppID"].lower().startswith(family.lower() + "!"):
            return a["AppID"]
        if not family and a["Name"].lower() == name.lower():
            return a["AppID"]
    return None


def running_apps():
    raw = ps("Get-Process | Where-Object { $_.Path -and $_.Path -notlike 'C:\\Windows\\*' } | "
             "Select-Object ProcessName, Path -Unique | ConvertTo-Json -Compress")
    procs = json.loads(raw or "[]")
    procs = procs if isinstance(procs, list) else [procs]
    start_apps = json.loads(ps("Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress") or "[]")
    seen, apps = set(), []
    for p in sorted(procs, key=lambda p: p["ProcessName"].lower()):
        if p["ProcessName"].lower() in seen:
            continue
        seen.add(p["ProcessName"].lower())
        apps.append({"name": p["ProcessName"], "process": p["ProcessName"] + ".exe",
                     "aumid": find_aumid(p["ProcessName"], p["Path"], start_apps)})
    return apps


# ---------- Windows 알림(토스트) 설정 ----------

def set_toast(aumid, enabled):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"{TOAST_KEY}\\{aumid}") as k:
        if enabled is None:
            try:
                winreg.DeleteValue(k, "Enabled")
            except FileNotFoundError:
                pass
        else:
            winreg.SetValueEx(k, "Enabled", 0, winreg.REG_DWORD, int(enabled))


def get_toast(aumid):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{TOAST_KEY}\\{aumid}") as k:
            return winreg.QueryValueEx(k, "Enabled")[0]
    except FileNotFoundError:
        return None  # 값이 없음 = Windows 기본값(켜짐)


# ---------- 창 감시 ----------

def exe_of(pid, cache):
    if pid not in cache:
        name = ""
        h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                name = Path(buf.value).name.lower()
            kernel32.CloseHandle(h)
        cache[pid] = name
    return cache[pid]


def visible_windows(targets, cache):
    found = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if exe_of(pid.value, cache) in targets:
                found.append(hwnd)
        return True

    user32.EnumWindows(proc(cb), 0)
    return found


LOG = DATA / "yes-quiet.log"


def window_info(hwnd):
    title = ctypes.create_unicode_buffer(200)
    user32.GetWindowTextW(hwnd, title, 200)
    cls = ctypes.create_unicode_buffer(200)
    user32.GetClassNameW(hwnd, cls, 200)
    ex = user32.GetWindowLongW(hwnd, -20) & 0xFFFFFFFF  # GWL_EXSTYLE
    return {"title": title.value, "class": cls.value,
            "tool": bool(ex & 0x80), "topmost": bool(ex & 0x8),
            "foreground": hwnd == user32.GetForegroundWindow()}


def is_popup(info):
    """알림 팝업 = 도구창(작업표시줄에 안 뜸)이거나 항상 위 창이면서, 내가 클릭해 앞으로 가져온 창이 아님.
    채팅창은 둘 다 아니라서 남는다 (카카오톡 EVA_Window_Dblclk 채팅창으로 확인)."""
    if info["class"] in ("tooltips_class32", "SysShadow", "#32768"):  # 툴팁·그림자·메뉴는 팝업 아님
        return False
    return (info["tool"] or info["topmost"]) and not info["foreground"]


def mute(targets, muted):
    from pycaw.pycaw import AudioUtilities
    for s in AudioUtilities.GetAllSessions():
        if s.Process and s.Process.name().lower() in targets:
            s.SimpleAudioVolume.SetMute(1 if muted else 0, None)


class Watcher(threading.Thread):
    """OFF 순간 이미 보이던 창(메인 창 등)은 두고, 그 뒤에 새로 보이는 창만 숨긴다."""

    def __init__(self, targets):
        super().__init__(daemon=True)
        self.targets = targets
        self.stop_event = threading.Event()

    def run(self):
        import comtypes
        comtypes.CoInitialize()
        cache = {}
        keep = set(visible_windows(self.targets, cache))
        tick = 0
        # shortcut: 0.1초 폴링이라 팝업이 순간 깜빡일 수 있음, 거슬리면 SetWinEventHook(EVENT_OBJECT_SHOW)로 교체
        while not self.stop_event.wait(0.1):
            for hwnd in visible_windows(self.targets, cache):
                if hwnd in keep:
                    continue
                info = window_info(hwnd)
                popup = is_popup(info)
                if popup:
                    user32.ShowWindow(hwnd, 0)  # SW_HIDE
                else:
                    keep.add(hwnd)  # 채팅창 등은 한 번 판정하면 다시 보지 않는다
                with LOG.open("a", encoding="utf-8") as f:
                    f.write(f"{time.strftime('%H:%M:%S')} {'숨김' if popup else '유지'} {info}\n")
            tick += 1
            if tick % 10 == 0:  # 소리 세션은 앱이 처음 소리를 낼 때 생기므로 1초마다 다시 음소거
                mute(self.targets, True)
                cache.clear()  # 새로 뜬 프로세스의 pid 재사용 대비
        mute(self.targets, False)
        comtypes.CoUninitialize()


# ---------- 화면 ----------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"yes-quiet {__version__}")
        self.icons = [tk.PhotoImage(file=RES / n) for n in ("icon.png", "icon-32.png")]  # make_icon.py로 생성
        self.iconphoto(True, *self.icons)
        self.geometry("320x420")
        self.cfg = load()
        self.cfg.setdefault("toast_backup", {})
        self.watcher = None

        self.button = tk.Button(self, font=("Malgun Gothic", 20, "bold"), height=2, command=self.toggle)
        self.button.pack(fill="x", padx=16, pady=16)
        tk.Label(self, text="대상 앱", anchor="w").pack(fill="x", padx=16)
        self.listbox = tk.Listbox(self, height=10)
        self.listbox.pack(fill="both", expand=True, padx=16)
        row = tk.Frame(self)
        row.pack(fill="x", padx=16, pady=12)
        tk.Button(row, text="실행 중인 앱에서 추가", command=self.add).pack(side="left")
        tk.Button(row, text="삭제", command=self.remove).pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        if self.cfg["state"] == "off":  # 꺼진 상태로 종료됐다면 감시를 이어서 한다
            self.start_watch()
        self.refresh()

    def targets(self):
        return {a["process"].lower() for a in self.cfg["apps"]}

    def start_watch(self):
        self.watcher = Watcher(self.targets())
        self.watcher.start()

    def stop_watch(self):
        if self.watcher:
            self.watcher.stop_event.set()
            self.watcher.join(timeout=3)
            self.watcher = None

    def refresh(self):
        on = self.cfg["state"] == "on"
        self.button.config(text="알림 ON" if on else "알림 OFF", bg="#2e7d32" if on else "#9e9e9e", fg="white")
        self.listbox.delete(0, "end")
        for a in self.cfg["apps"]:
            self.listbox.insert("end", a["name"] + ("  (Windows 알림)" if a.get("aumid") else ""))

    def toggle(self):
        if self.cfg["state"] == "on":
            for a in self.cfg["apps"]:
                if a.get("aumid"):
                    self.cfg["toast_backup"][a["aumid"]] = get_toast(a["aumid"])
                    set_toast(a["aumid"], False)
            self.cfg["state"] = "off"
            self.start_watch()
        else:
            self.stop_watch()
            for aumid, value in self.cfg["toast_backup"].items():
                set_toast(aumid, value)
            self.cfg["toast_backup"] = {}
            self.cfg["state"] = "on"
        save(self.cfg)
        self.refresh()

    def add(self):
        names = {x["name"] for x in self.cfg["apps"]}
        candidates = [a for a in running_apps() if a["name"] not in names]
        win = tk.Toplevel(self)
        win.title("추가할 앱 선택 (여러 개 가능)")
        box = tk.Listbox(win, selectmode="multiple", width=40, height=20)
        box.pack(fill="both", expand=True, padx=8, pady=8)
        for a in candidates:
            box.insert("end", a["name"])

        def ok():
            self.cfg["apps"] += [candidates[i] for i in box.curselection()]
            save(self.cfg)
            self.restart_if_off()
            self.refresh()
            win.destroy()

        tk.Button(win, text="추가", command=ok).pack(pady=8)

    def remove(self):
        for i in reversed(self.listbox.curselection()):
            del self.cfg["apps"][i]
        save(self.cfg)
        self.restart_if_off()
        self.refresh()

    def restart_if_off(self):
        if self.watcher:
            self.stop_watch()
            self.start_watch()

    def on_close(self):
        # OFF 상태로 닫으면 감시가 멈추므로, 다시 켤 때까지 팝업이 다시 보인다는 점을 화면에 알려 준다.
        if self.cfg["state"] == "off":
            if not tk.messagebox.askyesno("알림 OFF 중", "닫으면 팝업 차단이 멈춥니다. 그래도 닫을까요?"):
                return
            self.stop_watch()
        self.destroy()


if __name__ == "__main__":
    import tkinter.messagebox  # noqa: F401  (on_close에서 사용)
    App().mainloop()
