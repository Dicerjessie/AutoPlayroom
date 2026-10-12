"""
AutoUno - Autonomous Uno Player for Quentin C's Gameroom (Salon de QuentinC)
Interfaces cleanly with the desktop client via Win32 and IAccessible (MSAA).
Supports all Playroom Uno rules and variations:
- Strict Turn-State Tracking: NEVER plays out of turn, avoiding the 3-point penalty!
- Wild Color Request Detection: Recognizes when opponents ask for a color (e.g. 'wants yellow', 'asks for blue')
- Automated Wild Color Selection: Automatically asks for dominant hand color upon playing Wild / Wild Draw Four
- Draw Chain Gating: Strict matching (+2 on +2 only, +4 on +4 only; illegal combinations barred to avoid penalties)
- Uno No Mercy & Uno Flip (all card types, light/dark colors)
- Automatic "Uno!" calling (U key) when 1 card remains, plus auto-denunciations
- Automatic Buzzer slamming (B key)
- Strategic card shedding (shedding high numbers first to minimize penalty risk)
- Spacebar drawing on hwnd_menu with immediate evaluation of drawn cards
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

STANDARD_COLORS = ["red", "blue", "green", "yellow"]
FLIP_COLORS = ["orange", "pink", "purple", "teal"]
ALL_COLORS = STANDARD_COLORS + FLIP_COLORS

class UnoBot:
    def __init__(self, delay=0.2, enable_interceptions=True, super_interceptions=True, anti_bluff=True, straights=False):
        self.delay = delay
        self.enable_interceptions = enable_interceptions
        self.super_interceptions = super_interceptions
        self.bluff_enabled = anti_bluff
        self.straights_enabled = straights  # Disabled by default: prevents 'Too late!' penalties in fast games
        self.username = None
        self.hwnd_main = None
        self.hwnd_menu = None
        self.hwnd_edit = None
        self.p_acc = None
        
        self.running = True
        self.last_history_len = 0
        
        # Turn and table state
        self.my_turn = False
        self.active_color = None
        self.top_card_name = None
        self.top_card_value = None
        self.is_draw_chain = False
        self.draw_chain_type = None  # "normal_draw" (+2) or "wild_draw" (+4)
        self.draw_count_this_turn = 0
        self.max_draw_allowed = 1  # QC standard Uno: strictly 1 draw per turn
        self.has_drawn_this_turn = False
        self.interception_cooldown_until = 0.0
        self.failed_indices_this_turn = set()
        
        self.scores = {}
        self.my_cards = []
        self.current_round = 0
        self.in_intermission = False

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

    def send_key(self, vk_code):
        target = self.hwnd_menu or self.hwnd_main
        win32gui.SendMessage(target, win32con.WM_KEYDOWN, vk_code, 0)
        time.sleep(0.04)
        win32gui.SendMessage(target, win32con.WM_KEYUP, vk_code, 0)

    def say_uno(self):
        self.log("📣 [Action] Calling UNO! (U key)")
        self.send_key(0x55)  # VK_U

    def press_buzzer(self):
        self.log("🔔 [Action] Pressing Buzzer! (B key)")
        self.send_key(0x42)  # VK_B

    def draw_card(self):
        self.draw_count_this_turn += 1
        self.log(f"🃏 [Action] Drawing card ({self.draw_count_this_turn}) from deck (Spacebar)")
        target = self.hwnd_menu or self.hwnd_main
        win32gui.SendMessage(target, win32con.WM_KEYDOWN, win32con.VK_SPACE, 0)
        time.sleep(0.05)
        win32gui.SendMessage(target, win32con.WM_KEYUP, win32con.VK_SPACE, 0)
        time.sleep(self.delay)

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
            new_lines = lines
        self.last_history_len = len(lines)
        return new_lines

    def parse_card_string(self, card_str):
        card_str = card_str.lower().strip()
        if "wild" in card_str or "color roulette" in card_str or "buzzer" in card_str or "skip everyone" in card_str:
            return {
                "color": "wild",
                "value": card_str,
                "is_wild": True,
                "raw": card_str
            }
        
        for c in ALL_COLORS:
            if card_str.startswith(c + " "):
                val = card_str[len(c):].strip()
                return {
                    "color": c,
                    "value": val,
                    "is_wild": False,
                    "raw": card_str
                }
        
        return {
            "color": "unknown",
            "value": card_str,
            "is_wild": False,
            "raw": card_str
        }

    def parse_history_events(self, lines):
        for idx, line in enumerate(lines):
            # Round start
            m_round = re.search(r"Round #(\d+)", line)
            if m_round:
                self.current_round = int(m_round.group(1))
                self.in_intermission = False
                self.is_draw_chain = False
                self.draw_chain_type = None
                self.draw_count_this_turn = 0
                self.has_drawn_this_turn = False
                self.failed_indices_this_turn.clear()
                self.log(f"\n🎮 [Game] Round #{self.current_round} started!")

            # Intermission tracking
            if re.search(r"Next round in \d+ seconds", line, re.I):
                self.in_intermission = True
                self.my_turn = False
            elif any(k in line.lower() for k in ["table is now open", "game stopped", "press shift+p to start", "pause."]):
                self.in_intermission = True
                self.my_turn = False

            # Rule options
            m_draw = re.search(r"draw up to (\d+) cards", line, re.I)
            if m_draw:
                self.max_draw_allowed = int(m_draw.group(1))

            # Turn announcements
            if re.search(r"^It's your turn\.", line, re.I):
                self.my_turn = True
                self.draw_count_this_turn = 0
                self.has_drawn_this_turn = False
                self.failed_indices_this_turn.clear()
                self.log("👉 It's your turn!")
            elif self.username and re.search(rf"^It's {re.escape(self.username)}'s turn\.", line, re.I):
                self.my_turn = True
                self.draw_count_this_turn = 0
                self.has_drawn_this_turn = False
                self.failed_indices_this_turn.clear()
                self.log("👉 It's your turn!")
            elif re.search(r"^It's .*?'s turn\.", line, re.I):
                self.my_turn = False
                self.failed_indices_this_turn.clear()
            elif re.search(r"^Your turn is skipped", line, re.I):
                self.my_turn = False
                self.failed_indices_this_turn.clear()
                self.log("⏭️ Your turn was skipped.")
            elif re.search(r"^You can't play that card", line, re.I):
                self.log("⚠️ [Rule] Attempted card was rejected by game ('You can't play that card'). Turn still active.")
                self.my_turn = True
            elif re.search(r"^You (?:can't play\.|pass\.)", line, re.I):
                self.my_turn = False
                self.failed_indices_this_turn.clear()

            # Top card at table start
            m_start = re.search(r"visible on the table\.\s*It\'s (?:a\s+|an\s+)?([a-z0-9\+\s]+?)\.", line, re.I)
            if m_start:
                cinfo = self.parse_card_string(m_start.group(1))
                self.top_card_name = cinfo["raw"]
                self.active_color = cinfo["color"]
                self.top_card_value = cinfo["value"]
                self.log(f"[Table] Starting card: {self.top_card_name} (Color: {self.active_color})")

            # Plays card event: "<Player> plays a red skip." or "You play a blue draw two."
            # "<Player> plays a wild, and chooses blue."
            m_play = re.search(r"^([A-Za-z0-9_\-\s]+?)\s+(?:plays?|interception!.*?plays?)\s+(?:a\s+|an\s+)?([a-z0-9\+\s]+?)(?:,\s*and chooses\s+([a-z]+))?\.", line, re.I)
            if m_play:
                player = m_play.group(1).strip()
                card_name = m_play.group(2).strip()
                chosen_color = m_play.group(3)
                is_me = (player.lower() == "you" or (self.username and player.lower() == self.username.lower()))
                
                cinfo = self.parse_card_string(card_name)
                self.top_card_name = cinfo["raw"]
                self.top_card_value = cinfo["value"]
                
                if chosen_color and chosen_color.lower() in ALL_COLORS:
                    self.active_color = chosen_color.lower()
                    self.log(f"[Table] Wild color chosen: {self.active_color.upper()}")
                elif cinfo["color"] != "wild":
                    self.active_color = cinfo["color"]
                
                # Check draw chain:
                # If played by opponent, we may be obligated to respond or draw!
                if not is_me:
                    if "wild draw" in cinfo["raw"]:
                        self.is_draw_chain = True
                        self.draw_chain_type = "wild_draw"
                        self.log(f"[Table] Wild Draw Chain (+4) initiated by {player}!")
                    elif any(k in cinfo["value"] for k in ["draw", "+"]):
                        self.is_draw_chain = True
                        self.draw_chain_type = "normal_draw"
                        self.log(f"[Table] Normal Draw Chain (+2) initiated by {player}!")
                    else:
                        self.is_draw_chain = False
                        self.draw_chain_type = None

                    # Instant Interception Engine
                    # Check if this card is still on top of the table (no subsequent card played after it in this batch)
                    has_subsequent_play = any(
                        bool(re.search(r"\bplays?\b", l, re.I)) or "interception!" in l.lower()
                        for l in lines[idx+1:]
                    )
                    if (self.enable_interceptions and 
                        not has_subsequent_play and 
                        time.time() >= self.interception_cooldown_until):
                        self.try_intercept(cinfo, player)
                else:
                    # Our own play satisfies our turn; opponent faces the draw chain
                    self.is_draw_chain = False
                    self.draw_chain_type = None

            # Color chosen after wild: "<Player> wants red." / "<Player> asks for blue." / "You want green."
            m_wants = re.search(r"(?:wants|chooses|asks for|demands|veut|choisit|demande)\s+(?:a\s+)?([a-z]+)", line, re.I)
            if m_wants:
                col = m_wants.group(1).lower()
                if col in ALL_COLORS:
                    self.active_color = col
                    self.log(f"[Table] Color requested by player: {self.active_color.upper()}")

            # Standalone color name from top card query ('C' key)
            m_color_alone = re.search(r"^([a-z]+)$", line.strip(), re.I)
            if m_color_alone and m_color_alone.group(1).lower() in ALL_COLORS:
                self.active_color = m_color_alone.group(1).lower()
                self.log(f"[Table] Active color confirmed: {self.active_color.upper()}")

            # Drawing cards ends any draw chain!
            if re.search(r"\b(?:draws?|drew)\b.*?\b(?:card|cards)\b", line, re.I):
                self.is_draw_chain = False
                self.draw_chain_type = None

            # Buzzer event
            if "plays a buzzer" in line.lower() or "plays buzzer" in line.lower():
                self.press_buzzer()

            # Uno denounce opportunity
            if "forgot to say uno" in line.lower():
                self.say_uno()

            # Score updates
            m_sc = re.search(r"^([A-Za-z0-9_\-\s]+):\s*(\d+)\.", line)
            if m_sc:
                player = m_sc.group(1).strip()
                pts = int(m_sc.group(2))
                self.scores[player] = pts

            # Interception outcome announced in history:
            # e.g.: "Interception! Player1 plays a red 6." or "Player1 says: interception!"
            m_inter = re.search(r"(?:interception!\s+([A-Za-z0-9_\-\s]+?)\s+plays|([A-Za-z0-9_\-\s]+?)\s+says:\s*interception!)", line, re.I)
            if m_inter:
                inter_p = (m_inter.group(1) or m_inter.group(2) or "").strip()
                is_inter_me = (inter_p.lower() == "you" or (self.username and inter_p.lower() == self.username.lower()))
                if is_inter_me:
                    self.log(f"⚡ [Interception] Interception confirmed for {inter_p} -> It is your turn!")
                    self.my_turn = True
                    self.draw_count_this_turn = 0
                    self.has_drawn_this_turn = False
                else:
                    self.log(f"⚡ [Interception] Opponent {inter_p} intercepted -> Turn stolen!")
                    self.my_turn = False

            # Interception failure and 'Too late!' detection
            if any(k in line.lower() for k in ["failed an interception", "too late", "penalty point"]):
                m_fail = re.search(r"([A-Za-z0-9_\-\s]+?)\s+(?:has failed an interception|takes \d+ penalty points)", line, re.I)
                if m_fail:
                    fail_p = m_fail.group(1).strip()
                    if fail_p.lower() == "you" or (self.username and fail_p.lower() == self.username.lower()):
                        self.log(f"❌ [Penalty] You failed an interception ({line}) -> Cooldown active.")
                        self.interception_cooldown_until = time.time() + 0.35
                        self.my_turn = False
                elif re.search(r"^(?:you are |you\'re )?too late", line.strip(), re.I):
                    self.log(f"❌ [Penalty] Action rejected ('Too late!') -> Cooldown active.")
                    self.interception_cooldown_until = time.time() + 0.35
                    self.my_turn = False

            # Room option detections
            if "super interceptions are enabled" in line.lower():
                self.super_interceptions = True
                self.enable_interceptions = True
                self.log("✓ [Game Rules] Super interceptions detected: ACTIVE")
            elif "interceptions are enabled" in line.lower():
                self.enable_interceptions = True
                self.log("✓ [Game Rules] Interceptions detected: ACTIVE")
            elif "interceptions are disabled" in line.lower():
                self.enable_interceptions = False
                self.super_interceptions = False
                self.log("✓ [Game Rules] Interceptions detected: DISABLED")
            elif "bluff is enabled" in line.lower():
                self.bluff_enabled = True
                self.log("✓ [Game Rules] Bluff variant detected: ACTIVE (Anti-Bluff protection active)")
            elif "bluff is disabled" in line.lower():
                self.bluff_enabled = False
                self.log("✓ [Game Rules] Bluff variant detected: DISABLED")
            elif "straights are enabled" in line.lower():
                self.straights_enabled = True
                self.log("✓ [Game Rules] Straights detected: ACTIVE")
            elif "straights are disabled" in line.lower():
                self.straights_enabled = False
                self.log("✓ [Game Rules] Straights detected: DISABLED")

            # Bluff challenge detection
            if "says: you're bluffing!" in line.lower() or "accuses you of bluffing" in line.lower():
                self.log(f"⚠️ [Bluff] Opponent challenged bluff: {line}")
            elif "are a bad bluffer" in line.lower():
                self.log("❌ [Bluff] You were caught bluffing! Penalty incurred.")
            elif "they are wrong and must draw" in line.lower():
                self.log("✓ [Bluff] Opponent's bluff challenge was WRONG! Opponent penalized.")

    def try_intercept(self, cinfo, player):
        if time.time() < self.interception_cooldown_until:
            return False
        if self.in_intermission or self.is_draw_chain:
            return False

        card_raw = cinfo["raw"].lower().strip()
        
        # QC Rule: All cards can be intercepted, except wild and draw four cards
        if cinfo["is_wild"] or "draw four" in card_raw or "draw 4" in card_raw:
            return False

        items = self.get_menu_items()
        if not items:
            return False

        match_idx = None
        exact_match_idx = None
        super_match_idx = None
        match_name = None

        for it in items:
            if it["disabled"]:
                continue
            it_name = it["name"].lower().strip()
            p_it = self.parse_card_string(it_name)
            if p_it["is_wild"] or "draw four" in it_name or "draw 4" in it_name:
                continue

            if it_name == card_raw:
                exact_match_idx = it["index"]
                match_name = it["name"]
                break
            elif self.super_interceptions and p_it["value"] == cinfo["value"]:
                if super_match_idx is None:
                    super_match_idx = it["index"]
                    match_name = it["name"]

        match_idx = exact_match_idx if exact_match_idx is not None else super_match_idx

        if match_idx is None:
            return False

        t0 = time.time()
        inter_type = "exact" if exact_match_idx is not None else "super"
        self.log(f"⚡ [Interception] Triggering instant {inter_type} interception '{match_name}' on '{card_raw}' from {player}! (Card [{match_idx}])")

        will_have_one_card = (len(items) == 2)
        success = self.select_and_activate(match_idx, items)
        dt = (time.time() - t0) * 1000

        if success:
            self.log(f"⚡ [Interception] Interception dispatched in {dt:.1f}ms!")
            if will_have_one_card:
                time.sleep(0.08)
                self.say_uno()

            # QC Rule: The player who made the interception can play again!
            self.my_turn = True
            self.draw_count_this_turn = 0
            self.has_drawn_this_turn = False
            time.sleep(0.2)
            return True

        return False

    def get_menu_items(self):
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
        return items

    def is_color_choice_menu(self, items):
        if not items or len(items) > 8 or len(items) < 3:
            return False
        # In QC Uno, color choice menu contains exactly the playable colors:
        # e.g. ['yellow', 'red', 'blue', 'green']
        color_matches = sum(1 for it in items if it["name"].lower().strip() in ALL_COLORS)
        return color_matches == len(items)

    def select_and_activate(self, target_idx, items=None):
        if not items:
            items = self.get_menu_items()
        if not items or target_idx < 1 or target_idx > len(items):
            return False

        total = len(items)
        cur_idx = 1
        for item in items:
            if item["selected"]:
                cur_idx = item["index"]
                break

        # Rapid triangulation: find shortest keypath
        cost_direct = abs(target_idx - cur_idx)
        cost_home = 1 + (target_idx - 1)
        cost_end = 1 + (total - target_idx)
        min_cost = min(cost_direct, cost_home, cost_end)

        if min_cost == cost_home:
            win32gui.SendMessage(self.hwnd_menu, win32con.WM_KEYDOWN, win32con.VK_HOME, 0)
            win32gui.SendMessage(self.hwnd_menu, win32con.WM_KEYUP, win32con.VK_HOME, 0)
            diff = target_idx - 1
            for _ in range(diff):
                win32gui.SendMessage(self.hwnd_menu, win32con.WM_KEYDOWN, win32con.VK_DOWN, 0)
                win32gui.SendMessage(self.hwnd_menu, win32con.WM_KEYUP, win32con.VK_DOWN, 0)
        elif min_cost == cost_end:
            win32gui.SendMessage(self.hwnd_menu, win32con.WM_KEYDOWN, win32con.VK_END, 0)
            win32gui.SendMessage(self.hwnd_menu, win32con.WM_KEYUP, win32con.VK_END, 0)
            diff = total - target_idx
            for _ in range(diff):
                win32gui.SendMessage(self.hwnd_menu, win32con.WM_KEYDOWN, win32con.VK_UP, 0)
                win32gui.SendMessage(self.hwnd_menu, win32con.WM_KEYUP, win32con.VK_UP, 0)
        else:
            diff = target_idx - cur_idx
            if diff > 0:
                for _ in range(diff):
                    win32gui.SendMessage(self.hwnd_menu, win32con.WM_KEYDOWN, win32con.VK_DOWN, 0)
                    win32gui.SendMessage(self.hwnd_menu, win32con.WM_KEYUP, win32con.VK_DOWN, 0)
            elif diff < 0:
                for _ in range(-diff):
                    win32gui.SendMessage(self.hwnd_menu, win32con.WM_KEYDOWN, win32con.VK_UP, 0)
                    win32gui.SendMessage(self.hwnd_menu, win32con.WM_KEYUP, win32con.VK_UP, 0)

        # Confirm card play with Enter
        win32gui.SendMessage(self.hwnd_menu, win32con.WM_KEYDOWN, win32con.VK_RETURN, 0)
        time.sleep(0.02)
        win32gui.SendMessage(self.hwnd_menu, win32con.WM_KEYUP, win32con.VK_RETURN, 0)
        return True

    def _extract_parsed(self, c):
        if isinstance(c, dict):
            if "parsed" in c:
                return c["parsed"]
            if "color" in c and "value" in c:
                return c
            if "name" in c:
                return self.parse_card_string(c["name"])
        return self.parse_card_string(str(c))

    def is_colored_card_playable(self, card_info):
        info = self._extract_parsed(card_info)
        if info["is_wild"]:
            return False
        # Matches active color (including color requested after a Wild!)
        if self.active_color and info["color"] == self.active_color:
            return True
        # Matches number/symbol (e.g. 8 on 8, reverse on reverse)
        if self.top_card_value and info["value"] == self.top_card_value:
            return True
        return False

    def is_card_playable(self, card_info, cards_in_hand=None):
        card_info = self._extract_parsed(card_info)
        val = card_info["value"].lower()
        raw = card_info["raw"].lower()
        is_wd = card_info["is_wild"] and ("draw" in val or "+" in val or "draw four" in raw or "draw 4" in raw or "draw six" in raw or "draw ten" in raw)

        # Draw chain obligation (e.g. responding to +2 or +4 before drawing)
        # Rule: A draw two cannot be placed on top of a wild draw four and vice versa!
        if self.is_draw_chain:
            if self.draw_chain_type == "wild_draw":
                if not is_wd:
                    return False
                # Under QC Bluff rules: Stacking a Wild Draw Four when holding ANY card of the requested color is a BLUFF!
                if self.bluff_enabled and cards_in_hand:
                    has_requested_color = any(
                        (not self._extract_parsed(c)["is_wild"]) and self._extract_parsed(c)["color"] == self.active_color 
                        for c in cards_in_hand
                    )
                    if has_requested_color:
                        # Avoid bluffing penalty (which would be 10+ cards)! Wisely draw instead.
                        return False
                return True
            elif self.draw_chain_type == "normal_draw":
                # Can ONLY respond with another normal draw card (draw two)
                return (not card_info["is_wild"]) and ("draw" in val or "+" in val)
            return False

        # Normal turns:
        # Strict Anti-Bluffing Protection for Wild Draw Four:
        if is_wd:
            if self.bluff_enabled and cards_in_hand:
                # If we have ANY card of the active color, playing Wild Draw Four is 100% a bluff!
                has_matching_color = any(
                    (not self._extract_parsed(c)["is_wild"]) and self._extract_parsed(c)["color"] == self.active_color
                    for c in cards_in_hand
                )
                if has_matching_color:
                    return False
                # If we have ANY legal colored card, we MUST play the colored card!
                has_legal_colored = any(
                    self.is_colored_card_playable(self._extract_parsed(c))
                    for c in cards_in_hand
                    if (c.get("index") if isinstance(c, dict) else None) not in self.failed_indices_this_turn
                )
                if has_legal_colored:
                    return False
            return True

        # Regular Wild cards (color change without draw) can ALWAYS be played on normal turns without bluff risk!
        if card_info["is_wild"]:
            return True

        # Matches active color (including color requested after a Wild!)
        if self.active_color and card_info["color"] == self.active_color:
            return True

        # Matches number/symbol (e.g. 8 on 8, reverse on reverse)
        if self.top_card_value and card_info["value"] == self.top_card_value:
            return True

        return False

    def find_straight_chain(self, card, hand):
        val = card["parsed"]["value"]
        if not val.isdigit():
            return []
        num = int(val)
        color = card["parsed"]["color"]
        if color not in ALL_COLORS:
            return []

        other_cards = [c for c in hand if c["index"] != card["index"]]

        # Ascending straight chain
        asc_chain = []
        curr = num
        rem = list(other_cards)
        while True:
            next_cand = None
            for c in rem:
                c_val = c["parsed"]["value"]
                if c["parsed"]["color"] == color and c_val.isdigit() and int(c_val) == curr + 1:
                    next_cand = c
                    break
            if next_cand:
                asc_chain.append(next_cand)
                rem.remove(next_cand)
                curr += 1
            else:
                break

        # Descending straight chain
        desc_chain = []
        curr = num
        rem = list(other_cards)
        while True:
            next_cand = None
            for c in rem:
                c_val = c["parsed"]["value"]
                if c["parsed"]["color"] == color and c_val.isdigit() and int(c_val) == curr - 1:
                    next_cand = c
                    break
            if next_cand:
                desc_chain.append(next_cand)
                rem.remove(next_cand)
                curr -= 1
            else:
                break

        return asc_chain if len(asc_chain) >= len(desc_chain) else desc_chain

    def decide_card(self, cards):
        # Count color distribution in hand (excluding wilds)
        color_counts = {}
        for c in cards:
            col = c["parsed"]["color"]
            if col != "wild" and col in ALL_COLORS:
                color_counts[col] = color_counts.get(col, 0) + 1

        dominant_color = max(color_counts, key=color_counts.get) if color_counts else "red"

        # Filter playable cards not in failed_indices
        playable = [c for c in cards if self.is_card_playable(c["parsed"], cards_in_hand=cards) and c["index"] not in self.failed_indices_this_turn]

        if not playable:
            return None, "No playable card"

        is_two_player = (len(self.scores) <= 2)

        # Evaluate playable cards
        def evaluate_card(c):
            info = c["parsed"]
            val = info["value"]
            score = 0

            # Discard all bonus
            if "discard all" in val:
                count = color_counts.get(info["color"], 0)
                score += 500 * count

            # Action cards (Skip, Reverse)
            # In 2-player games, Skip and Reverse give an immediate extra turn!
            if "skip" in val or "reverse" in val:
                score += 350 if is_two_player else 150

            # Draw Two / Draw X cards
            if any(k in val for k in ["draw two", "+2", "draw five", "draw six", "draw ten"]):
                score += 250

            # Matches dominant color: strong preference to maintain momentum
            if info["color"] == dominant_color:
                score += 80

            # Shed higher-value number cards first to minimize penalty points!
            if val.isdigit():
                score += int(val) * 10
                # Straights bonus: prioritize cards that can dump multiple cards in a sequence!
                if self.straights_enabled:
                    straight = self.find_straight_chain(c, cards)
                    if straight:
                        score += 260 * len(straight)

            # Wild card strategy:
            # Save Wilds for when we have no other colored options, or when we have <= 2 cards
            if info["is_wild"]:
                raw = info["raw"].lower()
                is_wd = ("draw" in val or "+" in val or "draw four" in raw or "draw 4" in raw)
                has_colored = any(not p["parsed"]["is_wild"] for p in playable)
                if is_wd:
                    # Wild Draw Four is only played when we have no colored options (to avoid bluffing)
                    if has_colored:
                        score -= 500  # Strictly prioritize colored card
                    elif len(cards) <= 2:
                        score += 90   # Great for finishing when legally permissible
                    else:
                        score += 40
                else:
                    # Regular Wild (safe from bluffing)
                    if len(cards) <= 2:
                        score += 120  # Great for closing out round
                    elif has_colored:
                        score -= 100  # Save wild for later
                    else:
                        score += 50   # Only wild is playable

            return score

        best_card = max(playable, key=evaluate_card)
        return best_card, f"Playing {best_card['name']}"

    def handle_color_choice(self, items):
        color_counts = {}
        for c in self.my_cards:
            col = c.get("color")
            if col and col in ALL_COLORS and col != "wild":
                color_counts[col] = color_counts.get(col, 0) + 1

        best_color = max(color_counts, key=color_counts.get) if color_counts else "red"
        self.log(f"🎨 [Color Choice] Asking for color: {best_color.upper()}")

        target_idx = 1
        for it in items:
            name = it["name"].lower().strip()
            if best_color == name:
                target_idx = it["index"]
                break

        self.select_and_activate(target_idx)
        time.sleep(0.3)
        
        # Verify color was acknowledged
        new_items = self.get_menu_items()
        if not self.is_color_choice_menu(new_items):
            self.active_color = best_color
            self.my_turn = False
            self.log(f"✓ [Color Choice] Confirmed: {best_color.upper()}")
        else:
            self.log("⚠️ Color choice not yet acknowledged, retrying...")
        time.sleep(self.delay)

    def step(self):
        new_lines = self.read_new_history()
        if new_lines:
            self.parse_history_events(new_lines)

        items = self.get_menu_items()
        if not items:
            return False

        # Color selection menu (active after playing a Wild)
        if self.is_color_choice_menu(items):
            self.handle_color_choice(items)
            return True

        # CRITICAL PENALTY PROTECTION:
        # Playing out of turn in Uno gives 3 penalty points!
        # MUST verify that it is explicitly our turn!
        if not self.my_turn:
            return False

        cards = []
        for it in items:
            if not it["disabled"]:
                cinfo = self.parse_card_string(it["name"])
                cards.append({
                    "index": it["index"],
                    "name": it["name"],
                    "parsed": cinfo
                })

        self.my_cards = [c["parsed"] for c in cards]

        best_card, reason = self.decide_card(cards)

        if best_card:
            self.log(f"🃏 [Move] {reason} -> [{best_card['index']}] '{best_card['name']}'")
            
            # If playing this card leaves 1 card in hand: call UNO!
            will_have_one_card = (len(cards) == 2)
            is_wild_play = best_card["parsed"]["is_wild"]
            card_idx = best_card["index"]
            
            cinfo = best_card["parsed"]
            card_raw = cinfo["raw"].lower().strip()

            # Pre-evaluate whether we hold OTHER cards that can self-intercept this play!
            # QC Rule: All cards can be intercepted, except wild and draw four cards.
            # Normal interception: exact duplicate in hand.
            # Super interception: any card with same number/symbol in hand.
            self_intercept_candidates = []
            if self.enable_interceptions and not is_wild_play and "draw four" not in card_raw and "draw 4" not in card_raw:
                for c in cards:
                    if c["index"] == card_idx:
                        continue  # Must be a DIFFERENT card in hand!
                    c_info = c["parsed"]
                    if c_info["is_wild"] or "draw four" in c_info["raw"].lower() or "draw 4" in c_info["raw"].lower():
                        continue
                    if c["name"].lower().strip() == card_raw:
                        # Exact duplicate
                        self_intercept_candidates.append((True, c))
                    elif self.super_interceptions and c_info["value"] == cinfo["value"]:
                        # Super interception (same value/symbol across different colors)
                        self_intercept_candidates.append((False, c))

            # Pre-evaluate whether we hold consecutive cards for a Straight!
            # QC Rule: Whoever has several cards of the same color with consecutive numbers
            # has the right to put them down in the same turn, in ascending or descending order.
            straight_candidates = []
            if self.straights_enabled and cinfo["value"].isdigit():
                straight_candidates = self.find_straight_chain(best_card, cards)

            # Keep remaining cards for color choice calculation if wild
            self.my_cards = [c["parsed"] for c in cards if c["index"] != card_idx]
            
            self.select_and_activate(card_idx)
            
            if will_have_one_card:
                time.sleep(0.12)
                self.say_uno()

            # Execute Self-Interception if and only if we had pre-verified candidate cards!
            self_intercepted = False
            if self_intercept_candidates:
                # Wait for the client listbox to reflect the removal of the played card (hand count drops by 1)
                t_wait = time.time()
                rem_items = []
                while time.time() - t_wait < 0.35:
                    rem_items = self.get_menu_items()
                    if len(rem_items) < len(cards):
                        break
                    time.sleep(0.02)
                
                if len(rem_items) < len(cards):
                    target_cand = None
                    for is_exact, cand in sorted(self_intercept_candidates, key=lambda x: 0 if x[0] else 1):
                        cand_name = cand["name"].lower().strip()
                        for it in rem_items:
                            if not it["disabled"] and it["name"].lower().strip() == cand_name:
                                target_cand = (it["index"], it["name"], is_exact)
                                break
                        if target_cand:
                            break

                    if target_cand:
                        c_idx, c_name, is_exact = target_cand
                        inter_type = "duplicate" if is_exact else "super"
                        self.log(f"⚡ [Self-Interception] Self-intercepting own '{best_card['name']}' with {inter_type} '{c_name}'! (Card [{c_idx}])")
                        self.select_and_activate(c_idx, rem_items)
                        self_intercepted = True
                        if len(rem_items) == 2:
                            time.sleep(0.08)
                            self.say_uno()
                        # QC Rule: The player who made the interception can play again!
                        self.my_turn = True
                        self.draw_count_this_turn = 0
                        self.has_drawn_this_turn = False
                        time.sleep(0.1)
                        return True

            # Execute Straight sequence if available and no self-interception occurred
            straight_executed = False
            if not self_intercepted and self.straights_enabled and straight_candidates:
                expected_len = len(cards)
                for cand in straight_candidates:
                    t_wait = time.time()
                    rem_items = []
                    while time.time() - t_wait < 0.35:
                        rem_items = self.get_menu_items()
                        if len(rem_items) < expected_len:
                            break
                        time.sleep(0.02)
                    
                    if len(rem_items) < expected_len:
                        c_idx = None
                        cand_name = cand["name"].lower().strip()
                        for it in rem_items:
                            if not it["disabled"] and it["name"].lower().strip() == cand_name:
                                c_idx = it["index"]
                                break
                        if c_idx is not None:
                            self.log(f"⚡ [Straight] Chaining consecutive '{cand['name']}' in straight sequence! (Card [{c_idx}])")
                            self.select_and_activate(c_idx, rem_items)
                            straight_executed = True
                            expected_len = len(rem_items)
                            if len(rem_items) == 2:
                                time.sleep(0.08)
                                self.say_uno()
                        else:
                            break
                    else:
                        break

            if is_wild_play:
                # Wait for color choice menu to appear
                chosen = False
                for _ in range(15):
                    time.sleep(0.1)
                    new_items = self.get_menu_items()
                    if self.is_color_choice_menu(new_items):
                        self.handle_color_choice(new_items)
                        chosen = True
                        break
                if not chosen:
                    self.log("⚠️ Color choice menu pending...")
            elif not self_intercepted and not straight_executed:
                # Normal card play: assume turn ended unless history indicates otherwise (e.g. Skip/Reverse in 2p, or rejection)
                self.my_turn = False
                time.sleep(0.35)
                check_lines = self.read_new_history()
                if check_lines:
                    self.parse_history_events(check_lines)
                    for l in check_lines:
                        if re.search(r"^You can't play that card", l, re.I):
                            self.failed_indices_this_turn.add(card_idx)
                            self.my_turn = True
                            return True
            elif straight_executed:
                # Straight completed: turn passes to next player
                self.my_turn = False
                time.sleep(0.2)
                
            time.sleep(self.delay)
            return True
        else:
            # Cannot play: draw card
            if self.is_draw_chain:
                # In a draw chain (+2 or +4), pressing spacebar draws the penalty cards and ends turn
                self.log(f"🔄 [Draw Chain] Drawing penalty cards ({self.draw_chain_type}) from deck (Spacebar)")
                self.draw_card()
                self.is_draw_chain = False
                self.draw_chain_type = None
                self.my_turn = False
                self.has_drawn_this_turn = True
                time.sleep(0.35)
                return True

            if not self.has_drawn_this_turn:
                self.log(f"🔄 [Move] {reason} -> Drawing card")
                self.draw_card()
                self.has_drawn_this_turn = True
                time.sleep(0.35)
                check_lines = self.read_new_history()
                if check_lines:
                    self.parse_history_events(check_lines)
                
                # Check if server ended our turn (e.g. "You can't play." or next player's turn)
                if not self.my_turn:
                    return True

                # Check if any card in hand is now playable
                rem_cards = self.get_menu_items()
                parsed_cards = [
                    {"index": c["index"], "name": c["name"], "parsed": self.parse_card_string(c["name"])}
                    for c in rem_cards if not c["disabled"]
                ]
                can_play = any(
                    self.is_card_playable(c["parsed"], cards_in_hand=parsed_cards)
                    for c in parsed_cards
                )
                
                if not can_play:
                    self.log("🔄 Drawn card is not playable. Passing turn.")
                    self.my_turn = False
                    # DO NOT SEND SPACEBAR! Spacebar draws cards, it does not pass!
                else:
                    self.log("🃏 Drawn card is playable! Executing on next iteration.")
            else:
                self.log("🔄 Already drew this turn. Passing turn.")
                self.my_turn = False
                # DO NOT SEND SPACEBAR!
            return True

    def run(self):
        self.log("=" * 60)
        self.log("   AutoUno - Autonomous Uno Player for Quentin C Gameroom")
        self.log("=" * 60)
        if not self.find_client():
            self.log("❌ Error: Quentin C's Gameroom client window not found!")
            self.log("Make sure qcgc is running and open to an Uno table.")
            return

        self.log(f"✓ Connected to Quentin C Gameroom! Player: {self.username}")
        self.log(f"✓ Penalty protection active: Plays strictly on verified turns.")
        self.log(f"✓ Wild color awareness active: Detects opponent requests & asks for dominant colors.")
        self.log(f"✓ Draw chain gating active: Strictly separates normal (+2) and wild (+4) draw chains.")
        self.log(f"✓ Anti-Bluffing Protection active: Wild Draw 4 played strictly when holding no matching colors." if self.bluff_enabled else "⚠️ Anti-Bluffing protection disabled.")
        self.log(f"✓ Straights Engine: {'ACTIVE (chains consecutive same-color ranks)' if self.straights_enabled else 'DISABLED'}")
        self.log(f"✓ Rapid Interception Engine active: Sub-50ms triangulation on matching opponent cards." if self.enable_interceptions else "✓ Interceptions disabled.")
        self.log(f"✓ Super Interceptions: {'ACTIVE (matches value/symbol across colors)' if self.super_interceptions else 'DISABLED (exact duplicate only)'}")
        self.log(f"✓ Autonomous Watchdog active: Queries turn status via T key to prevent stalls.")
        self.log(f"✓ Monitoring Uno table...\n")

        # Parse existing history to get table context
        lines = self.read_new_history()
        self.parse_history_events(lines)

        was_my_turn = False
        turn_count = 0
        last_activity_time = time.time()
        base_watchdog_interval = 1.2
        watchdog_interval = base_watchdog_interval

        try:
            while self.running:
                new_lines = self.read_new_history()
                if new_lines:
                    last_activity_time = time.time()
                    watchdog_interval = base_watchdog_interval
                    self.parse_history_events(new_lines)
                    for l in new_lines:
                        if any(k in l.lower() for k in ["plays", "draws", "uno", "interception", "round", "wins", "buzzer", "wants", "chooses"]):
                            self.log(f"   [Game] {l}")

                items = self.get_menu_items()
                if self.is_color_choice_menu(items):
                    self.handle_color_choice(items)
                    last_activity_time = time.time()
                    watchdog_interval = base_watchdog_interval
                elif self.my_turn:
                    if len(items) == 0 or self.in_intermission:
                        time.sleep(0.015)
                        continue
                    if not was_my_turn:
                        turn_count += 1
                        self.log(f"\n👉 [Turn {turn_count}] Executing move (Cards in hand: {len(items)})")
                        was_my_turn = True

                    self.step()
                    last_activity_time = time.time()
                    watchdog_interval = base_watchdog_interval
                else:
                    if was_my_turn:
                        self.log("⏳ Turn completed. Waiting for opponent...")
                        was_my_turn = False
                        last_activity_time = time.time()
                        watchdog_interval = base_watchdog_interval

                    # Autonomous Turn Watchdog:
                    # In QC Uno, after interceptions or certain plays, the server does not always print
                    # "It's your turn." Querying 'T' asks the server to state the turn immediately.
                    if not self.in_intermission and len(items) > 0:
                        now = time.time()
                        if now - last_activity_time > watchdog_interval:
                            self.send_key(0x54)  # VK_T
                            last_activity_time = now
                            watchdog_interval = min(watchdog_interval + 1.2, 4.0)

                    time.sleep(0.015)
        except KeyboardInterrupt:
            self.log("\nAutoUno stopped by user.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AutoUno for Quentin C Gameroom")
    parser.add_argument("--delay", type=float, default=0.2, help="Move delay in seconds (default: 0.2)")
    parser.add_argument("--no-intercept", action="store_true", help="Disable automatic interceptions")
    parser.add_argument("--no-super", action="store_true", help="Disable super interceptions (exact matches only)")
    parser.add_argument("--allow-bluff", action="store_true", help="Allow playing Wild Draw Four as a bluff even when holding matching colors")
    parser.add_argument("--straights", action="store_true", help="Enable straights chaining (disabled by default to prevent penalties)")
    args = parser.parse_args()

    bot = UnoBot(
        delay=args.delay,
        enable_interceptions=not args.no_intercept,
        super_interceptions=not args.no_super,
        anti_bluff=not args.allow_bluff,
        straights=args.straights
    )
    bot.run()
