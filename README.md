# AutoPlayroom - Autonomous Multigame Bot for Quentin C's Gameroom

AutoPlayroom is an intelligent, autonomous multi-game bot suite that interfaces directly with Quentin C's Gameroom client (`qcgc.exe`) to play **Farkle** (10,000), **Uno**, and **1000 Miles** (Mille Bornes) in the background.

## Key Features

- **Autonomous Table & Room Detection**:
  - Automatically identifies whether you are in the **Main Room (Reception)**, at a **Supported Table** (Farkle, Uno, 1000 Miles), or at an **Unsupported Game Table** (e.g. Poker, Belote, Yahtzee, Scrabble).
- **Dynamic Table Switching on the Fly**:
  - Automatically switches engines as you move between tables. When you leave a table or join a different game, AutoPlayroom seamlessly adapts without needing a restart!
- **Safe Main Room & Unsupported Game Idling**:
  - Safely stands by when in the lobby or when playing unsupported casual games with friends, and instantly awakens the moment you join a supported game.

## Supported Games

### 1. Farkle (`autofarkle.py`)
- **Statistical Decision Engine**:
  - Automatically identifies "Hot Dice" (clearing all 6 dice) to unlock free rolls.
  - Prioritizes high-scoring combinations (Straights, Three Pairs, Triplets).
  - Preserves dice count by declining isolated `Single 5`s when 4 or 5 dice can be rolled.
  - Risk-adjusted banking thresholds based on remaining dice count.
  - Dynamic late-game risk adjustment (trailing vs leading).
  - Multi-round lifecycle and automatic table reset tracking.

### 2. Uno (`autouno.py`)
Based on the official [Quentin C's Uno Rules & Variations](http://qcsalon.net/en/uno):
- **Full Rule & Variant Support**:
  - Standard cards (0–9, Skip, Reverse, Draw Two, Wild, Wild Draw Four).
  - **Responses**: Automatically stacks `Draw Two` on `Draw Two` and `+4` on `+4` to pass penalties to opponents.
  - **Uno No Mercy**: Supports `Discard All`, `Skip Everyone`, `+6`, `+10`, and `Color Roulette`.
  - **Uno Flip**: Supports light and dark side colors (`Orange`, `Pink`, `Purple`, `Teal`, `Draw 1`, `Draw 5`).
  - **Automated "Uno!" Calling**: Automatically declares "Uno!" (`U` key) immediately upon reaching 1 card.
  - **Counter-Denunciations**: Catches opponents who forgot to say "Uno!" and denounces them (`U` key).
  - **Buzzer Response**: Automatically slams the buzzer (`B` key) when a buzzer card is played.
  - **Smart Color Selection**: Selects the dominant color in hand when playing Wild / Color Roulette.
  - **Point Minimization Strategy**: Sheds higher-value number cards first to minimize penalty points if an opponent goes out.
  - **Automatic Drawing**: Automatically draws via `Spacebar` when no playable card exists.

### 3. 1000 Miles / Mille Bornes (`auto1000miles.py`)
Based on the official [Quentin C's 1000 Miles Rules](http://qcsalon.net/en/1000miles):
- **Precision Racing Engine**:
  - Exact 1000 miles victory calculation (prevents overshooting).
  - Enforces the top gear rule (maximum two 200-mile cards per round).
  - Automatically respects speed limit restrictions (restricts to 25/50 miles).
- **Sub-50ms Dirty Trick (Coup Fourré) Engine**:
  - Instantly responds to opponent attacks with matching immunities out of turn (`Right of way`, `Extra tank`, `Puncture-proof`, `Driving ace`).
  - Completely cancels attacks, steals turn, and claims +300 bonus points.
- **Smart Defense & Problem Resolution**:
  - Automatically cures problems in optimal order (`Gasoline` for out of gas, `Spare tire` for flats, `Repairs` for accidents, `Green light` for red lights/start, `End of speed limit`).
- **Threat Targeting**:
  - Tracks opponent mileage in real time and directs attacks (`Red light`, `Accident`, `Out of gas`, `Flat tire`, `Speed limit`) toward the leading active opponent.
- **Strategic Discard Engine**:
  - Intelligently discards unplayable distance cards exceeding required miles, redundant defenses for immunities already held, and duplicate cards when blocked.

---

## Getting Started

### Option 1: Standalone Windows Executable (No Python Required)
You do not need to install Python or any dependencies:
1. Download `AutoPlayroom.exe` from the [Latest Release](https://github.com/Dicerjessie/AutoPlayroom/releases/latest).
2. Double-click `AutoPlayroom.exe` or run it from the command line:
```powershell
.\AutoPlayroom.exe
```

### Option 2: Run from Python Source
Clone the repository and install the dependencies:
```powershell
pip install -r requirements.txt
python bot.py
```

### Launch Specifically by Game
To play 1000 Miles:
```powershell
python auto1000miles.py
```
or
```powershell
python bot.py --game miles
```

To play Uno:
```powershell
python autouno.py
```
or
```powershell
python bot.py --game uno
```

To play Farkle:
```powershell
python autofarkle.py
```
or
```powershell
python bot.py --game farkle
```

---

## Options & Arguments

### 1000 Miles Options (`auto1000miles.py` / `bot.py --game miles`)
| Flag | Description | Default |
|---|---|---|
| `--delay <sec>` | Delay in seconds between actions | `0.2` |

### Uno Options (`autouno.py` / `bot.py --game uno`)
| Flag | Description | Default |
|---|---|---|
| `--delay <sec>` | Delay in seconds between moves | `0.2` |
| `--no-intercept` | Disable automatic interceptions | Enabled |
| `--no-super` | Disable super interceptions | Enabled |
| `--allow-bluff` | Allow bluffing with Wild Draw Four even when holding matching colors | Disabled |
| `--straights` | Enable straights chaining | Disabled |

### Farkle Options (`autofarkle.py` / `bot.py --game farkle`)
| Flag | Description | Default |
|---|---|---|
| `--delay <sec>` | Move delay in seconds | `0.2` |
| `--turns <N>` | Maximum turns to play | Unlimited |
| `--aggressive` | Play more aggressively on 3/4 dice | Disabled |
| `--entry <pts>` | Entry threshold | `50` (auto-detected) |
| `--bank <pts>` | Bank threshold | `30` (auto-detected) |

---

## License

This project is licensed under the [MIT License](LICENSE).

