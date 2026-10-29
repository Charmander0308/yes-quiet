import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("nt", Path(__file__).with_name("yes-quiet.pyw"))
nt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nt)

WA = r"C:\Program Files\WindowsApps\5319275A.WhatsAppDesktop_2.2639.100.0_x64__cv1g1gvanyjgm\WhatsApp.Root.exe"
START = [{"Name": "Telegram", "AppID": "Telegram.TelegramDesktop"},
         {"Name": "WhatsApp", "AppID": "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"}]


def test_package_family():
    assert nt.package_family(WA) == "5319275A.WhatsAppDesktop_cv1g1gvanyjgm"
    assert nt.package_family(r"C:\Program Files\Kakao\KakaoTalk\KakaoTalk.exe") is None


def test_find_aumid():
    assert nt.find_aumid("WhatsApp.Root", WA, START) == "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"
    assert nt.find_aumid("Telegram", r"C:\x\Telegram.exe", START) == "Telegram.TelegramDesktop"
    assert nt.find_aumid("KakaoTalk", r"C:\Program Files\Kakao\KakaoTalk\KakaoTalk.exe", START) is None


def test_is_popup():
    # 실제 기록(yes-quiet.log)에서 나온 창 속성들
    kakao_chat = {"class": "EVA_Window_Dblclk", "tool": False, "topmost": False, "foreground": True}
    kakao_popup = {"class": "EVA_Window", "tool": True, "topmost": True, "foreground": False}
    telegram_popup = {"class": "Qt51519QWindowToolSaveBits", "tool": True, "topmost": True, "foreground": False}
    tooltip = {"class": "tooltips_class32", "tool": True, "topmost": False, "foreground": False}
    assert not nt.is_popup(kakao_chat)
    assert nt.is_popup(kakao_popup)
    assert nt.is_popup(telegram_popup)
    assert not nt.is_popup(tooltip)


def test_face():
    on, off = nt.face(False), nt.face(True)
    assert on.getpixel((0, 0))[3] == 0  # 모서리는 투명
    alphas = {a for _, (r, g, b, a) in on.getcolors(nt.SIZE ** 2)}
    assert alphas - {0, 255}  # 가장자리 반투명이 살아 있어야 계단처럼 안 깨진다
    assert any(r != g for _, (r, g, b, a) in on.getcolors(nt.SIZE ** 2) if a)  # ON은 컬러
    assert all(r == g == b for _, (r, g, b, a) in off.getcolors(nt.SIZE ** 2) if a)  # OFF는 회색


def test_suppress_roundtrip():
    import sys
    import tkinter as tk
    import ctypes
    r = tk.Tk()
    r.update()
    h = int(r.wm_frame(), 16)
    alpha, flags = ctypes.c_ubyte(), ctypes.c_ulong()

    def alpha_now():
        assert nt.user32.GetLayeredWindowAttributes(h, None, ctypes.byref(alpha), ctypes.byref(flags))
        return alpha.value

    before = nt.suppress(h)  # 반투명 창이 아니었던 창
    assert before is None and not nt.user32.IsWindowVisible(h) and alpha_now() == 0
    nt.restore(h, before)
    assert not nt.user32.GetWindowLongW(h, -20) & 0x80000  # ON으로 돌리면 원래대로

    r.attributes("-alpha", 250 / 255)  # 카카오톡 미리보기처럼 처음부터 투명도 250인 창
    before = nt.suppress(h)
    assert before[1] == 250 and alpha_now() == 0
    nt.restore(h, before)
    assert alpha_now() == 250

    nt.suppress(h)  # 강제 종료로 restore를 못 했을 때
    nt.unsuppress({Path(sys.executable).name.lower()})
    assert not nt.user32.GetWindowLongW(h, -20) & 0x80000
    r.destroy()


def test_toast_roundtrip():
    aumid = "NotifyToggle.SelfTest"
    nt.set_toast(aumid, False)
    assert nt.get_toast(aumid) == 0
    nt.set_toast(aumid, None)
    assert nt.get_toast(aumid) is None
    import winreg
    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, f"{nt.TOAST_KEY}\\{aumid}")


if __name__ == "__main__":
    test_package_family()
    test_find_aumid()
    test_is_popup()
    test_face()
    test_suppress_roundtrip()
    test_toast_roundtrip()
    print("ok")
