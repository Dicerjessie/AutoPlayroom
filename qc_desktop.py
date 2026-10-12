import ctypes

u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32

def switch_to_default_desktop():
    hdesk = u32.OpenDesktopW('default', 0, False, 0x1FF)
    if not hdesk:
        return None
    if not u32.SetThreadDesktop(hdesk):
        # Error 170 (ERROR_BUSY) occurs if the thread already has windows/console hooks (e.g. PyInstaller bootloader)
        pass
    return hdesk

switch_to_default_desktop()
