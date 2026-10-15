"""
PlayroomBot - Unified Autonomous Player for Quentin C's Gameroom
Features:
- Dynamic room & table detection (Main Room vs Unsupported Game vs Supported Game)
- Automatic game table switching on the fly (Uno <-> Farkle <-> 1000 Miles)
- Standalone execution & Explorer-friendly launcher
"""

import sys
import time
import argparse
import re
import qc_desktop
import win32gui
import win32con
import ctypes
import comtypes
import comtypes.client

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

oleacc_mod = comtypes.client.GetModule("oleacc.dll")
IAccessible = oleacc_mod.IAccessible
oleacc = ctypes.windll.oleacc

SUPPORTED_GAMES = {
    "farkle": ["farkle", "10000", "10,000", "10 000", "dix mille"],
    "uno": ["uno", "uno no mercy", "uno flip"],
    "miles": ["1000 miles", "mille bornes", "1000 bornes", "mille-bornes"],
}

UNSUPPORTED_GAMES = {
    "Poker Texas Holdem": ["poker", "texas holdem", "texas hold'em"],
    "Belote / Coinche": ["belote", "coinche"],
    "French Tarot": ["french tarot", "tarot"],
    "Yahtzee": ["yahtzee", "yams"],
    "Scrabble": ["scrabble"],
    "Rummy": ["rummy", "rami"],
    "Battleship": ["battleship", "bataille navale"],
    "Connect Four": ["connect four", "connect 4", "puissance 4"],
    "Chess": ["chess", "échecs", "echecs"],
    "Checkers": ["checkers", "dames"],
    "Reversi": ["reversi", "othello"],
    "Backgammon": ["backgammon"],
    "Cribbage": ["cribbage"],
    "6 Takes": ["6 takes", "6 qui prend", "six takes"],
    "Spades": ["spades", "pique"],
    "Hearts": ["hearts", "coeurs"],
    "Dominos": ["dominos", "dominoes", "domino"],
    "Monopoly": ["monopoly"],
    "Bouillabaisse": ["bouillabaisse"],
    "Black Jack": ["black jack", "blackjack"],
    "Citadels": ["citadels", "citadelles"],
    "Duck Racing": ["duck racing", "course de canards"],
    "Exploding Kittens": ["exploding kittens"],
    "Solitaire": ["solitaire"],
    "Zanzibar": ["zanzibar"],
}

def get_qc_window():
    hwnd_main = None
    def find_top(h, _):
        nonlocal hwnd_main
        if "QuentinC Gameroom" in win32gui.GetWindowText(h):
            hwnd_main = h
    win32gui.EnumWindows(find_top, None)
    return hwnd_main

def get_qc_controls(hwnd_main):
    hwnd_menu = None
    hwnd_edit = None
    def find_c(h, _):
        nonlocal hwnd_edit, hwnd_menu
        if 'RICHEDIT' in win32gui.GetClassName(h):
            hwnd_edit = h
        if win32gui.GetWindowText(h) == 'control':
            hwnd_menu = h
    win32gui.EnumChildWindows(hwnd_main, find_c, None)

    p_acc = None
    if hwnd_menu:
        p = ctypes.POINTER(IAccessible)()
        hr = oleacc.AccessibleObjectFromWindow(
            hwnd_menu,
            0xFFFFFFFC,  # OBJID_CLIENT
            ctypes.byref(IAccessible._iid_),
            ctypes.byref(p)
        )
        if hr == 0 and p:
            p_acc = p

    return hwnd_edit, hwnd_menu, p_acc

def get_menu_names(p_acc):
    if not p_acc:
        return []
    names = []
    try:
        count = p_acc.accChildCount
        for i in range(1, count + 1):
            nm = p_acc.accName(i) or ""
            if nm.strip():
                names.append(nm.strip())
    except Exception:
        return []
    return names

def read_history_lines(hwnd_edit):
    if not hwnd_edit:
        return []
    try:
        length = win32gui.SendMessage(hwnd_edit, win32con.WM_GETTEXTLENGTH, 0, 0)
        buf = ctypes.create_unicode_buffer(length + 2)
        win32gui.SendMessage(hwnd_edit, win32con.WM_GETTEXT, length + 1, ctypes.addressof(buf))
        text = buf.value
        return [l.strip() for l in text.splitlines() if l.strip()]
    except Exception:
        return []

