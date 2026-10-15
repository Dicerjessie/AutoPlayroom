"""
PlayroomBot - Unified Autonomous Player for Quentin C's Gameroom
Automatically detects whether you are at a Farkle or Uno table,
or allows selecting with --game farkle / --game uno.
"""

import sys
import time
import argparse
import re
import qc_desktop
import win32gui
import win32con
import ctypes

def get_qc_window():
    hwnd_main = None
    def find_top(h, _):
        nonlocal hwnd_main
        if "QuentinC Gameroom" in win32gui.GetWindowText(h):
            hwnd_main = h
    win32gui.EnumWindows(find_top, None)
    return hwnd_main

def detect_current_game():
    hwnd_main = get_qc_window()
    if not hwnd_main:
        return None

    hwnd_edit = None
    hwnd_menu = None
    def find_c(h, _):
        nonlocal hwnd_edit, hwnd_menu
        if 'RICHEDIT' in win32gui.GetClassName(h):
            hwnd_edit = h
        if win32gui.GetWindowText(h) == 'control':
            hwnd_menu = h
    win32gui.EnumChildWindows(hwnd_main, find_c, None)

    # Check recent history text
    if hwnd_edit:
        length = win32gui.SendMessage(hwnd_edit, win32con.WM_GETTEXTLENGTH, 0, 0)
        buf = ctypes.create_unicode_buffer(length + 2)
        win32gui.SendMessage(hwnd_edit, win32con.WM_GETTEXT, length + 1, ctypes.addressof(buf))
        text = buf.value.lower()
        
        # Check last 30 lines
        lines = [l.strip() for l in text.splitlines() if l.strip()][-30:]
        recent_text = " ".join(lines)
        
        if any(k in recent_text for k in ["farkle", "dice", "points to enter", "points to bank", "rolls the dice"]):
            return "farkle"
        if any(k in recent_text for k in ["1000 miles", "mille bornes", "bornes", "green light", "feu vert", "red light", "feu rouge", "driving ace", "spare tire", "flat tire", "out of gas", "speed limit", "right of way", "coup fourré"]):
            return "miles"
        if any(k in recent_text for k in ["uno", "shuffles the cards", "draws", "skip", "reverse", "interception"]):
            return "uno"

    return None

def main():
    print("=" * 60)
    print("   AutoPlayroom - Autonomous Farkle, Uno & 1000 Miles Bot")
    print("   for Quentin C's Gameroom")
    print("=" * 60)

    parser = argparse.ArgumentParser(description="Autonomous Playroom Bot (Farkle, Uno & 1000 Miles)")
    parser.add_argument("--game", choices=["farkle", "uno", "miles", "1000miles", "auto"], default="auto", help="Game to play (default: auto)")
    parser.add_argument("--delay", type=float, default=0.2, help="Move delay in seconds (default: 0.2)")
    parser.add_argument("--turns", type=int, default=None, help="Max turns (Farkle only)")
    parser.add_argument("--aggressive", action="store_true", help="Play aggressively (Farkle)")
    parser.add_argument("--no-intercept", action="store_true", help="Disable automatic interceptions (Uno)")
    parser.add_argument("--no-super", action="store_true", help="Disable super interceptions (Uno)")
    parser.add_argument("--allow-bluff", action="store_true", help="Allow playing Wild Draw Four as a bluff even when holding matching colors (Uno)")
    parser.add_argument("--straights", action="store_true", help="Enable straights chaining (Uno)")
    args = parser.parse_args()

    # Step 1: Wait for Gameroom client if not yet running
    if not get_qc_window():
        print("⏳ Waiting for Quentin C's Gameroom (qcgc.exe) to open... (Press Ctrl+C to cancel)")
        while not get_qc_window():
            time.sleep(1.0)
        print("✓ Connected to Quentin C's Gameroom!")

    game = args.game
    if game == "auto":
        print("🔍 Detecting game table...")
        detected = detect_current_game()
        
        # If table not yet recognized, poll briefly before falling back
        if not detected:
            print("⏳ Waiting for game table activity (Farkle / Uno / 1000 Miles)...")
            for _ in range(5):
                time.sleep(1.0)
                detected = detect_current_game()
                if detected:
                    break

        if detected:
            print(f"✓ Detected game table: {detected.upper()}")
            game = detected
        else:
            print("ℹ Table undetermined. Defaulting to Uno mode.")
            game = "uno"

    if game == "farkle":
        from autofarkle import FarkleBot
        bot = FarkleBot(delay=args.delay, aggressive=args.aggressive)
        if bot.find_client():
            lines = bot.read_new_history()
            bot.parse_history_events(lines)
        bot.run(max_turns=args.turns)
    elif game == "uno":
        from autouno import UnoBot
        bot = UnoBot(
            delay=args.delay,
            enable_interceptions=not args.no_intercept,
            super_interceptions=not args.no_super,
            anti_bluff=not args.allow_bluff,
            straights=args.straights
        )
        bot.run()
    elif game in ["miles", "1000miles"]:
        from auto1000miles import MilesBot
        bot = MilesBot(delay=args.delay)
        bot.run()

def should_pause_on_exit():
    if not getattr(sys, 'frozen', False):
        return False
    try:
        arr = (ctypes.c_uint * 2)()
        count = ctypes.windll.kernel32.GetConsoleProcessList(arr, 2)
        return count <= 1
    except Exception:
        return False

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 AutoPlayroom stopped by user.")
    except Exception as e:
        print(f"\n❌ Error encountered: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if should_pause_on_exit():
            input("\nPress Enter to exit...")

