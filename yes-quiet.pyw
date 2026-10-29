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
import struct
import subprocess
import sys
import threading
import time
import tkinter as tk
import uuid
import winreg
from ctypes import wintypes
from pathlib import Path

import pystray
from PIL import Image, ImageOps

__version__ = "1.0.0"

# exe로 묶으면 아이콘은 임시 압축 해제 폴더(_MEIPASS)에, 설정·기록은 사용자별 %APPDATA%\yes-quiet에 둔다.
RES = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
DATA = Path(os.environ["APPDATA"]) / "yes-quiet"
DATA.mkdir(exist_ok=True)
CONFIG = DATA / "config.json"
NO_WINDOW = 0x08000000
TOAST_KEY = r"Software\Microsoft\Windows\CurrentVersion\Notifications\Settings"
SIZE = 72  # 화면에 뜨는 고양이 크기(px)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
gdi32 = ctypes.windll.gdi32

# paint_layered에서 쓰는 함수들. 핸들이 64비트라 기본값(int)으로 주고받으면 잘린다.
H = ctypes.c_void_p
user32.GetDC.restype = H
user32.GetDC.argtypes = [H]
user32.ReleaseDC.argtypes = [H, H]
user32.UpdateLayeredWindow.argtypes = [H, H, ctypes.POINTER(wintypes.POINT), ctypes.POINTER(wintypes.SIZE), H,
                                       ctypes.POINTER(wintypes.POINT), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                       wintypes.DWORD]
gdi32.CreateCompatibleDC.restype = H
gdi32.CreateCompatibleDC.argtypes = [H]
gdi32.CreateBitmap.restype = H
gdi32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.UINT, ctypes.c_char_p]
gdi32.SelectObject.restype = H
gdi32.SelectObject.argtypes = [H, H]
gdi32.DeleteObject.argtypes = [H]
gdi32.DeleteDC.argtypes = [H]
user32.SetWinEventHook.restype = H  # Watcher
user32.UnhookWinEvent.argtypes = [H]


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


def visible_windows(targets, cache, include_hidden=False):
    found = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        if include_hidden or user32.IsWindowVisible(hwnd):
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


def suppress(hwnd):
    """팝업을 투명도 0으로 만들고 숨긴다.
    카카오톡은 팝업이 미끄러져 올라오고 내려가는 동안 창을 계속 다시 띄우므로, 숨기기만 하면 그때마다 보인다."""
    ex = user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
    if not ex & 0x80000:  # 이미 반투명 창(그림자 등)은 앱이 직접 그리므로 건드리면 깨진다. 숨기기만 한다
        user32.SetWindowLongW(hwnd, -20, ex | 0x80000)  # WS_EX_LAYERED
        user32.SetLayeredWindowAttributes(hwnd, 0, 0, 2)  # LWA_ALPHA, 투명도 0
    user32.ShowWindow(hwnd, 0)  # SW_HIDE


def unsuppress(targets):
    """suppress로 투명하게 만든 창을 되돌린다. 카카오톡은 숨긴 팝업 창을 다음 알림 때 다시 쓰므로 안 돌리면 ON에서도 안 보인다."""
    alpha, flags = ctypes.c_ubyte(), wintypes.DWORD()
    for hwnd in visible_windows(targets, {}, include_hidden=True):
        if (user32.GetLayeredWindowAttributes(hwnd, None, ctypes.byref(alpha), ctypes.byref(flags))
                and flags.value & 2 and alpha.value == 0):
            user32.SetWindowLongW(hwnd, -20, user32.GetWindowLongW(hwnd, -20) & ~0x80000)


WINEVENTPROC = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG,
                                  wintypes.LONG, wintypes.DWORD, wintypes.DWORD)


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

        def check(hwnd):
            if hwnd in keep:
                return
            info = window_info(hwnd)
            popup = is_popup(info)
            if popup:
                suppress(hwnd)
            else:
                keep.add(hwnd)  # 채팅창 등은 한 번 판정하면 다시 보지 않는다
            with LOG.open("a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} {'숨김' if popup else '유지'} {info}\n")

        def on_show(hook, event, hwnd, obj, child, thread, ms):
            # 창이 뜨는 순간 Windows가 알려 준다(0.1초 폴링을 기다리면 그사이 팝업이 보인다). 최상위 창만 본다.
            if not hwnd or obj != 0 or user32.GetAncestor(hwnd, 2) != hwnd:  # OBJID_WINDOW, GA_ROOT
                return
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if exe_of(pid.value, cache) in self.targets:
                check(hwnd)

        callback = WINEVENTPROC(on_show)  # 훅이 살아 있는 동안 참조를 붙잡아 둔다
        hook = user32.SetWinEventHook(0x8002, 0x8002, None, callback, 0, 0, 0)  # EVENT_OBJECT_SHOW, 모든 프로세스, OUTOFCONTEXT
        msg = wintypes.MSG()
        last_mute = 0
        while not self.stop_event.is_set():
            # 훅 알림은 이 스레드가 메시지를 꺼낼 때 불린다. 알림이 없어도 0.1초마다 깨어 나머지 일을 한다.
            user32.MsgWaitForMultipleObjects(0, None, False, 100, 0x4FF)  # QS_ALLINPUT
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):  # PM_REMOVE
                user32.DispatchMessageW(ctypes.byref(msg))
            for hwnd in visible_windows(self.targets, cache):  # 알림을 놓친 창 대비
                check(hwnd)
            if time.monotonic() - last_mute >= 1:  # 소리 세션은 앱이 처음 소리를 낼 때 생기므로 1초마다 다시 음소거
                last_mute = time.monotonic()
                mute(self.targets, True)
                cache.clear()  # 새로 뜬 프로세스의 pid 재사용 대비
        user32.UnhookWinEvent(hook)
        unsuppress(self.targets)
        mute(self.targets, False)
        comtypes.CoUninitialize()


