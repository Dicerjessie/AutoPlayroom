import ctypes

u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32

def switch_to_default_desktop():
    hdesk = u32.OpenDesktopW('default', 0, False, 0x1FF)
    if not hdesk:
        raise RuntimeError(f"OpenDesktopW failed: {k32.GetLastError()}")
    if not u32.SetThreadDesktop(hdesk):
        raise RuntimeError(f"SetThreadDesktop failed: {k32.GetLastError()}")
    return hdesk

switch_to_default_desktop()
