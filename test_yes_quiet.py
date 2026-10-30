import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("nt", Path(__file__).with_name("yes-quiet.pyw"))
nt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nt)

# 이 PC의 Get-StartApps 결과에서 가져온 값
START = [{"Name": "Telegram", "AppID": "Telegram.TelegramDesktop"},
         {"Name": "WhatsApp", "AppID": "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"},
         {"Name": "Microsoft Teams", "AppID": "MSTeams_8wekyb3d8bbwe!MSTeams"}]


def test_find_aumid():
    assert nt.find_aumid("WhatsApp", START) == "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"
    assert nt.find_aumid("Microsoft Teams", START) == "MSTeams_8wekyb3d8bbwe!MSTeams"
    assert nt.find_aumid("LINE", START) is None  # 설치 안 된 앱


def test_ps_korean():
    assert nt.ps("Write-Output '카카오톡'").strip() == "카카오톡"  # 깨지면 시작 메뉴 목록을 못 읽는다


def test_messengers():
    assert len({m["process"].lower() for m in nt.MESSENGERS}) == len(nt.MESSENGERS) == 7
    assert [m["name"] for m in nt.MESSENGERS if not m["toast"]] == ["카카오톡"]


def test_is_popup():
    # 실제 기록(yes-quiet.log)에서 나온 창 속성들
    def win(exe, cls, tool, topmost, foreground=False):
        return {"exe": exe, "class": cls, "tool": tool, "topmost": topmost, "foreground": foreground}
    k = "kakaotalk.exe"
    assert nt.is_popup(win(k, "EVA_Window_Dblclk", True, True))  # 미리보기 본문
    assert nt.is_popup(win(k, "KakaoTalkShadowWndClass", True, True))  # 미리보기 그림자
    assert not nt.is_popup(win(k, "EVA_Window_Dblclk", False, False, True))  # 채팅창
    assert not nt.is_popup(win(k, "EVA_Window_Dblclk", False, True))  # 항상 위로 고정한 채팅창
    assert not nt.is_popup(win(k, "EVA_Menu", True, True))  # 메뉴(예전 규칙은 숨겼음)
    assert not nt.is_popup(win(k, "EVA_Window", True, True))  # 클릭이 통과하는 89x33 창(예전 규칙은 숨겼음)
    assert nt.is_popup(win("telegram.exe", "Qt51519QWindowToolSaveBits", True, True))
    assert not nt.is_popup(win("telegram.exe", "tooltips_class32", True, False))


def test_face():
    on, off = nt.face(False), nt.face(True)
    assert on.getpixel((0, 0))[3] == 0  # 모서리는 투명
    alphas = {a for _, (r, g, b, a) in on.getcolors(nt.SIZE ** 2)}
    assert alphas - {0, 255}  # 가장자리 반투명이 살아 있어야 계단처럼 안 깨진다
    assert off.getpixel((0, 0))[3] == 0
    assert on.tobytes() != off.tobytes()  # ON은 낮(icon-day.png), OFF는 밤(icon-night.png)


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
    test_find_aumid()
    test_ps_korean()
    test_messengers()
    test_is_popup()
    test_face()
    test_suppress_roundtrip()
    test_toast_roundtrip()
    print("ok")
