# AutoPlayroom - Autonomous Farkle & Uno Bot for Quentin C's Gameroom

AutoPlayroom is an intelligent, autonomous script suite that interfaces directly with Quentin C's Gameroom client (`qcgc.exe`) to play **Farkle** (10,000) and **Uno** in the background.

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

