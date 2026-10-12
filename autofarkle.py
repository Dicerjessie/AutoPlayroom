"""
AutoFarkle - Autonomous Farkle Player for Quentin C's Gameroom (Salon de QuentinC)
Interfaces cleanly with the desktop client via Win32 and IAccessible (MSAA).
"""

import qc_desktop  # Switches thread desktop to WinSta0\Default before COM
import sys
import time
import re
import argparse
import ctypes
from ctypes import wintypes
import win32gui
import win32con
import comtypes
import comtypes.client

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

oleacc_mod = comtypes.client.GetModule("oleacc.dll")
IAccessible = oleacc_mod.IAccessible
oleacc = ctypes.windll.oleacc

class FarkleBot:
    def __init__(self, entry_threshold=50, bank_threshold=30, win_target=1000, delay=0.6, aggressive=False):
        self.entry_threshold = entry_threshold
        self.bank_threshold = bank_threshold
        self.win_target = win_target
        self.delay = delay
        self.aggressive = aggressive
        
        self.username = None
        self.hwnd_main = None
        self.hwnd_menu = None
        self.hwnd_edit = None
        self.p_acc = None
        
        self.has_entered = False
        self.my_score = 0
        self.scores = {}
        self.last_history_len = 0
        self.running = True
        
        # Turn-level state
        self.combos_taken_this_roll = 0
        self.current_turn_points = 0
        self.consecutive_bank_failures = 0

    def log(self, msg):
        print(msg, flush=True)

    def find_client(self):
        self.hwnd_main = None
        def find_top(h, _):
            title = win32gui.GetWindowText(h)
            if "QuentinC Gameroom" in title:
                self.hwnd_main = h
                m = re.search(r"QuentinC Gameroom \[(.*?)\]", title)
                if m:
                    self.username = m.group(1)
        win32gui.EnumWindows(find_top, None)

        if not self.hwnd_main:
            return False

        self.hwnd_menu = None
        self.hwnd_edit = None
        def find_controls(h, _):
            if win32gui.GetWindowText(h) == 'control':
                self.hwnd_menu = h
            if 'RICHEDIT' in win32gui.GetClassName(h):
                self.hwnd_edit = h
        win32gui.EnumChildWindows(self.hwnd_main, find_controls, None)

        if not self.hwnd_menu or not self.hwnd_edit:
            return False

        # Get IAccessible for menu control
        p = ctypes.POINTER(IAccessible)()
        hr = oleacc.AccessibleObjectFromWindow(
            self.hwnd_menu,
            0xFFFFFFFC,  # OBJID_CLIENT
            ctypes.byref(IAccessible._iid_),
            ctypes.byref(p)
        )
        if hr == 0 and p:
            self.p_acc = p
            return True
        return False

    def read_new_history(self):
        if not self.hwnd_edit:
            return []
        length = win32gui.SendMessage(self.hwnd_edit, win32con.WM_GETTEXTLENGTH, 0, 0)
        buf = ctypes.create_unicode_buffer(length + 2)
        win32gui.SendMessage(self.hwnd_edit, win32con.WM_GETTEXT, length + 1, ctypes.addressof(buf))
        text = buf.value
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        
        new_lines = []
        if len(lines) > self.last_history_len:
            new_lines = lines[self.last_history_len:]
        elif len(lines) < self.last_history_len:
            # Table history reset
            new_lines = lines
        self.last_history_len = len(lines)
        return new_lines

    def parse_history_events(self, lines):
        for line in lines:
            # Detect game start / reset
            if any(k in line.lower() for k in ["a new game has started", "the victory is fixed to", "creates a new table"]):
                m_win = re.search(r"victory is fixed to (\d+) points", line, re.I)
                if m_win:
                    self.win_target = int(m_win.group(1))
                    self.log(f"[Game Reset] New game target: {self.win_target} pts")
                    self.has_entered = False
                    self.my_score = 0
                    self.scores = {}

            # Rule parsing
            m_rules = re.search(r"at least (\d+) points to enter.*?at least (\d+) points to bank", line, re.I)
            if m_rules:
                self.entry_threshold = int(m_rules.group(1))
                self.bank_threshold = int(m_rules.group(2))
                self.log(f"[Rules] Entry requirement: {self.entry_threshold} pts | Bank requirement: {self.bank_threshold} pts")

            # Entry rejected by server
            if "you must bank at least" in line.lower() and "to enter the game" in line.lower():
                self.has_entered = False
                self.consecutive_bank_failures += 1
                self.log(f"[Warning] Bank rejected: not entered yet (need {self.entry_threshold} pts)")

            # Score banking
            m_bank = re.search(r"^(.*?)\s+banks?\s+(\d+)\s+points for this turn.*?(?:total score is now|total score of)\s+(\d+)\s+points", line, re.I)
            if m_bank:
                player = m_bank.group(1).strip()
                turn_pts = int(m_bank.group(2))
                total_pts = int(m_bank.group(3))
                self.scores[player] = total_pts
                self.log(f"[Score] {player} banked {turn_pts} pts (Total: {total_pts})")
                if player.lower() in ["you", (self.username or "").lower()]:
                    self.my_score = total_pts
                    self.has_entered = True
                    self.consecutive_bank_failures = 0

            # Score summary parsing (e.g. PlayerName: 405.)
            m_sc = re.search(r"^([A-Za-z0-9_\-\s]+):\s*(\d+)\.", line)
            if m_sc:
                player = m_sc.group(1).strip()
                pts = int(m_sc.group(2))
                self.scores[player] = pts
                if player.lower() in ["you", (self.username or "").lower()]:
                    self.my_score = pts
                    if pts > 0:
                        self.has_entered = True

            # Entry announcement
            m_enter = re.search(r"^(.*?)\s+(?:has entered the game|has just entered the game)", line, re.I)
            if m_enter:
                player = m_enter.group(1).strip()
                if player.lower() in ["you", (self.username or "").lower()]:
                    self.has_entered = True
                    self.log(f"[Status] You have entered the game!")

            # Game win
            m_win_player = re.search(r"^(.*?)\s+wins the game", line, re.I)
            if m_win_player:
                winner = m_win_player.group(1).strip()
                self.log(f"\n🎉 GAME FINISHED! Winner: {winner}")

    def get_farkle_menu_items(self):
        """
        Inspects menu items and verifies they are genuine Farkle game choices.
        Ignores playroom lobby, player list, or general menu items.
        """
        if not self.p_acc:
            return []
        items = []
        try:
            count = self.p_acc.accChildCount
            for i in range(1, count + 1):
                name = self.p_acc.accName(i) or ""
                st = self.p_acc.accState(i)
                is_sel = bool(st & 2) or bool(st & 4)
                is_dis = bool(st & 1)
                items.append({
                    "index": i,
                    "name": name,
                    "selected": is_sel,
                    "disabled": is_dis,
                    "state": st
                })
        except Exception:
            return []

        # Validate that these are Farkle items
        farkle_patterns = [
            r"^Roll (?:\d+ dice|the dice)",
            r"^Bank \d+ points",
            r".* for \d+ points"
        ]
        is_farkle = any(any(re.match(pat, it["name"], re.I) for pat in farkle_patterns) for it in items)
        if not is_farkle:
            # Menu does not belong to an active Farkle turn (lobby/table menu)
            return []

        return items

    def select_and_activate(self, target_idx):
        items = self.get_farkle_menu_items()
        if not items or target_idx < 1 or target_idx > len(items):
            return False

        cur_idx = 1
        for item in items:
            if item["selected"]:
                cur_idx = item["index"]
                break

        # Move selection to target_idx
        diff = target_idx - cur_idx
        if diff > 0:
            for _ in range(diff):
                win32gui.SendMessage(self.hwnd_menu, win32con.WM_KEYDOWN, win32con.VK_DOWN, 0)
                win32gui.SendMessage(self.hwnd_menu, win32con.WM_KEYUP, win32con.VK_DOWN, 0)
                time.sleep(0.04)
        elif diff < 0:
            for _ in range(-diff):
                win32gui.SendMessage(self.hwnd_menu, win32con.WM_KEYDOWN, win32con.VK_UP, 0)
                win32gui.SendMessage(self.hwnd_menu, win32con.WM_KEYUP, win32con.VK_UP, 0)
                time.sleep(0.04)

        time.sleep(0.06)
        # Activate via VK_RETURN
        win32gui.SendMessage(self.hwnd_menu, win32con.WM_KEYDOWN, win32con.VK_RETURN, 0)
        win32gui.SendMessage(hwnd_menu := self.hwnd_menu, win32con.WM_KEYUP, win32con.VK_RETURN, 0)
        return True

    def estimate_combo_dice(self, combo_name):
        cname = combo_name.lower()
        if "six" in cname or "straight" in cname or "three pairs" in cname or "two triplets" in cname:
            return 6
        if "five" in cname:
            return 5
        if "four" in cname:
            return 4
        if "three" in cname:
            return 3
        if "pair" in cname:
            return 2
        if "single" in cname:
            return 1
        return 1

    def decide_move(self, items):
        """
        Enhanced Statistical Farkle Decision Engine:
        - Endgame awareness (trailing vs leading)
        - Hot dice detection
        - Risk-adjusted banking
        - Dice count preservation
        """
        combos = []
        roll_item = None
        bank_item = None
        remaining_dice = 0
        turn_points = 0

        for item in items:
            name = item["name"]
            m_roll = re.match(r"^Roll (\d+) dice", name, re.I)
            if m_roll:
                roll_item = item
                remaining_dice = int(m_roll.group(1))
                continue

            m_bank = re.match(r"^Bank (\d+) points for this turn", name, re.I)
            if m_bank:
                bank_item = item
                turn_points = int(m_bank.group(1))
                continue

            m_combo = re.match(r"^(.*?) for (\d+) points", name, re.I)
            if m_combo:
                c_name = m_combo.group(1).strip()
                c_pts = int(m_combo.group(2))
                combos.append({
                    "index": item["index"],
                    "name": name,
                    "combo_name": c_name,
                    "points": c_pts,
                    "dice_used": self.estimate_combo_dice(c_name)
                })

        self.current_turn_points = turn_points

        # 1. Turn start: only Roll 6 dice and Bank 0 are present
        if len(combos) == 0 and roll_item and remaining_dice == 6 and turn_points == 0:
            self.combos_taken_this_roll = 0
            self.consecutive_bank_failures = 0
            return roll_item["index"], "roll", "Initial turn roll: Rolling 6 dice"

        # 2. Check if we MUST take a combination (nothing taken since last roll)
        must_take_combo = (self.combos_taken_this_roll == 0 and len(combos) > 0)

        # 3. Check if banking wins the game immediately
        if bank_item and (self.my_score + turn_points >= self.win_target):
            if combos:
                # Take highest combo to maximize victory score
                best_c = max(combos, key=lambda c: c["points"])
                return best_c["index"], "combo", f"Taking {best_c['name']} to seal victory"
            return bank_item["index"], "bank", f"🏆 BANKING {turn_points} PTS FOR IMMEDIATE VICTORY! (Total: {self.my_score + turn_points})"

        # 4. Check if Hot Dice is achievable (all remaining dice can be scored)
        total_combo_dice = sum(c["dice_used"] for c in combos)
        can_hot_dice = (remaining_dice > 0 and total_combo_dice >= remaining_dice)
        if can_hot_dice and combos:
            # Prioritize clearing all dice to unlock a fresh 6-dice roll!
            best_c = max(combos, key=lambda c: c["points"])
            return best_c["index"], "combo", f"Taking {best_c['name']} towards HOT DICE (Clearing all dice)"

        # 5. Check banking eligibility
        eligible_to_bank = False
        if bank_item:
            if not self.has_entered:
                eligible_to_bank = (turn_points >= self.entry_threshold) and (self.consecutive_bank_failures == 0)
            else:
                eligible_to_bank = (turn_points >= self.bank_threshold)

        # 6. Analyze Table Situation: Trailing vs Leading
        opponent_max_score = 0
        if self.scores:
            other_scores = [v for k, v in self.scores.items() if k.lower() not in ["you", (self.username or "").lower()]]
            if other_scores:
                opponent_max_score = max(other_scores)

        is_opponent_close = (opponent_max_score >= self.win_target * 0.75)
        am_i_far_behind = (opponent_max_score - self.my_score > 200)

        # 7. Evaluate if we WANT to bank:
        want_to_bank = False
        if eligible_to_bank and not can_hot_dice:
            if not self.has_entered:
                # Priority 1: Enter the board!
                if remaining_dice <= 3:
                    want_to_bank = True
                elif remaining_dice == 4 and turn_points >= 70:
                    want_to_bank = True
            else:
                # Already entered
                if is_opponent_close and am_i_far_behind:
                    # Trailing late-game: play more aggressively to catch up!
                    if remaining_dice == 1:
                        want_to_bank = True
                    elif remaining_dice == 2:
                        # Only bank on 2 dice if score is significant
                        if turn_points >= 50:
                            want_to_bank = True
                    elif remaining_dice == 3:
                        # Push on 3 dice unless turn points >= 70
                        if turn_points >= 70:
                            want_to_bank = True
                    elif remaining_dice == 4:
                        if turn_points >= 150:
                            want_to_bank = True
                else:
                    # Standard optimal play:
                    if remaining_dice == 1:
                        want_to_bank = True
                    elif remaining_dice == 2:
                        want_to_bank = True
                    elif remaining_dice == 3:
                        min_bank_at_3 = 45 if self.aggressive else 30
                        if turn_points >= min_bank_at_3:
                            want_to_bank = True
                    elif remaining_dice == 4:
                        min_bank_at_4 = 150 if self.aggressive else 100
                        if turn_points >= min_bank_at_4:
                            want_to_bank = True

        # If we WANT to bank:
        if want_to_bank and bank_item:
            # Take all remaining combinations before banking to maximize banked score
            if combos:
                best_c = max(combos, key=lambda c: c["points"])
                return best_c["index"], "combo", f"Taking {best_c['name']} before banking"
            return bank_item["index"], "bank", f"Banking {turn_points} points with {remaining_dice} dice left (Risk threshold reached)"

        # If we MUST take a combination (or choose to take one):
        if must_take_combo or combos:
            def combo_score(c):
                cname = c["combo_name"].lower()
                pts = c["points"]
                if "straight" in cname or "pair" in cname:
                    return 1000 + pts
                if any(k in cname for k in ["three", "four", "five", "six"]):
                    return 500 + pts
                if "single 1" in cname:
                    return 100 + pts
                return pts

            best_combo = max(combos, key=combo_score)

            # Preserve dice when rolling:
            # If we already took at least one scoring combo, and only Single 5 remains:
            # Rolling 4 or 5 dice has much lower farkle risk than losing a die for 5 pts
            if not must_take_combo and roll_item and best_combo["combo_name"].lower().startswith("single 5"):
                if remaining_dice >= 4:
                    self.combos_taken_this_roll = 0
                    return roll_item["index"], "roll", f"Rolling {remaining_dice} dice instead of taking Single 5 (Preserving dice count)"

            return best_combo["index"], "combo", f"Taking combination: {best_combo['name']}"

        # No combinations left: must roll or bank
        if roll_item and not want_to_bank:
            self.combos_taken_this_roll = 0
            return roll_item["index"], "roll", f"Rolling {remaining_dice} dice"

        if bank_item and eligible_to_bank:
            return bank_item["index"], "bank", f"Banking {turn_points} points"

        if roll_item:
            self.combos_taken_this_roll = 0
            return roll_item["index"], "roll", f"Rolling {remaining_dice} dice (Must continue)"

        return items[0]["index"], "default", f"Action: {items[0]['name']}"

    def step(self):
        new_lines = self.read_new_history()
        if new_lines:
            self.parse_history_events(new_lines)

        items = self.get_farkle_menu_items()
        if not items:
            return False

        active_items = [it for it in items if not it["disabled"]]
        if not active_items:
            return False

        target_idx, action_type, reason = self.decide_move(active_items)
        target_name = next((it["name"] for it in items if it["index"] == target_idx), f"Item {target_idx}")
        self.log(f"🎲 [Move] {reason} -> [{target_idx}] '{target_name}'")

        if action_type == "combo":
            self.combos_taken_this_roll += 1
        elif action_type == "roll":
            self.combos_taken_this_roll = 0

        self.select_and_activate(target_idx)
        time.sleep(self.delay)
        return True

    def run(self, max_turns=None):
        self.log("=" * 60)
        self.log("   AutoFarkle - Autonomous Player for Quentin C Gameroom")
        self.log("=" * 60)
        if not self.find_client():
            self.log("❌ Error: Quentin C's Gameroom client window not found!")
            self.log("Make sure qcgc is running and open to a Farkle table.")
            return

        self.log(f"✓ Connected to Quentin C Gameroom! Player: {self.username}")
        self.log(f"✓ Score: {self.my_score} pts | Entered: {self.has_entered}")
        self.log(f"✓ Monitoring game table...\n")
        
        turns_played = 0
        was_my_turn = False
        
        try:
            while self.running:
                items = self.get_farkle_menu_items()
                is_my_turn = (len(items) > 0 and any(not it["disabled"] for it in items))

                if is_my_turn:
                    if not was_my_turn:
                        turns_played += 1
                        self.combos_taken_this_roll = 0
                        self.log(f"\n👉 [Turn {turns_played}] It's your turn! (Score: {self.my_score} pts | Entered: {self.has_entered})")
                        was_my_turn = True

                    self.step()
                else:
                    if was_my_turn:
                        self.log("⏳ Turn ended. Waiting for opponent...")
                        was_my_turn = False
                        if max_turns and turns_played >= max_turns:
                            self.log(f"\nCompleted {turns_played} turn(s). Stopping as requested.")
                            break
                    
                    new_lines = self.read_new_history()
                    if new_lines:
                        self.parse_history_events(new_lines)
                        for l in new_lines:
                            if any(k in l.lower() for k in ["rolls the dice", "farkle", "fly away", "banks", "takes", "wins"]):
                                self.log(f"   [Game] {l}")
                    time.sleep(0.4)
        except KeyboardInterrupt:
            self.log("\nAutoFarkle stopped by user.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AutoFarkle for Quentin C Gameroom")
    parser.add_argument("--entry", type=int, default=50, help="Entry threshold (default: 50)")
    parser.add_argument("--bank", type=int, default=30, help="Bank threshold (default: 30)")
    parser.add_argument("--win", type=int, default=1000, help="Win target (default: 1000)")
    parser.add_argument("--delay", type=float, default=0.6, help="Move delay in seconds (default: 0.6)")
    parser.add_argument("--turns", type=int, default=None, help="Max turns to play (default: unlimited)")
    parser.add_argument("--aggressive", action="store_true", help="Play more aggressively")
    args = parser.parse_args()

    bot = FarkleBot(
        entry_threshold=args.entry,
        bank_threshold=args.bank,
        win_target=args.win,
        delay=args.delay,
        aggressive=args.aggressive
    )
    if bot.find_client():
        lines = bot.read_new_history()
        bot.parse_history_events(lines)
    bot.run(max_turns=args.turns)