# ---------- 화면 ----------

def taskbar_identity(hwnd):
    """작업 표시줄에 고정할 때 pythonw.exe 대신 yes-quiet(고양이 아이콘·다시 실행 명령)로 고정되게 창 속성을 붙인다."""
    frozen = getattr(sys, "frozen", False)
    values = {  # PKEY_AppUserModel_*(propkey.h): 5=ID, 2=RelaunchCommand, 3=RelaunchIconResource, 4=RelaunchDisplayNameResource
        5: "yes-quiet",
        2: f'"{sys.executable}"' if frozen else f'"{sys.executable}" "{Path(__file__).resolve()}"',
        3: f"{sys.executable},0" if frozen else str(RES / "icon.ico"),
        4: "yes-quiet",
    }
    fmtid = uuid.UUID("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3").bytes_le
    iid = ctypes.create_string_buffer(uuid.UUID("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99").bytes_le)  # IPropertyStore
    ctypes.windll.ole32.CoInitialize(None)
    store = ctypes.c_void_p()
    if ctypes.windll.shell32.SHGetPropertyStoreForWindow(wintypes.HWND(hwnd), iid, ctypes.byref(store)) != 0:
        return  # 실패해도 아이콘만 파이썬으로 보일 뿐이라 앱은 그대로 띄운다
    vtbl = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    set_value = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p)(vtbl[6])
    for pid, text in values.items():
        s = ctypes.create_unicode_buffer(text)
        set_value(store, fmtid + struct.pack("<I", pid), struct.pack("<H6xQ8x", 31, ctypes.addressof(s)))  # PROPVARIANT VT_LPWSTR
    ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p)(vtbl[7])(store)  # Commit
    ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])(store)  # Release


def face(off):
    """화면에 띄울 고양이 그림(ON은 컬러, OFF는 회색). 가장자리 반투명까지 살린 RGBA."""
    img = Image.open(RES / "icon.png").convert("RGBA").resize((SIZE, SIZE), Image.LANCZOS)
    if off:
        img = Image.merge("RGBA", (*[ImageOps.grayscale(img)] * 3, img.getchannel("A")))
    return img