def identify_game_from_string(text):
    t = text.lower().strip()
    for key, aliases in SUPPORTED_GAMES.items():
        if any(re.search(rf"\b{re.escape(a)}\b", t) for a in aliases):
            display = "Uno" if key == "uno" else ("Farkle" if key == "farkle" else "1000 Miles")
            return key, True, display
    for name, aliases in UNSUPPORTED_GAMES.items():
        if any(re.search(rf"\b{re.escape(a)}\b", t) for a in aliases):
            return name.lower(), False, name
    return None

def detect_room_and_game(menu_names, history_lines):
    menu_lower = [m.lower().strip() for m in menu_names]

    # 1. Check Menu items for definitive Main Room markers
    is_main_menu = any(k in menu_lower for k in [
        "create a new table", "créer une nouvelle table",
        "join a table", "rejoindre une table",
        "server options", "options du serveur"
    ]) or any(re.search(r"^table \d+ \(", m) for m in menu_lower)

    if is_main_menu:
        return {"location": "main_room", "game": None, "status": "main_room", "display": "Main Room (Reception)"}

    # 2. Check Menu items for active game hands / dice
    # 1000 Miles hand
    miles_card_indicators = [
        "25 miles", "50 miles", "75 miles", "100 miles", "200 miles",
        "green light", "feu vert", "red light", "feu rouge", "speed limit",
        "gasoline", "spare tire", "reparation", "right of way", "driving ace",
        "puncture-proof", "extra tank"
    ]
    if any(any(c in m for c in miles_card_indicators) for m in menu_lower):
        return {"location": "table", "game": "miles", "status": "supported", "display": "1000 Miles"}

    # Uno hand
    def is_uno_item(m):
        if any(w in m for w in ["wild", "color roulette", "skip everyone", "take a card", "prendre une carte"]):
            return True
        return bool(re.search(r"\b(?:red|blue|green|yellow|orange|pink|purple|teal|rouge|bleu|vert|jaune)\s+(?:\d+|skip|reverse|draw|discard|\+2|\+4|\+6|\+10|passer)\b", m))

    if any(is_uno_item(m) for m in menu_lower):
        return {"location": "table", "game": "uno", "status": "supported", "display": "Uno"}

    # Farkle menu
    farkle_menu_indicators = ["roll the dice", "lancer les dés", "bank", "garder les points", "single 1", "single 5", "three 1s"]
    if any(any(c in m for c in farkle_menu_indicators) for m in menu_lower):
        return {"location": "table", "game": "farkle", "status": "supported", "display": "Farkle"}

    # 3. Scan History from newest to oldest for table transitions
    for line in reversed(history_lines):
        line_clean = line.strip()
        if not line_clean:
            continue
        line_lower = line_clean.lower()

        # Check for leave table / return to main room
        if any(k in line_lower for k in [
            "you leave the table", "vous quittez la table",
            "you are back in the main room", "you are back in the reception",
            "vous êtes de retour dans la salle principale", "vous etes de retour dans la salle principale",
            "vous êtes de retour dans la réception", "the table is closed", "la table est fermée",
            "the table has been deleted", "la table a été supprimée"
        ]):
            return {"location": "main_room", "game": None, "status": "main_room", "display": "Main Room (Reception)"}

        # Check for table join / creation announcement
        m_join = re.search(r"(?:you create a new table|you create a table of|you sit at table|you join (?:the )?table|table #?\d+\s*:|vous créez une nouvelle table|vous rejoignez la table|vous vous asseyez à la table|bienvenue à la table|bienvenue au)\s*(?:#?\d+\s*)?(?:\((.*?)\)|:\s*(.*?)(?:\.|$)|of\s+(.*?)(?:\.|$)|(?:to\s+)?(.*?)(?:\.|\!|$))", line_clean, re.I)
        if m_join:
            raw_game = (m_join.group(1) or m_join.group(2) or m_join.group(3) or m_join.group(4) or "").strip()
            res = identify_game_from_string(raw_game)
            if res:
                game_id, supported, display_name = res
                return {
                    "location": "table",
                    "game": game_id,
                    "status": "supported" if supported else "unsupported",
                    "display": display_name
                }

    # 4. In-game keyword scan (last 60 lines)
    recent_text = " ".join([l.lower() for l in history_lines[-60:]])
    if any(k in recent_text for k in ["rolls the dice", "farkle", "points to enter", "points to bank", "fly away", "hot dice"]):
        return {"location": "table", "game": "farkle", "status": "supported", "display": "Farkle"}
    if any(k in recent_text for k in ["1000 miles", "mille bornes", "speed limit", "dirty trick", "coup fourré", "flat tire", "out of gas", "right of way"]):
        return {"location": "table", "game": "miles", "status": "supported", "display": "1000 Miles"}
    if any(k in recent_text for k in ["shuffles the cards", "draws a card", "uno!", "interception", "said uno", "forgot to say uno"]):
        return {"location": "table", "game": "uno", "status": "supported", "display": "Uno"}

    # Unsupported games keywords
    if any(k in recent_text for k in ["blinds", "flop", "all-in", "the pot contains"]):
        return {"location": "table", "game": "poker", "status": "unsupported", "display": "Poker Texas Holdem"}
    if any(k in recent_text for k in ["belote", "rebelote", "coinche", "atout"]):
        return {"location": "table", "game": "belote", "status": "unsupported", "display": "Belote / Coinche"}
    if any(k in recent_text for k in ["yahtzee", "yams"]):
        return {"location": "table", "game": "yahtzee", "status": "unsupported", "display": "Yahtzee"}
    if any(k in recent_text for k in ["scrabble", "exchanges letters"]):
        return {"location": "table", "game": "scrabble", "status": "unsupported", "display": "Scrabble"}
    if any(k in recent_text for k in ["petit au bout", "garde contre", "garde sans"]):
        return {"location": "table", "game": "tarot", "status": "unsupported", "display": "French Tarot"}

    # 5. Generic table check
    is_at_table = any(k in menu_lower for k in [
        "leave the table", "quitter la table",
        "start the game", "lancer la partie",
        "table options", "options de la table"
    ])
    if is_at_table:
        return {"location": "table", "game": "unknown", "status": "unsupported", "display": "Unknown Game Table"}

    # Default fallback: main room
    return {"location": "main_room", "game": None, "status": "main_room", "display": "Main Room (Reception)"}

