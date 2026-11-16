"""
크롬 창에 실제 키보드 입력 보내기 (Windows 전용, 추가 설치 없음)

사람이 직접 누르는 것과 똑같이  F12 → Ctrl+Shift+M → F5  를 크롬 창에 보냄.
 - F12          : 개발자 도구 열기
 - Ctrl+Shift+M : Toggle device toolbar (모바일 기기 모드)
 - F5           : 새로고침
"""
import ctypes
import os
import time

IS_WINDOWS = os.name == "nt"

VK = {"CTRL": 0x11, "SHIFT": 0x10, "ALT": 0x12, "M": 0x4D, "F5": 0x74, "F12": 0x7B}
KEYEVENTF_KEYUP = 0x0002
SW_RESTORE = 9


def _user32():
    return ctypes.windll.user32


def find_window(title_part):
    """제목에 title_part가 들어간 보이는 창 찾기"""
    user32 = _user32()
    found = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if title_part in buf.value:
                    found.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return found[0] if found else None


def focus_window(hwnd):
    """창을 맨 앞으로 가져와 키보드 입력을 받게 함. 성공 시 True"""
    user32 = _user32()
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    # Windows는 다른 프로그램이 창 포커스를 가져가는 것을 막으므로 Alt 키를 한 번 눌러 허용받음
    user32.keybd_event(VK["ALT"], 0, 0, 0)
    user32.keybd_event(VK["ALT"], 0, KEYEVENTF_KEYUP, 0)
    user32.SetForegroundWindow(hwnd)
    user32.BringWindowToTop(hwnd)
    time.sleep(0.3)
    return user32.GetForegroundWindow() == hwnd


def is_foreground(hwnd):
    return _user32().GetForegroundWindow() == hwnd


def press(*keys):
    """예: press('CTRL', 'SHIFT', 'M')  → 동시에 누르고 역순으로 뗌"""
    user32 = _user32()
    for k in keys:
        user32.keybd_event(VK[k], 0, 0, 0)
        time.sleep(0.03)
    for k in reversed(keys):
        user32.keybd_event(VK[k], 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.03)


def get_clipboard_text():
    """Windows 클립보드의 글자 읽기 (실패하면 None) - 어떤 오류가 나도 수집을 멈추지 않음"""
    if not IS_WINDOWS or not hasattr(ctypes, "windll"):
        return None
    try:
        return _read_clipboard()
    except Exception:
        return None


def _read_clipboard():
    CF_UNICODETEXT = 13
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    user32.GetClipboardData.restype = ctypes.c_void_p
    for _ in range(5):  # 다른 프로그램이 클립보드를 쓰는 중이면 잠깐 기다렸다 재시도
        if user32.OpenClipboard(None):
            break
        time.sleep(0.1)
    else:
        return None
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()
