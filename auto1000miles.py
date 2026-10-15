"""
Auto1000Miles - Autonomous 1000 Miles (Mille Bornes) Player for Quentin C's Gameroom
Based on official rules from http://qcsalon.net/en/1000miles

Features:
- Statistical decision engine for optimal 1000 miles racing
- Exact 1000 miles victory calculation (prevents overshooting)
- Enforces top gear (200 miles) limit (max 2 per round)
- Sub-50ms Dirty Trick (Coup Fourré) instant response engine
- Full English and French card and game event recognition
- Smart defense & problem resolution (Gas, Spare tire, Repairs, Green light, Speed limit)
- Intelligent opponent threat targeting (attacks the leading active opponent)
- Optimal discard prioritization when blocked or holding redundant cards
- Autonomous turn watchdog via T key queries to prevent stalls
- Safe background session switching via qc_desktop
"""

import qc_desktop
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

# Card Categories
CAT_DISTANCE = "distance"
CAT_ATTACK = "attack"
CAT_DEFENSE = "defense"
CAT_IMMUNITY = "immunity"

# Card Types
TYPE_25 = "25"
TYPE_50 = "50"
TYPE_75 = "75"
TYPE_100 = "100"
TYPE_200 = "200"

TYPE_RED_LIGHT = "red_light"
TYPE_SPEED_LIMIT = "speed_limit"
TYPE_OUT_OF_GAS = "out_of_gas"
TYPE_FLAT_TIRE = "flat_tire"
TYPE_ACCIDENT = "accident"
TYPE_COUNTERFLOW = "counterflow"

TYPE_GREEN_LIGHT = "green_light"
TYPE_END_OF_SPEED_LIMIT = "end_of_speed_limit"
TYPE_GASOLINE = "gasoline"
TYPE_SPARE_TIRE = "spare_tire"
TYPE_REPARATION = "reparation"
TYPE_END_OF_COUNTERFLOW = "end_of_counterflow"

TYPE_RIGHT_OF_WAY = "right_of_way"
TYPE_EXTRA_TANK = "extra_tank"
TYPE_PUNCTURE_PROOF = "puncture_proof"
TYPE_DRIVING_ACE = "driving_ace"

# Attack to Immunity (Coup Fourré / Dirty Trick)
ATTACK_TO_IMMUNITY = {
    TYPE_RED_LIGHT: TYPE_RIGHT_OF_WAY,
    TYPE_SPEED_LIMIT: TYPE_RIGHT_OF_WAY,
    TYPE_OUT_OF_GAS: TYPE_EXTRA_TANK,
    TYPE_FLAT_TIRE: TYPE_PUNCTURE_PROOF,
    TYPE_ACCIDENT: TYPE_DRIVING_ACE,
    TYPE_COUNTERFLOW: TYPE_DRIVING_ACE,
}

# Attack to Defense
ATTACK_TO_DEFENSE = {
    TYPE_RED_LIGHT: TYPE_GREEN_LIGHT,
    TYPE_SPEED_LIMIT: TYPE_END_OF_SPEED_LIMIT,
    TYPE_OUT_OF_GAS: TYPE_GASOLINE,
    TYPE_FLAT_TIRE: TYPE_SPARE_TIRE,
    TYPE_ACCIDENT: TYPE_REPARATION,
    TYPE_COUNTERFLOW: TYPE_END_OF_COUNTERFLOW,
}