class PlayroomSupervisor:
    def __init__(self, args):
        self.args = args
        self.running = True
        self.hwnd_main = None
        self.hwnd_edit = None
        self.hwnd_menu = None
        self.p_acc = None
        
        self.current_state = None
        self.active_bot = None
        self.last_status_announce_time = 0.0

    def refresh_controls(self, hwnd_main):
        self.hwnd_main = hwnd_main
        self.hwnd_edit, self.hwnd_menu, self.p_acc = get_qc_controls(hwnd_main)

    def transition_state(self, new_state):
        # Stop existing engine cleanly if state changes
        if self.active_bot:
            old_name = getattr(self.active_bot, "game_name", "Game")
            print(f"\n⏹️ [Supervisor] Leaving {old_name}...")
            self.active_bot.running = False
            self.active_bot = None

        loc = new_state["location"]
        game = new_state["game"]
        status = new_state["status"]
        display = new_state["display"]

        if loc == "main_room":
            print(f"\n🏛️ [Location] Player is in the Main Room (Reception).")
            print(f"   AutoPlayroom is standing by. Join a Farkle, Uno, or 1000 Miles table to begin!\n")
        elif status == "unsupported":
            print(f"\nℹ [Location] Player is at an unsupported table: {display.upper()}.")
            print(f"   AutoPlayroom will idle safely until you return to a supported game or the main room.\n")
        elif status == "supported":
            # Check if user locked the bot with --game
            is_locked = (self.args.game != "auto")
            game_matches_lock = (self.args.game == game or (self.args.game in ["miles", "1000miles"] and game == "miles"))

            if is_locked and not game_matches_lock:
                print(f"\n⚠️ [Warning] Detected {display.upper()} table, but bot is locked to --game {self.args.game}.")
                print(f"   (Run with --game auto for automatic multi-game table switching)\n")
                return

            print(f"\n🎮 [Table Detected] Entering {display.upper()} table!")
            print(f"   🚀 Launching autonomous {display} engine...")

            if game == "farkle":
                from autofarkle import FarkleBot
                bot = FarkleBot(
                    delay=self.args.delay,
                    aggressive=self.args.aggressive
                )
                bot.game_name = "AutoFarkle"
                if bot.find_client():
                    lines = bot.read_new_history()
                    bot.parse_history_events(lines)
                self.active_bot = bot
            elif game == "uno":
                from autouno import UnoBot
                bot = UnoBot(
                    delay=self.args.delay,
                    enable_interceptions=not self.args.no_intercept,
                    super_interceptions=not self.args.no_super,
                    anti_bluff=not self.args.allow_bluff,
                    straights=self.args.straights
                )
                bot.game_name = "AutoUno"
                if bot.find_client():
                    lines = bot.read_new_history()
                    bot.parse_history_events(lines)
                self.active_bot = bot
            elif game == "miles":
                from auto1000miles import MilesBot
                bot = MilesBot(delay=self.args.delay)
                bot.game_name = "Auto1000Miles"
                if bot.find_client():
                    lines = bot.read_new_history()
                    bot.parse_history_events(lines)
                self.active_bot = bot

    def run(self):
        print("=" * 60)
        print("   AutoPlayroom - Autonomous Multigame Bot Supervisor")
        print("   Supports Farkle, Uno & 1000 Miles for Quentin C's Gameroom")
        print("=" * 60)

        # Main supervisor loop
        while self.running:
            hwnd_main = get_qc_window()
            if not hwnd_main:
                if self.current_state != "WAITING_FOR_QC":
                    print("⏳ Waiting for Quentin C's Gameroom (qcgc.exe) to open... (Press Ctrl+C to cancel)")
                    self.current_state = "WAITING_FOR_QC"
                    if self.active_bot:
                        self.active_bot.running = False
                        self.active_bot = None
                time.sleep(1.0)
                continue

            self.refresh_controls(hwnd_main)
            menu_names = get_menu_names(self.p_acc)
            history_lines = read_history_lines(self.hwnd_edit)

            detected = detect_room_and_game(menu_names, history_lines)
            now = time.time()

            # State transition detection
            state_key = (detected["location"], detected["game"])
            prev_key = (self.current_state["location"], self.current_state["game"]) if isinstance(self.current_state, dict) else None

            if state_key != prev_key:
                self.transition_state(detected)
                self.current_state = detected
                self.last_status_announce_time = now
            elif detected["status"] in ["main_room", "unsupported"] and (now - self.last_status_announce_time > 25.0):
                if detected["status"] == "main_room":
                    print("🏛️ [AutoPlayroom] Standing by in Main Room (Reception)...")
                else:
                    print(f"ℹ [AutoPlayroom] Idling at unsupported table: {detected['display']}...")
                self.last_status_announce_time = now

            # If an active bot is attached, run its tick
            if self.active_bot:
                try:
                    self.active_bot.tick()
                except Exception as e:
                    print(f"⚠️ [Engine Error] {e}")

            time.sleep(0.02)