def paint_layered(hwnd, img):
    """창 전체를 RGBA 그림으로 칠한다(UpdateLayeredWindow).
    색 하나를 투명하게 빼는 방식(-transparentcolor)은 가장자리가 계단처럼 깨져서, 픽셀마다 투명도를 준다."""
    r, g, b, a = img.convert("RGBa").split()  # Windows는 알파를 미리 곱한(premultiplied) BGRA를 받는다
    bits = Image.merge("RGBA", (b, g, r, a)).tobytes()
    screen = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateBitmap(img.width, img.height, 1, 32, bits)
    old = gdi32.SelectObject(mem, bmp)
    blend = wintypes.DWORD(0x01FF0000)  # BLENDFUNCTION: AC_SRC_OVER, 불투명도 255, AC_SRC_ALPHA
    user32.UpdateLayeredWindow(hwnd, screen, None, wintypes.SIZE(img.width, img.height), mem, wintypes.POINT(),
                               0, blend, 2)  # ULW_ALPHA
    gdi32.SelectObject(mem, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(None, screen)


def work_area():
    """작업 표시줄을 뺀 화면의 오른쪽·아래 끝 좌표."""
    r = wintypes.RECT()
    user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
    return r.right, r.bottom


def on_screen(x, y):
    """고양이 가운데가 어느 모니터 위에 있는지. 옮겨 둔 모니터를 뺐을 때 안 보이는 곳에 뜨는 것을 막는다."""
    user32.MonitorFromPoint.restype = wintypes.HMONITOR
    return bool(user32.MonitorFromPoint(wintypes.POINT(x + SIZE // 2, y + SIZE // 2), 0))  # MONITOR_DEFAULTTONULL


class App(tk.Tk):
    """화면에는 고양이 얼굴만 띄우고 누르면 알림 ON/OFF. 대상 앱 목록은 알림 영역 고양이를 우클릭해서 연다."""

    def __init__(self):
        super().__init__()
        self.title(f"yes-quiet {__version__}")
        self.icons = [tk.PhotoImage(file=RES / n) for n in ("icon.png", "icon-32.png")]  # make_icon.py로 생성
        self.iconphoto(True, *self.icons)
        self.overrideredirect(True)  # 제목 표시줄·테두리 없이 고양이만
        self.attributes("-topmost", True)
        self.cfg = load()
        self.cfg.setdefault("toast_backup", {})
        right, bottom = work_area()
        x, y = self.cfg.get("pos") or (right - SIZE - 16, bottom - SIZE - 16)  # 기본은 오른쪽 아래
        if not on_screen(x, y):
            x, y = right - SIZE - 16, bottom - SIZE - 16
        self.geometry(f"+{x}+{y}")
        self.faces = {"on": face(False), "off": face(True)}
        # 보이는 그림은 paint_layered가 그리고, 이 칸은 마우스 입력만 받는다.
        self.cat = tk.Frame(self, width=SIZE, height=SIZE, cursor="hand2")
        self.cat.pack()
        self.update_idletasks()
        self.hwnd = int(self.wm_frame(), 16)
        user32.SetWindowLongW(self.hwnd, -20, user32.GetWindowLongW(self.hwnd, -20) | 0x80000)  # GWL_EXSTYLE |= WS_EX_LAYERED
        self.cat.bind("<ButtonPress-1>", self.press)
        self.cat.bind("<B1-Motion>", self.drag)
        self.cat.bind("<ButtonRelease-1>", self.release)
        self.protocol("WM_DELETE_WINDOW", self.on_close)  # Alt+F4로 닫을 때도 같은 확인을 거친다
        self.watcher = None
        self.listwin = None

        # 알림 영역 아이콘은 별도 스레드에서 돌므로 메뉴 동작은 after로 tk 스레드에 넘긴다.
        self.tray = pystray.Icon("yes-quiet", Image.open(RES / "icon.png"), "yes-quiet", pystray.Menu(
            pystray.MenuItem("대상 앱", lambda: self.after(0, self.show_list)),
            pystray.MenuItem("종료", lambda: self.after(0, self.on_close))))
        self.tray.run_detached()

        if self.cfg["state"] == "off":  # 꺼진 상태로 종료됐다면 감시를 이어서 한다
            self.start_watch()
        else:  # OFF 중에 강제 종료됐다면 투명하게 만든 팝업 창이 남아 있으니 되돌린다
            unsuppress(self.targets())
        self.refresh()

    # 누른 채 움직이면 옮기기, 그냥 눌렀다 떼면 ON/OFF.
    def press(self, e):
        self.drag_from = (e.x_root, e.y_root, self.winfo_x(), self.winfo_y())
        self.moved = False

    def drag(self, e):
        sx, sy, wx, wy = self.drag_from
        dx, dy = e.x_root - sx, e.y_root - sy
        if self.moved or abs(dx) + abs(dy) > 4:  # 클릭할 때 손이 살짝 떨린 건 옮기기로 보지 않는다
            self.moved = True
            self.geometry(f"+{wx + dx}+{wy + dy}")

    def release(self, e):
        if self.moved:
            self.cfg["pos"] = [self.winfo_x(), self.winfo_y()]
            save(self.cfg)
        else:
            self.toggle()

    def show_list(self):
        if not self.listwin:
            win = self.listwin = tk.Toplevel(self)
            win.title(f"yes-quiet {__version__} - 대상 앱")
            win.geometry("320x360")
            win.update_idletasks()  # 창이 실제로 만들어진 뒤라야 바깥 창 핸들(wm_frame)이 바뀌지 않는다
            taskbar_identity(int(win.wm_frame(), 16))
            self.listbox = tk.Listbox(win, height=10)
            self.listbox.pack(fill="both", expand=True, padx=16, pady=(16, 0))
            row = tk.Frame(win)
            row.pack(fill="x", padx=16, pady=12)
            tk.Button(row, text="실행 중인 앱에서 추가", command=self.add).pack(side="left")
            tk.Button(row, text="삭제", command=self.remove).pack(side="right")
            win.protocol("WM_DELETE_WINDOW", self.close_list)
            self.refresh()
        # 다른 앱 창이 앞에 있으면 Windows가 앞으로 가져오기를 막으므로, 항상 위로 잠깐 올렸다 내린다.
        self.listwin.deiconify()
        self.listwin.attributes("-topmost", True)
        self.listwin.attributes("-topmost", False)
        self.listwin.focus_force()

    def close_list(self):
        self.listwin.destroy()
        self.listwin = None

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
        paint_layered(self.hwnd, self.faces[self.cfg["state"]])
        if not self.listwin:
            return
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
        self.tray.stop()
        self.destroy()


if __name__ == "__main__":
    import tkinter.messagebox  # noqa: F401  (on_close에서 사용)
    # pythonw로 실행하면 작업 표시줄이 pythonw.exe 아이콘을 쓰므로, 별도 앱 ID를 줘서 창 아이콘(고양이)이 보이게 한다(추가 창도 같은 칸으로 묶임).
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("yes-quiet")
    App().mainloop()