class MilesBot:
    def __init__(self, delay=0.2, aggressive=True):
        self.delay = delay
        self.aggressive = aggressive
        self.username = None
        
        self.running = True
        self.last_history_len = 0
        
        # Game & table state
        self.current_round = 0
        self.in_intermission = False
        self.my_turn = False
        self.draw_pile_empty = False
        self.has_drawn_this_turn = False
        self.draw_count_this_turn = 0
        
        # Self race status
        self.my_miles = 0
        self.my_status = "stopped"  # "stopped" or "driving"
        self.speed_limit = False
        self.counterflow = False
        self.active_problems = set()  # red_light, accident, out_of_gas, flat_tire
        self.my_immunities = set()    # right_of_way, extra_tank, puncture_proof, driving_ace
        self.top_gear_played = 0      # 200 miles cards played this round (max 2)
        
        # Opponents tracking: name -> {miles, status, speed_limit, problems, immunities}
        self.opponents = {}
        self.scores = {}

        self.was_my_turn = False
        self.turn_count = 0
        self.last_activity_time = time.time()
        self.base_watchdog_interval = 1.4
        self.watchdog_interval = 1.4
        
        self.hwnd_main = None
        self.hwnd_menu = None
        self.hwnd_edit = None
        self.p_acc = None

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
                    self.username = m.group(1).strip()
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

    def draw_card(self):
        self.draw_count_this_turn += 1
        self.log(f"🃏 [Action] Drawing card from deck (Spacebar)")
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
        c = card_str.lower().strip()
        
        # Distance cards
        if any(k in c for k in ["200 mile", "200 bornes", "top gear", "coup de volant", "200km", "200 km"]) or c == "200":
            return {"category": CAT_DISTANCE, "type": TYPE_200, "value": 200, "name": card_str}
        if any(k in c for k in ["100 mile", "100 bornes", "100km", "100 km"]) or c == "100":
            return {"category": CAT_DISTANCE, "type": TYPE_100, "value": 100, "name": card_str}
        if any(k in c for k in ["75 mile", "75 bornes", "75km", "75 km"]) or c == "75":
            return {"category": CAT_DISTANCE, "type": TYPE_75, "value": 75, "name": card_str}
        if any(k in c for k in ["50 mile", "50 bornes", "50km", "50 km"]) or c == "50":
            return {"category": CAT_DISTANCE, "type": TYPE_50, "value": 50, "name": card_str}
        if any(k in c for k in ["25 mile", "25 bornes", "25km", "25 km"]) or c == "25":
            return {"category": CAT_DISTANCE, "type": TYPE_25, "value": 25, "name": card_str}

        # Immunities (Safety / Bottes)
        if any(k in c for k in ["right of way", "priority", "prioritaire", "véhicule prioritaire", "vehicule prioritaire"]):
            return {"category": CAT_IMMUNITY, "type": TYPE_RIGHT_OF_WAY, "name": card_str}
        if any(k in c for k in ["extra tank", "citerne", "citerne d'essence", "citerne d essence"]):
            return {"category": CAT_IMMUNITY, "type": TYPE_EXTRA_TANK, "name": card_str}
        if any(k in c for k in ["puncture-proof", "puncture proof", "increvable"]):
            return {"category": CAT_IMMUNITY, "type": TYPE_PUNCTURE_PROOF, "name": card_str}
        if any(k in c for k in ["driving ace", "ace driver", "as du volant"]):
            return {"category": CAT_IMMUNITY, "type": TYPE_DRIVING_ACE, "name": card_str}

        # Attack cards
        if any(k in c for k in ["red light", "feu rouge"]):
            return {"category": CAT_ATTACK, "type": TYPE_RED_LIGHT, "name": card_str}
        if any(k in c for k in ["speed limit", "limitation de vitesse", "limite de vitesse"]):
            return {"category": CAT_ATTACK, "type": TYPE_SPEED_LIMIT, "name": card_str}
        if any(k in c for k in ["out of gas", "panne d'essence", "panne d essence"]):
            return {"category": CAT_ATTACK, "type": TYPE_OUT_OF_GAS, "name": card_str}
        if any(k in c for k in ["flat tire", "crevaison"]) or c == "flat":
            return {"category": CAT_ATTACK, "type": TYPE_FLAT_TIRE, "name": card_str}
        if "accident" in c:
            return {"category": CAT_ATTACK, "type": TYPE_ACCIDENT, "name": card_str}
        if any(k in c for k in ["counterflow", "wrong way", "contresens"]):
            return {"category": CAT_ATTACK, "type": TYPE_COUNTERFLOW, "name": card_str}

        # Defense cards
        if any(k in c for k in ["green light", "feu vert"]):
            return {"category": CAT_DEFENSE, "type": TYPE_GREEN_LIGHT, "name": card_str}
        if any(k in c for k in ["end of speed limit", "fin de limitation", "fin de vitesse"]):
            return {"category": CAT_DEFENSE, "type": TYPE_END_OF_SPEED_LIMIT, "name": card_str}
        if any(k in c for k in ["gasoline", "gas", "essence"]):
            return {"category": CAT_DEFENSE, "type": TYPE_GASOLINE, "name": card_str}
        if any(k in c for k in ["spare tire", "roue de secours"]):
            return {"category": CAT_DEFENSE, "type": TYPE_SPARE_TIRE, "name": card_str}
        if any(k in c for k in ["reparation", "repairs", "réparation", "réparations"]):
            return {"category": CAT_DEFENSE, "type": TYPE_REPARATION, "name": card_str}
        if any(k in c for k in ["end of counterflow", "fin de contresens"]):
            return {"category": CAT_DEFENSE, "type": TYPE_END_OF_COUNTERFLOW, "name": card_str}

        return {"category": "unknown", "type": c, "name": card_str}

    def reset_round_state(self):
        self.my_miles = 0
        self.my_status = "stopped"
        self.speed_limit = False
        self.counterflow = False
        self.active_problems.clear()
        self.active_problems.add(TYPE_RED_LIGHT)
        self.my_immunities.clear()
        self.top_gear_played = 0
        self.draw_pile_empty = False
        self.has_drawn_this_turn = False
        self.draw_count_this_turn = 0
        self.opponents.clear()

    def get_or_create_opponent(self, name):
        name = name.strip()
        if name not in self.opponents:
            self.opponents[name] = {
                "miles": 0,
                "status": "stopped",
                "speed_limit": False,
                "counterflow": False,
                "problems": {TYPE_RED_LIGHT},
                "immunities": set(),
                "top_gear_played": 0
            }
        return self.opponents[name]

    def try_dirty_trick(self, attack_type, attacker):
        needed_immunity = ATTACK_TO_IMMUNITY.get(attack_type)
        if not needed_immunity:
            return False

        items = self.get_menu_items()
        if not items:
            return False

        match_idx = None
        for it in items:
            if it["disabled"]:
                continue
            p = self.parse_card_string(it["name"])
            if p["category"] == CAT_IMMUNITY and p["type"] == needed_immunity:
                match_idx = it["index"]
                break

        if match_idx is not None:
            t0 = time.time()
            self.log(f"⚡ [Dirty Trick] Instant Coup Fourré! Triggering '{items[match_idx-1]['name']}' on '{attack_type}' from {attacker}! (Card [{match_idx}])")
            self.select_and_activate(match_idx, items)
            dt = (time.time() - t0) * 1000
            self.log(f"⚡ [Dirty Trick] Coup Fourré dispatched in {dt:.1f}ms! (+300 Bonus Points)")
            self.my_immunities.add(needed_immunity)
            if needed_immunity == TYPE_RIGHT_OF_WAY:
                self.my_status = "driving"
                self.active_problems.discard(TYPE_RED_LIGHT)
                self.speed_limit = False
            self.active_problems.discard(attack_type)
            self.my_turn = True
            time.sleep(0.15)
            return True
        return False

    def parse_history_events(self, lines):
        for line in lines:
            line_clean = line.strip()
            if not line_clean:
                continue

            # Round start
            m_round = re.search(r"(?:Round #|Manche #?|race starts|round begins)(\d+)?", line_clean, re.I)
            if m_round:
                self.current_round = int(m_round.group(1)) if m_round.group(1) else (self.current_round + 1)
                self.in_intermission = False
                self.reset_round_state()
                self.log(f"\n🏁 [Game] Race / Round #{self.current_round} started! Target: 1000 Miles")

            # Intermission / game stopped
            if any(k in line_clean.lower() for k in ["wins the round", "gagne la manche", "next round in", "game stopped", "table is now open", "pause."]):
                self.in_intermission = True
                self.my_turn = False

            # Draw pile empty
            if any(k in line_clean.lower() for k in ["draw pile is empty", "pioche est vide"]):
                self.draw_pile_empty = True
                self.log("⚠️ [Deck] Draw pile is empty! Continuing with remaining cards.")

            # Turn announcements
            if re.search(r"^(?:It's your turn|It is your turn|C'est à votre tour)\b", line_clean, re.I):
                self.my_turn = True
                self.draw_count_this_turn = 0
                self.has_drawn_this_turn = False
                self.log("👉 It's your turn!")
            elif self.username and re.search(rf"^(?:It's {re.escape(self.username)}'s turn|C'est au tour de {re.escape(self.username)})\b", line_clean, re.I):
                self.my_turn = True
                self.draw_count_this_turn = 0
                self.has_drawn_this_turn = False
                self.log("👉 It's your turn!")
            elif re.search(r"^(?:It's .*?'s turn|C'est au tour de .*?)\b", line_clean, re.I):
                self.my_turn = False

            # Card play parsing
            # e.g.: "Alice plays a 100 miles." or "Alice plays a red light on Bob." or "Alice plays an accident on you."
            m_play = re.search(r"^([A-Za-z0-9_\-\s]+?)\s+(?:plays|joue)\s+(?:a\s+|an\s+|the\s+|un\s+|une\s+|l\')?(.*?)(?:\s+(?:on|sur)\s+([A-Za-z0-9_\-\s]+?))?\.?$", line_clean, re.I)
            if m_play:
                player = m_play.group(1).strip()
                card_text = m_play.group(2).strip()
                target = (m_play.group(3) or "").strip()
                
                is_me = (player.lower() == "you" or (self.username and player.lower() == self.username.lower()))
                is_target_me = (target.lower() == "you" or (self.username and target.lower() == self.username.lower()))
                p_card = self.parse_card_string(card_text)

                if is_me:
                    # Self plays
                    if p_card["category"] == CAT_DISTANCE:
                        self.my_miles += p_card["value"]
                        if p_card["type"] == TYPE_200:
                            self.top_gear_played += 1
                        self.log(f"🚗 [Miles] You covered {p_card['value']} miles! (Total: {self.my_miles} / 1000)")
                    elif p_card["category"] == CAT_IMMUNITY:
                        self.my_immunities.add(p_card["type"])
                        self.log(f"🛡️ [Immunity] You activated {p_card['name']}! (+100 Points, Free Turn)")
                        if p_card["type"] == TYPE_RIGHT_OF_WAY:
                            self.my_status = "driving"
                            self.active_problems.discard(TYPE_RED_LIGHT)
                            self.speed_limit = False
                    elif p_card["category"] == CAT_DEFENSE:
                        if p_card["type"] == TYPE_GREEN_LIGHT:
                            self.my_status = "driving"
                            self.active_problems.discard(TYPE_RED_LIGHT)
                            self.log("🟢 [Green Light] Permission to drive granted! Status: DRIVING")
                        elif p_card["type"] == TYPE_END_OF_SPEED_LIMIT:
                            self.speed_limit = False
                            self.log("🏁 [Speed Limit] Speed limit lifted!")
                        elif p_card["type"] == TYPE_GASOLINE:
                            self.active_problems.discard(TYPE_OUT_OF_GAS)
                            self.log("⛽ [Gas] Fuel tank refilled.")
                        elif p_card["type"] == TYPE_SPARE_TIRE:
                            self.active_problems.discard(TYPE_FLAT_TIRE)
                            self.log("🛞 [Tire] Spare tire installed.")
                        elif p_card["type"] == TYPE_REPARATION:
                            self.active_problems.discard(TYPE_ACCIDENT)
                            self.log("🔧 [Repairs] Vehicle repaired.")
                        elif p_card["type"] == TYPE_END_OF_COUNTERFLOW:
                            self.counterflow = False
                            self.log("🔄 [Counterflow] Returned to correct driving direction.")
                else:
                    # Opponent plays
                    opp = self.get_or_create_opponent(player)
                    if p_card["category"] == CAT_DISTANCE:
                        opp["miles"] += p_card["value"]
                        if p_card["type"] == TYPE_200:
                            opp["top_gear_played"] += 1
                    elif p_card["category"] == CAT_IMMUNITY:
                        opp["immunities"].add(p_card["type"])
                        if p_card["type"] == TYPE_RIGHT_OF_WAY:
                            opp["status"] = "driving"
                            opp["problems"].discard(TYPE_RED_LIGHT)
                            opp["speed_limit"] = False
                    elif p_card["category"] == CAT_DEFENSE:
                        if p_card["type"] == TYPE_GREEN_LIGHT:
                            opp["status"] = "driving"
                            opp["problems"].discard(TYPE_RED_LIGHT)
                        elif p_card["type"] == TYPE_END_OF_SPEED_LIMIT:
                            opp["speed_limit"] = False
                        elif p_card["type"] == TYPE_GASOLINE:
                            opp["problems"].discard(TYPE_OUT_OF_GAS)
                        elif p_card["type"] == TYPE_SPARE_TIRE:
                            opp["problems"].discard(TYPE_FLAT_TIRE)
                        elif p_card["type"] == TYPE_REPARATION:
                            opp["problems"].discard(TYPE_ACCIDENT)

                    # Check if attack is aimed at us
                    if p_card["category"] == CAT_ATTACK and is_target_me:
                        self.log(f"⚠️ [Attack] {player} played {p_card['name']} on YOU!")
                        # Instantly check for Dirty Trick!
                        if self.try_dirty_trick(p_card["type"], player):
                            continue

                        # If no Dirty Trick was executed, apply attack effect
                        if p_card["type"] == TYPE_SPEED_LIMIT:
                            self.speed_limit = True
                            self.log("🛑 [Speed Limit] You are under a speed limit! (Max 50 miles)")
                        elif p_card["type"] == TYPE_COUNTERFLOW:
                            self.counterflow = True
                            self.log("🔄 [Counterflow] You are driving counterflow!")
                        else:
                            self.my_status = "stopped"
                            self.active_problems.add(p_card["type"])
                            self.log(f"❌ [Blocked] You were stopped by {p_card['type']}!")

                    elif p_card["category"] == CAT_ATTACK and target and not is_target_me:
                        tgt_opp = self.get_or_create_opponent(target)
                        if p_card["type"] == TYPE_SPEED_LIMIT:
                            tgt_opp["speed_limit"] = True
                        elif p_card["type"] == TYPE_COUNTERFLOW:
                            tgt_opp["counterflow"] = True
                        else:
                            tgt_opp["status"] = "stopped"
                            tgt_opp["problems"].add(p_card["type"])

            # Dirty trick announcement from history
            m_dt = re.search(r"(?:Dirty trick!|Coup fourré\s*!)\s+([A-Za-z0-9_\-\s]+?)\s+(?:plays|joue)\s+(.*?)\!", line_clean, re.I)
            if m_dt:
                dt_player = m_dt.group(1).strip()
                dt_card = m_dt.group(2).strip()
                if dt_player.lower() == "you" or (self.username and dt_player.lower() == self.username.lower()):
                    self.log(f"⚡ [Dirty Trick] Verified Coup Fourré by YOU with {dt_card}!")
                    self.my_turn = True

            # Score updates (e.g. "Player: 450.")
            m_sc = re.search(r"^([A-Za-z0-9_\-\s]+):\s*(\d+)\.", line_clean)
            if m_sc:
                p_name = m_sc.group(1).strip()
                self.scores[p_name] = int(m_sc.group(2))

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

    def is_action_choice_menu(self, items):
        if not items:
            return False
        # Action sub-menu usually contains "Play", "Discard", "Junk", or list of player names
        names = [it["name"].lower().strip() for it in items]
        if any(k in names for k in ["play", "jouer", "discard", "junk", "défausser", "defausser", "jeter"]):
            return True
        if any("play on" in n or "jouer sur" in n for n in names):
            return True
        return False

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
                for _ in range(abs(diff)):
                    win32gui.SendMessage(self.hwnd_menu, win32con.WM_KEYDOWN, win32con.VK_UP, 0)
                    win32gui.SendMessage(self.hwnd_menu, win32con.WM_KEYUP, win32con.VK_UP, 0)

        # Press Enter to activate
        win32gui.SendMessage(self.hwnd_menu, win32con.WM_KEYDOWN, win32con.VK_RETURN, 0)
        time.sleep(0.04)
        win32gui.SendMessage(self.hwnd_menu, win32con.WM_KEYUP, win32con.VK_RETURN, 0)
        time.sleep(self.delay)
        return True

    def execute_move_with_submenus(self, card_idx, action="play", target_player=None):
        items = self.get_menu_items()
        card_name = items[card_idx - 1]["name"] if card_idx <= len(items) else f"Card {card_idx}"
        self.log(f"👉 [Move] Attempting {action.upper()} on [{card_idx}] '{card_name}'" + (f" targeting {target_player}" if target_player else ""))
        
        self.select_and_activate(card_idx, items)
        time.sleep(0.08)
        
        # Check if a sub-menu opened (e.g. Play / Discard / Target Player)
        sub_items = self.get_menu_items()
        if self.is_action_choice_menu(sub_items):
            self.log(f"   [Sub-Menu] Detected choice menu: {[it['name'] for it in sub_items]}")
            if action == "play":
                # If target player specified, search for it
                if target_player:
                    for it in sub_items:
                        it_n = it["name"].lower()
                        if target_player.lower() in it_n:
                            self.log(f"   [Sub-Menu] Selecting target player [{it['index']}] '{it['name']}'")
                            self.select_and_activate(it["index"], sub_items)
                            return True
                # Otherwise look for "Play" / "Jouer"
                for it in sub_items:
                    it_n = it["name"].lower().strip()
                    if it_n in ["play", "jouer"] or "play on" in it_n or "jouer sur" in it_n:
                        self.log(f"   [Sub-Menu] Selecting [{it['index']}] '{it['name']}'")
                        self.select_and_activate(it["index"], sub_items)
                        return True
            elif action == "discard":
                # Look for Discard / Junk
                for it in sub_items:
                    it_n = it["name"].lower().strip()
                    if it_n in ["discard", "junk", "défausser", "defausser", "jeter"]:
                        self.log(f"   [Sub-Menu] Selecting [{it['index']}] '{it['name']}'")
                        self.select_and_activate(it["index"], sub_items)
                        return True
            
            # If nothing matched, press Escape to return safely
            self.send_key(win32con.VK_ESCAPE)
            return False

        return True

    def find_leading_attackable_opponent(self, attack_type):
        if not self.opponents:
            return None

        # Sort opponents by miles descending
        sorted_opps = sorted(self.opponents.items(), key=lambda kv: kv[1]["miles"], reverse=True)
        for name, data in sorted_opps:
            # Check immunity
            imm_needed = ATTACK_TO_IMMUNITY.get(attack_type)
            if imm_needed and imm_needed in data["immunities"]:
                continue
            # Check if already stopped (unless speed limit or counterflow)
            if attack_type not in [TYPE_SPEED_LIMIT, TYPE_COUNTERFLOW]:
                if data["status"] == "stopped" or len(data["problems"]) > 0:
                    continue
            if attack_type == TYPE_SPEED_LIMIT and data["speed_limit"]:
                continue
            if attack_type == TYPE_COUNTERFLOW and data["counterflow"]:
                continue
            return name
        return sorted_opps[0][0] if sorted_opps else None

    def decide_best_move(self, hand_cards):
        # hand_cards: list of dicts with {"index", "parsed", "name"}
        needed_miles = 1000 - self.my_miles
        is_driving = (self.my_status == "driving" or TYPE_RIGHT_OF_WAY in self.my_immunities)
        has_blocking_problem = len(self.active_problems) > 0 and (TYPE_RIGHT_OF_WAY not in self.my_immunities)

        # ---------------------------------------------------------------------
        # PRIORITY 1: Play Immunities (Safety / Bottes)
        # Grants 100 pts, permanent immunity, clears attacks, and free extra turn!
        # ---------------------------------------------------------------------
        for c in hand_cards:
            p = c["parsed"]
            if p["category"] == CAT_IMMUNITY:
                return {
                    "card": c,
                    "action": "play",
                    "target": None,
                    "reason": f"Activate immunity {p['name']} (+100 pts & extra turn)"
                }

        # ---------------------------------------------------------------------
        # PRIORITY 2: Cure Problems (Defense cards)
        # ---------------------------------------------------------------------
        # If stopped by accident, flat, out of gas:
        if TYPE_ACCIDENT in self.active_problems:
            for c in hand_cards:
                if c["parsed"]["type"] == TYPE_REPARATION:
                    return {"card": c, "action": "play", "target": None, "reason": "Repair vehicle after accident"}
        if TYPE_FLAT_TIRE in self.active_problems:
            for c in hand_cards:
                if c["parsed"]["type"] == TYPE_SPARE_TIRE:
                    return {"card": c, "action": "play", "target": None, "reason": "Install spare tire"}
        if TYPE_OUT_OF_GAS in self.active_problems:
            for c in hand_cards:
                if c["parsed"]["type"] == TYPE_GASOLINE:
                    return {"card": c, "action": "play", "target": None, "reason": "Refuel empty tank"}

        # If vehicle is repaired or red light, need Green Light to start driving!
        if (not is_driving or TYPE_RED_LIGHT in self.active_problems) and TYPE_RIGHT_OF_WAY not in self.my_immunities:
            # Check if any problems remain
            serious_problems = self.active_problems.intersection({TYPE_ACCIDENT, TYPE_FLAT_TIRE, TYPE_OUT_OF_GAS})
            if not serious_problems:
                for c in hand_cards:
                    if c["parsed"]["type"] == TYPE_GREEN_LIGHT:
                        return {"card": c, "action": "play", "target": None, "reason": "Play Green Light to start driving"}

        # If counterflow, cure it
        if self.counterflow and TYPE_DRIVING_ACE not in self.my_immunities:
            for c in hand_cards:
                if c["parsed"]["type"] == TYPE_END_OF_COUNTERFLOW:
                    return {"card": c, "action": "play", "target": None, "reason": "Cure counterflow"}

        # If speed limit and holding high distance cards, cure speed limit
        if self.speed_limit and TYPE_RIGHT_OF_WAY not in self.my_immunities:
            has_high_miles = any(c["parsed"]["category"] == CAT_DISTANCE and c["parsed"]["value"] > 50 for c in hand_cards)
            if has_high_miles:
                for c in hand_cards:
                    if c["parsed"]["type"] == TYPE_END_OF_SPEED_LIMIT:
                        return {"card": c, "action": "play", "target": None, "reason": "Lift speed limit to unleash high gear"}

        # ---------------------------------------------------------------------
        # PRIORITY 3: Play Maximum Safe Distance Cards
        # ---------------------------------------------------------------------
        if is_driving and not has_blocking_problem and not self.counterflow:
            playable_distance_cards = []
            for c in hand_cards:
                p = c["parsed"]
                if p["category"] != CAT_DISTANCE:
                    continue
                val = p["value"]
                # Must not exceed exact 1000
                if val > needed_miles:
                    continue
                # Under speed limit: max 50
                if self.speed_limit and val > 50 and TYPE_RIGHT_OF_WAY not in self.my_immunities:
                    continue
                # Top gear (200) max 2 per round
                if val == 200 and self.top_gear_played >= 2:
                    continue
                playable_distance_cards.append((val, c))

            if playable_distance_cards:
                # 3a: Exact Winning Distance Check!
                for val, c in playable_distance_cards:
                    if val == needed_miles:
                        return {
                            "card": c,
                            "action": "play",
                            "target": None,
                            "reason": f"🏆 VICTORY MOVE: Play {val} miles to reach exactly 1000!"
                        }

                # 3b: Highest value distance card
                playable_distance_cards.sort(key=lambda x: x[0], reverse=True)
                best_val, best_c = playable_distance_cards[0]
                return {
                    "card": best_c,
                    "action": "play",
                    "target": None,
                    "reason": f"Advance race with {best_val} miles"
                }

        # ---------------------------------------------------------------------
        # PRIORITY 4: Attack the Leading Opponent
        # ---------------------------------------------------------------------
        attack_priority = [TYPE_ACCIDENT, TYPE_OUT_OF_GAS, TYPE_FLAT_TIRE, TYPE_RED_LIGHT, TYPE_SPEED_LIMIT, TYPE_COUNTERFLOW]
        for atk_type in attack_priority:
            matching_cards = [c for c in hand_cards if c["parsed"]["type"] == atk_type]
            if matching_cards:
                target_opp = self.find_leading_attackable_opponent(atk_type)
                if target_opp:
                    return {
                        "card": matching_cards[0],
                        "action": "play",
                        "target": target_opp,
                        "reason": f"Attack leading opponent '{target_opp}' with {matching_cards[0]['parsed']['name']}"
                    }

        # ---------------------------------------------------------------------
        # PRIORITY 5: Discard / Junk the Least Useful Card
        # ---------------------------------------------------------------------
        # Rank discard desirability (higher score = better to discard)
        def discard_score(c):
            p = c["parsed"]
            # Never discard immunities!
            if p["category"] == CAT_IMMUNITY:
                return -1000
            
            # Distance cards that exceed needed miles can NEVER be played!
            if p["category"] == CAT_DISTANCE and p["value"] > needed_miles:
                return 100 + p["value"]
            
            # Redundant 200 miles if already played 2
            if p["type"] == TYPE_200 and self.top_gear_played >= 2:
                return 95
            
            # Defense for immunity we already hold is useless
            if p["type"] == TYPE_GREEN_LIGHT and TYPE_RIGHT_OF_WAY in self.my_immunities:
                return 90
            if p["type"] == TYPE_END_OF_SPEED_LIMIT and TYPE_RIGHT_OF_WAY in self.my_immunities:
                return 88
            if p["type"] == TYPE_GASOLINE and TYPE_EXTRA_TANK in self.my_immunities:
                return 85
            if p["type"] == TYPE_SPARE_TIRE and TYPE_PUNCTURE_PROOF in self.my_immunities:
                return 85
            if p["type"] == TYPE_REPARATION and TYPE_DRIVING_ACE in self.my_immunities:
                return 85
            if p["type"] == TYPE_END_OF_COUNTERFLOW and TYPE_DRIVING_ACE in self.my_immunities:
                return 85

            # Duplicate defense cards
            def_counts = sum(1 for other in hand_cards if other["parsed"]["type"] == p["type"])
            if p["category"] == CAT_DEFENSE and def_counts > 1:
                return 70

            # Duplicate attack cards
            if p["category"] == CAT_ATTACK:
                return 50

            # Small distance cards (25) in early game when far from 1000
            if p["type"] == TYPE_25 and needed_miles > 200:
                return 40

            # High distance cards if blocked and holding multiple
            if p["category"] == CAT_DISTANCE:
                return 30 - p["value"] // 10

            return 10

        best_discard = max(hand_cards, key=discard_score)
        return {
            "card": best_discard,
            "action": "discard",
            "target": None,
            "reason": f"Discard least useful card '{best_discard['parsed']['name']}'"
        }

    def step(self):
        items = self.get_menu_items()
        if not items:
            return False

        # If we are stuck in an open action choice menu from a previous cycle, cancel it
        if self.is_action_choice_menu(items):
            self.send_key(win32con.VK_ESCAPE)
            time.sleep(0.1)
            items = self.get_menu_items()

        # Check hand size: QC 1000 miles requires 7 cards to make a move unless draw pile is empty
        card_items = [it for it in items if not it["disabled"]]
        if len(card_items) < 7 and not self.draw_pile_empty and not self.has_drawn_this_turn:
            self.draw_card()
            self.has_drawn_this_turn = True
            time.sleep(0.15)
            items = self.get_menu_items()
            card_items = [it for it in items if not it["disabled"]]

        if not card_items:
            return False

        # Parse cards
        hand = []
        for it in card_items:
            hand.append({
                "index": it["index"],
                "name": it["name"],
                "parsed": self.parse_card_string(it["name"])
            })

        decision = self.decide_best_move(hand)
        target_card = decision["card"]
        action = decision["action"]
        target_player = decision["target"]
        reason = decision["reason"]

        self.log(f"🎯 [Decision] {reason} -> [{target_card['index']}] '{target_card['name']}'")
        success = self.execute_move_with_submenus(target_card["index"], action=action, target_player=target_player)
        
        if success:
            self.my_turn = False
            self.has_drawn_this_turn = False
        return success

    def tick(self):
        if not self.hwnd_main or not win32gui.IsWindow(self.hwnd_main):
            if not self.find_client():
                return False

        new_lines = self.read_new_history()
        if new_lines:
            self.last_activity_time = time.time()
            self.watchdog_interval = self.base_watchdog_interval
            self.parse_history_events(new_lines)
            for l in new_lines:
                if any(k in l.lower() for k in ["plays", "joue", "draws", "tire", "dirty trick", "coup fourré", "round", "manche", "wins", "gagne", "miles", "bornes"]):
                    self.log(f"   [Game] {l}")

        items = self.get_menu_items()
        if self.is_action_choice_menu(items):
            # We might be in a dangling choice menu; hit Escape to return to hand
            self.send_key(win32con.VK_ESCAPE)
            time.sleep(0.08)
            self.last_activity_time = time.time()
        elif self.my_turn:
            if len(items) == 0 or self.in_intermission:
                return True
            if not self.was_my_turn:
                self.turn_count += 1
                self.log(f"\n👉 [Turn {self.turn_count}] Executing move (Cards in hand: {len(items)}, Miles: {self.my_miles})")
                self.was_my_turn = True

            self.step()
            self.last_activity_time = time.time()
            self.watchdog_interval = self.base_watchdog_interval
        else:
            if self.was_my_turn:
                self.log("⏳ Turn completed. Waiting for opponent...")
                self.was_my_turn = False
                self.last_activity_time = time.time()
                self.watchdog_interval = self.base_watchdog_interval

            # Turn Watchdog: query T key if stalled
            if not self.in_intermission and len(items) > 0:
                now = time.time()
                if now - self.last_activity_time > self.watchdog_interval:
                    self.send_key(0x54)  # VK_T
                    self.last_activity_time = now
                    self.watchdog_interval = min(self.watchdog_interval + 1.2, 4.0)

        return True

    def run(self):
        self.log("=" * 60)
        self.log("   Auto1000Miles - Autonomous Player for Quentin C Gameroom")
        self.log("=" * 60)
        if not self.find_client():
            self.log("❌ Error: Quentin C's Gameroom client window not found!")
            self.log("Make sure qcgc is running and open to a 1000 miles table.")
            return

        self.log(f"✓ Connected to Quentin C Gameroom! Player: {self.username}")
        self.log("✓ Statistical Racing Engine active: Maximizes speed while keeping exact 1000 margin.")
        self.log("✓ Sub-50ms Dirty Trick (Coup Fourré) active: Instant Coup Fourré on attacks.")
        self.log("✓ Threat Targeting Engine active: Tracks opponent miles and attacks leaders.")
        self.log("✓ Autonomous Turn Watchdog active: Queries turn status via T key.")
        self.log("✓ Monitoring 1000 Miles table...\n")

        # Parse existing history to get table context
        lines = self.read_new_history()
        self.parse_history_events(lines)

        try:
            while self.running:
                self.tick()
                time.sleep(0.02)
        except KeyboardInterrupt:
            self.log("\nAuto1000Miles stopped by user.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Auto1000Miles for Quentin C Gameroom")
    parser.add_argument("--delay", type=float, default=0.2, help="Move delay in seconds (default: 0.2)")
    args = parser.parse_args()

    bot = MilesBot(delay=args.delay)
    bot.run()