def should_pause_on_exit():
    if not getattr(sys, 'frozen', False):
        return False
    try:
        arr = (ctypes.c_uint * 2)()
        count = ctypes.windll.kernel32.GetConsoleProcessList(arr, 2)
        return count <= 1
    except Exception:
        return False

def main():
    parser = argparse.ArgumentParser(description="AutoPlayroom - Autonomous Multigame Bot (Farkle, Uno, 1000 Miles)")
    parser.add_argument("--game", choices=["farkle", "uno", "miles", "1000miles", "auto"], default="auto", help="Game to play (default: auto - dynamic table switching)")
    parser.add_argument("--delay", type=float, default=0.2, help="Move delay in seconds (default: 0.2)")
    parser.add_argument("--turns", type=int, default=None, help="Max turns (Farkle only)")
    parser.add_argument("--aggressive", action="store_true", help="Play aggressively (Farkle)")
    parser.add_argument("--no-intercept", action="store_true", help="Disable automatic interceptions (Uno)")
    parser.add_argument("--no-super", action="store_true", help="Disable super interceptions (Uno)")
    parser.add_argument("--allow-bluff", action="store_true", help="Allow playing Wild Draw Four as a bluff even when holding matching colors (Uno)")
    parser.add_argument("--straights", action="store_true", help="Enable straights chaining (Uno)")
    args = parser.parse_args()

    supervisor = PlayroomSupervisor(args)
    supervisor.run()

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
