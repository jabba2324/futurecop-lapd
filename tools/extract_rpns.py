#!/usr/bin/env python3
"""Extract the RPNS script pool (and decode the Future Cop RPN script VM) from PS1 MIS archives.

WHAT RPNS IS
  RPNS = "Reverse-Polish-Notation Script" pool. It is one blob of byte-code scripts per
  level, each script terminated by a 0x00 opcode. Nothing points *into* RPNS from its
  own payload; instead every resource's SHDR header carries three absolute offsets into
  it (see "SHDR fields" below). The same byte-code language is used by Cfun (tEXT
  section, see extract_fun.py) and by the per-resource load conditions in SHDR.

  Evidence (SLUS_007.39; first by hand-disassembly, then confirmed with the Ghidra
  decompilation of every function cited below):
    * 0x80069d68 is the interpreter: eval(code, context, initial_arg, self_actor).
      It is called from the Cfun ticker (0x80049564), from actor code with
      actor+148/+152/+156 as the code pointer, from the SHDR loader (0x80028bcc) and
      from the Csac spawner (0x8006125c).
    * The resource loader (0x80024fa0) relocates SHDR words +0x24/+0x28/+0x2c by adding
      the RPNS base pointer (gp+0x7e0) before dispatching by type, i.e. they are offsets
      into RPNS. 0x8002a5f4 (the common actor init) copies them into the actor struct at
      +148/+152/+156.
    * Load conditions (0x80028bcc): for every type except Csac the loader evaluates
      load_cond_0 and skips the resource when it returns 0; otherwise it then runs
      load_cond_1 and IGNORES its result (an "on load" action; in practice both are
      "return 1" or simple tests). For Csac the loader does not run them at all:
      the spawner loader 0x80060e74 stores pointers to them, and every spawn
      (0x8006125c) first evaluates load_cond_0 as the SPAWN CONDITION (no spawn when
      it returns 0) and, after creating the actor, runs load_cond_1 as an ON-SPAWN
      script. The output labels them "role" accordingly.

SHDR fields (per resource header chunk, offsets from the SHOC chunk start; iff_utils
skips these, so this script walks the IFF itself — see _iter_resources):
  +0x14 u32 enum/type number     +0x18 4cc type (byte-reversed)   +0x1c u32 resource id
  +0x20 u32 payload size         +0x24/+0x28/+0x2c u32 rpns_offsets[3]
  +0x30 u32 load_cond_0 length   +0x34 u32 load_cond_1 length
  +0x38 load_cond_0 bytes, then load_cond_1 bytes, then zero/garbage pad to 4.
  rpns_offsets for resources without scripts point at an empty script (a lone 0x00,
  normally the last byte of RPNS). The RPNS resource itself has 0xffffffff there.

  Actor script slots (actor+148/+152/+156; all call sites confirmed). "context" is
  what opcode 0x05 pushes, "arg" the initial stack value:
    slot 0 (+148) "on_damage"  - run right after the actor loses health: weapon hits and
        splash damage (0x8002aa1c, 0x8002a940), collisions (method 0xb), the player
        (0x80058378); also when something touches a class-14/38 zone (method 0xb,
        0x80031120) or a class-29 actor, which moves the toucher to the position
        stored in the class-29 actor (a teleporter, 0x80030c60). arg = id of the
        attacker / toucher (u16 & 0x7fff; 0x3fff for the player), context = 1 when
        this damage killed the actor (health < 0); 0 for zones and teleporters.
    slot 1 (+152) "on_destroy" - run once when the actor is removed (0x8004d958: called
        on death by the class code, by message 61, by triggers and zones).
        context = 1 for a normal death, 0 when removed by message 61 / a zone with
        flag 1; message 61 with arg 1 skips the script. Aircraft run it from their
        crash code (0x800324e0), the player from respawn (0x80058714).
    slot 2 (+156) "on_detect"  - run when the actor notices something: a shooter
        acquires a target (0x800600e8, arg = target id) or loses it with shooter flag
        0x8000 (0x80060238, arg = 0); about once per second (300 clock units) for the
        nearest actor within 0x22000 of the player that has entity flag 0x100000
        (0x8004df28 / 0x8004e248, arg = -1); a turret captured by a team when it has
        entity flag 0x100000 (0x8002d1a0, arg = capturing actor); class 14/38 zones
        when re-enabled by message 122 (0x80031004); class 32 in a state change
        involving another actor, probably the player (0x800306a0, arg = its id).
        context = 1 if the actor is dead (health < 0) where it is passed.
  Csac resources carry the same three words; the spawned actor gets them through the
  same init (0x8002a5f4), so they are the spawned actor's slots.
  Verified: in all 15 levels every non-empty slot offset is the start of a script, and
  every script in the pool is referenced by at least one resource.

RPNS payload layout
  Just concatenated scripts; no header. Length is padded with 0x00 (each pad byte is
  itself an empty script).

TIME UNITS (confirmed)
  The game clock (0x8009fa60, copied to 0x8009faa4 = *ref[0]) advances by 5 in the
  VSync callback 0x8001eff8, i.e. 300 units per second on NTSC. The per-frame tick
  delta (0x8009f9fc, 0x8002703c) is the clock delta averaged over 4 frames, min 5,
  max 60. Script-visible times are in VSyncs (1/60 s): the t[] bank is stored x5,
  Cfun periods and Csac spawn periods are multiplied by 5 on load. Raw clock units
  (1/300 s) appear in *ref[0] and *ref[2].

SCRIPT VM (verified against the interpreter at 0x80069d68 and its jump table at
0x80011c6c, 62 entries for opcodes 0x01..0x3e)
  Stack of int32, starts as [1] (so an empty script returns 1 = true), plus initial_arg
  when it is >= 0. The script returns the top of stack when it reads a 0x00 opcode.
  Before dispatching, opcodes >= 0x09 pop one value (b = top), >= 0x1d pop a second
  (a = next), >= 0x38 pop a third (x = next). So for "push a; push b; op" the op sees
  a and b in source order. The whole VM is disabled while 0x8009f840 is set.

  Operand / push encodings
    0x80-0xff  push (byte & 0x7f)               0..127
    0x01 n     push ~n  (bitwise not)          -1..-256   (01 00 = -1)
    0x02 n     push n + 128                     128..383
    0x03 hi lo push (hi<<8)|lo                  384..65535 (BE u16)
    0x04 4 bytes push BE u32
    0x05       push context (interpreter arg #2, see the slot descriptions)
    0x06,0x07  no-op
    0x08 r     goto  (address of r) + r          (r is u8, forward only)
    0x14 r     pop b; if b == 0: goto (address of r) + r

  Variable banks (allocated in 0x80076eb0, reset per level in 0x80069b58):
    v[i]    *(0x8009fa94)[i]   61 ints of game state; v[0..56] are zeroed at start-up.
                               Named where the code confirms them (VARIABLE_NAMES):
                               v[3] number of players (1/2), v[6..9] random 0..999
                               drawn at level start, v[10..12] player 1 gun / heavy /
                               special weapon choice, v[13..15] the same for player 2
                               (0x800573a4 copies them into the player; the Cvkh/Cvkb
                               2-16 load conditions test them), v[16]/v[17] mission
                               result flags (0x80025b7c compares them), v[23] game
                               type (1 = Precinct Assault team scoring in the spawner
                               0x8006125c), v[56] last announcer event code (written
                               by game code, read and cleared by PA scripts).
                               Other indices are unnamed.
    *ref[i] *(v[57+i])         4 pointers set by 0x80069b1c:
                               *ref[0] = 0x8009faa4 game clock (1/300 s units)
                               *ref[1] = 0x8009fab4 (unknown)
                               *ref[2] = 0x8009fa70 mission-end countdown: when > 0
                                 the main loop (0x80027d9c) subtracts the tick delta and
                                 ends the mission when it reaches 0 (scripts write
                                 ~1750-1800 = ~6 s after the result flag in v[16])
                               *ref[3] = 0x8009facc pending sound: 0x80049260 plays
                                 sound id (< 130) through 0x80046c84 and clears it
    t[i]    *(0x8009f9a4)[i]   32 timers, stored x5 (load divides by 5, store x5);
                               0x80069d0c subtracts the tick delta every frame and
                               clamps at 0, so t[i] counts down in VSyncs (1/60 s).
                               Initialised to -10 (reads -2 = "never set").
    c[i]    *(0x8009fad4)[i]   100 script-only counters, zeroed per level (no game
                               code writes them).
    g[i]    *(0x8009f9f8)[i&31] 32 random values 0..999 drawn per level; read-only
                               through op 0x0b.

  Opcodes (b = top, a = below it, x = third)
    09 not b            0a sbyte_table[b] (0x800a8540)    0b b<0 ? rand()%-b : g[b&31]
    0c queue_stream(b)  0d play_stream(b)   0e stream_query(b,0)  0f stream_query(b,1)
        -- 0c/0d push b into an 8-entry ring buffer at 0x800a0bc0 that the voice player
           (0x80029d54) resolves through the Ctos table: b is a Ctos entry index.
    10-13 load v/ref/t/c [b]                14 jz (see above)
    15-18 inc  v/ref/t/c [b] (then push b)  19-1c dec  v/ref/t/c [b] (then push b)
    1d-20 store  bank[b] = a                30-33 bank[b] += a (push b)   34-37 -= a
    21 a==b 22 a!=b 23 a>b 24 a>=b 25 a<b 26 a<=b 27 a+b 28 a-b 29 a*b 2a a/b 2b a%b
    2c a&&b 2d a||b     2e if a==0: return b
    2f get_prop(actor a (0 = self), prop b): calls the actor's class handler with
       method 0xc; the base handler 0x8002ab8c returns 0 health, 1/2/3 position
       x/y/z, 4 speed, 5 distance to the nearest player, 7 group_id, 8 team
       (class 5 adds 6); 0 if the actor id does not exist (0x8004de80).
    38 actor_msg(x, a, b)      send message a with arg b to actor id x (0 = self).
    39 group_msg_a(x, a, b)    send to every live actor whose group_id (struct byte
                               0x17 = BaseEntity.group_id) == x       (0x8004dd68)
    3a group_msg_b(x, a, b)    same for team (struct byte 0x18 = BaseEntity.team)
    3b net_node(x, a, b)       Cnet table entry x (load order), node b: a=0 enable
                               (clear flag bit 1), a=1 disable (set bit 1)
    3c spawner_msg(x, a, b)    message to the spawner whose TCAt actor id == x
                               (0x8006158c)
    3d spawner_group_msg(x,a,b) message to spawners whose TCAt byte +0x1d
                               (BaseEntity.group_id) == x       (0x80061600)
    3e prop_msg(x, a, b)       message to class-96 Prop objects (map-cell grid
                               0x8009f938, built by 0x80083a10) whose actor id == x
  Opcodes 0x3f-0x7f pop three values and do nothing.

MESSAGES (actor_msg / group_msg_*; confirmed by decompilation unless marked)
  Delivery (0x8004da84 / 0x8004dc64): actor_msg special-cases three numbers that do
  not go to an actor: 126 = set Cfun function x's count to b (0x8004953c; 0 disables,
  n fires n more times, <0 unlimited), 124 = Trigger (class 95) x (0x80031bec; b=1
  enable and reset, b=0 disable, other values set its +0x15 flag) and 105 (an empty
  function, 0x8003fd6c). Everything else is queued in the target's 4-slot mailbox
  (actor +0x0c, pairs of u8 msg, u8 arg: THE ARG IS TRUNCATED TO 8 BITS), overflowing
  into a 16-entry global queue (0x800a2090) that retries for 600 clock units (2 s).
  Classes 2/3/4/7/15/23 ignore all messages; classes 18 and 21 accept only 60-69 and
  100/101. Each frame 0x8004cecc handles the generic ones; the rest stay queued until
  the class code consumes them. Names are in MESSAGES below; summary:
    generic (any actor, 0x8004cecc):
      56 health = b*100   57 ambient sound = b   58 collision damage = b
      59 collision on (b != 0) / off (entity flag 0x4)
      60 damage: b >= 2 deals b points, b = 0/1 kills (health + 1), via method 0xe
      61 remove silently (sets flag 26; b = 1 also skips on_destroy)
      62 map icon = b      63 invincible on/off (flags 0x80 | 0x1000000)
      64 untargetable on/off (flag 0x20000)   65 health = b
      66 group_id = b      67 team = b        68 UV offset y = b (texture scroll frame)
      69 visible on/off (flag 0x20)   100 start Csfx/actor effect b on this actor
      101 stop effect b on this actor
    class specific (consumer function in parentheses):
      2-7, 10-15  path follower command, mode = msg-1, value = b (0x8004f188; meaning
                  of the modes not traced)
      19 path go (b != 0) / stop (b = 0)   20/21 enable/disable node b of own Cnet
      22 path speed = b * 0x2900 / 300 >> 4 (fx12)
      30 weapon fire on/off (0 = hold fire)  31-35 target mode msg-30 with target b
      38 switch weapon to definition b       (shooters, 0x8005ffb8)
      41-44 turret target mode msg-40 with target b  45 turret on/off
      46 class 30: swap to model slot b (0..3)            (turrets, 0x8002cb20)
      50 play animation track b   51 continue from frame b   52 hold frame b
                                                          (AnmD state, 0x800449f8)
      75 class 35: objective node b>>3 state = b&7   76 node b>>5 icon colour = b&31
      80 class 25 MoveableProp: target position/state = b       (0x8002f280)
      82 class 10 Elevator: 0 stop, 1-10 travel to stop b-1, >= 11 jump to stop b-11
      83 class 10 Elevator: enable (b != 0) / lock (b = 0)        (0x80041728)
      85 class 27: activate (b != 0) / deactivate   86 class 27 +0xe6 = b
      90 class 26: set state b   91 class 26: enable (b != 0) / disable
      95-98 player (class 1) commands (0x800585b8; not identified)
      110 class 31: on/off      115 class 80: +0x11e = b
      120/121 class 14/38 zone parameters  122 class 14/38 zone enable (b != 0; runs
              on_detect when the zone has flag 0x20) / disable (b = 0)
  Spawner messages (0x800614bc; any other number is ignored):
      70 spawner on (b != 0, also resets the countdown so it spawns next tick) / off
      71 spawn one now (only while alive < max and the spawn budget is not used up;
         b = 0 / b != 0 select two queue counters that are drained by 0x800616a0)
  Prop messages (op 0x3e, 0x800841c4; other numbers ignored):
      25 remove now (b = 0) / after b seconds / never (b < 0)
      26 spin stop (b = 0) / spin with period 300/b clock units
      27 hide (b = 0, flag 0x10) / show

Output: parsed/<LEVEL>/rpns.json
  {"level", "rpns": {"id", "size", "scripts": [{"offset", "length", "hex", "asm",
   "pseudo", "refs", "referenced_by"}], "unreferenced_offsets", "leftover_bytes"},
   "resource_scripts": [{"type", "id", "rpns_offsets", "slots"}],
   "load_conditions": [{"type", "id", "cond_sizes", "roles", "cond": [{"hex", "asm",
                         "pseudo"} x2], "unknown_pad"}]}
  roles is ["load_condition", "on_load"], or ["spawn_condition", "on_spawn"] for Csac.
  Pseudo code names known variables as bank[index:name] and known messages as
  number:name (VARIABLE_NAMES, MESSAGES, SPAWNER_MESSAGES, PROP_MESSAGES).
"""

import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.iff_utils import find_game_files



_IFF_BLOCK_SIZE = 0x6000

SLOT_NAMES = ("on_damage", "on_destroy", "on_detect")

# name, pops, pushes_result
# pops is implied by opcode range but stored for clarity; pushes=True means a value is
# pushed (for inc/dec/+=/-= that value is the index, not the variable).
_BANKS = ("v", "*ref", "t", "c")
OPCODES = {
    0x05: ("push_context", 0, True), 0x06: ("nop", 0, False), 0x07: ("nop", 0, False),
    0x09: ("not", 1, True), 0x0a: ("sbyte_table", 1, True), 0x0b: ("rand_or_g", 1, True),
    0x0c: ("queue_stream", 1, False), 0x0d: ("play_stream", 1, False),
    0x0e: ("stream_query0", 1, True), 0x0f: ("stream_query1", 1, True),
    0x21: ("eq", 2, True), 0x22: ("ne", 2, True), 0x23: ("gt", 2, True),
    0x24: ("ge", 2, True), 0x25: ("lt", 2, True), 0x26: ("le", 2, True),
    0x27: ("add", 2, True), 0x28: ("sub", 2, True), 0x29: ("mul", 2, True),
    0x2a: ("div", 2, True), 0x2b: ("mod", 2, True), 0x2c: ("and", 2, True),
    0x2d: ("or", 2, True), 0x2e: ("ret_if_false", 2, False), 0x2f: ("get_prop", 2, True),
    0x38: ("actor_msg", 3, False), 0x39: ("group_msg_a", 3, False),
    0x3a: ("group_msg_b", 3, False), 0x3b: ("net_node", 3, False),
    0x3c: ("spawner_msg", 3, False), 0x3d: ("spawner_group_msg", 3, False),
    0x3e: ("prop_msg", 3, False),
}
for _i, _b in enumerate(_BANKS):
    _n = _b.lstrip("*")
    OPCODES[0x10 + _i] = (f"load_{_n}", 1, True)
    OPCODES[0x15 + _i] = (f"inc_{_n}", 1, True)
    OPCODES[0x19 + _i] = (f"dec_{_n}", 1, True)
    OPCODES[0x1d + _i] = (f"store_{_n}", 2, False)
    OPCODES[0x30 + _i] = (f"addto_{_n}", 2, True)
    OPCODES[0x34 + _i] = (f"subfrom_{_n}", 2, True)

_BINOPS = {0x21: "==", 0x22: "!=", 0x23: ">", 0x24: ">=", 0x25: "<", 0x26: "<=",
           0x27: "+", 0x28: "-", 0x29: "*", 0x2a: "/", 0x2b: "%", 0x2c: "&&", 0x2d: "||"}

MSG_SET_FUN_COUNT = 126
MSG_SPAWN = 0x47

# Message names (see the MESSAGES section of the docstring for the evidence).
# actor_msg / group_msg_a / group_msg_b share one numbering.
MESSAGES = {
    2: "path_mode_1", 3: "path_mode_2", 4: "path_mode_3", 5: "path_mode_4",
    6: "path_mode_5", 7: "path_mode_6", 10: "path_mode_9", 11: "path_mode_10",
    12: "path_mode_11", 13: "path_mode_12", 14: "path_mode_13", 15: "path_mode_14",
    19: "path_go", 20: "path_node_enable", 21: "path_node_disable", 22: "path_speed",
    30: "weapon_fire_enable", 31: "target_mode_1", 32: "target_mode_2",
    33: "target_mode_3", 34: "target_mode_4", 35: "target_mode_5", 38: "set_weapon",
    41: "turret_target_mode_1", 42: "turret_target_mode_2", 43: "turret_target_mode_3",
    44: "turret_target_mode_4", 45: "turret_enable", 46: "turret_set_model",
    50: "anim_play_track", 51: "anim_continue_from", 52: "anim_hold_frame",
    56: "set_health_x100", 57: "set_ambient_sound", 58: "set_collision_damage",
    59: "set_collision", 60: "damage", 61: "remove", 62: "set_map_icon",
    63: "set_invincible", 64: "set_untargetable", 65: "set_health", 66: "set_group",
    67: "set_team", 68: "set_uv_offset", 69: "set_visible",
    75: "objective_node_state", 76: "objective_node_icon", 80: "moveable_prop_goto",
    82: "elevator_goto", 83: "elevator_enable", 85: "class27_activate",
    86: "class27_param", 90: "class26_state", 91: "class26_enable",
    95: "player_cmd_95", 96: "player_cmd_96", 97: "player_cmd_97", 98: "player_cmd_98",
    100: "start_effect", 101: "stop_effect", 105: "nop_105", 110: "class31_enable",
    115: "class80_param", 120: "zone_param_120", 121: "zone_param_121",
    122: "zone_enable", 124: "trigger_enable", 126: "cfun_set_count",
}
SPAWNER_MESSAGES = {70: "spawner_enable", 71: "spawn"}
PROP_MESSAGES = {25: "prop_lifetime", 26: "prop_spin", 27: "prop_visible"}
# What the two inline SHDR scripts do (0x80028bcc; Csac: 0x80060e74 / 0x8006125c).
COND_ROLES = ("load_condition", "on_load")
COND_ROLES_CSAC = ("spawn_condition", "on_spawn")

_MSG_TABLES = {0x38: MESSAGES, 0x39: MESSAGES, 0x3a: MESSAGES,
               0x3c: SPAWNER_MESSAGES, 0x3d: SPAWNER_MESSAGES, 0x3e: PROP_MESSAGES}

# Variables named from the code (see "Variable banks" in the docstring).
VARIABLE_NAMES = {
    ("v", 3): "num_players", ("v", 6): "level_random_0", ("v", 7): "level_random_1",
    ("v", 8): "level_random_2", ("v", 9): "level_random_3",
    ("v", 10): "p1_gun", ("v", 11): "p1_heavy", ("v", 12): "p1_special",
    ("v", 13): "p2_gun", ("v", 14): "p2_heavy", ("v", 15): "p2_special",
    ("v", 16): "result_1", ("v", 17): "result_2", ("v", 23): "game_type",
    ("v", 56): "announcer_event",
    ("*ref", 0): "clock", ("*ref", 2): "mission_end_countdown", ("*ref", 3): "play_sound",
}


def _var(bank, idx_txt, idx_val):
    """Render bank[index] with the variable's name when it is known."""
    name = VARIABLE_NAMES.get((bank, idx_val)) if idx_val is not None else None
    return f"{bank}[{idx_txt}:{name}]" if name else f"{bank}[{idx_txt}]"


def message_name(op, msg):
    """Name of message `msg` for messaging opcode `op` (None when unknown)."""
    table = _MSG_TABLES.get(op)
    return table.get(msg) if table is not None and msg is not None else None


def _pops_for(op):
    if op >= 0x38:
        return 3
    if op >= 0x1d:
        return 2
    if op >= 0x09:
        return 1
    return 0


# ── tokenizer ──────────────────────────────────────────────────────────────

def tokenize(code, start=0, end=None, stop_at_end=True):
    """Yield instruction dicts {off, size, kind, op, value} for code[start:end].

    kind is 'push', 'end', 'jmp', 'jz' or 'op'. Offsets are relative to `code`.
    With stop_at_end the walk stops after the first 0x00 opcode.
    """
    end = len(code) if end is None else end
    i = start
    while i < end:
        b = code[i]
        if b >= 0x80:
            yield {"off": i, "size": 1, "kind": "push", "op": b, "value": b & 0x7f}
            i += 1
        elif b == 0x00:
            yield {"off": i, "size": 1, "kind": "end", "op": 0, "value": None}
            i += 1
            if stop_at_end:
                return
        elif b in (0x01, 0x02):
            n = code[i + 1] if i + 1 < end else 0
            val = (~n) if b == 0x01 else n + 128
            yield {"off": i, "size": 2, "kind": "push", "op": b, "value": val}
            i += 2
        elif b == 0x03:
            val = (code[i + 1] << 8 | code[i + 2]) if i + 2 < end else 0
            yield {"off": i, "size": 3, "kind": "push", "op": b, "value": val}
            i += 3
        elif b == 0x04:
            val = struct.unpack_from(">i", code, i + 1)[0] if i + 4 < end else 0
            yield {"off": i, "size": 5, "kind": "push", "op": b, "value": val}
            i += 5
        elif b in (0x08, 0x14):
            r = code[i + 1] if i + 1 < end else 0
            yield {"off": i, "size": 2, "kind": "jmp" if b == 0x08 else "jz", "op": b,
                   "value": i + 1 + r}
            i += 2
        else:
            yield {"off": i, "size": 1, "kind": "op", "op": b, "value": None}
            i += 1


def script_length(code, start):
    """Length in bytes of the script starting at `start` (through its first 0x00)."""
    last = start
    for ins in tokenize(code, start):
        last = ins["off"] + ins["size"]
    return last - start


# ── reference resolution ───────────────────────────────────────────────────

def _resolve(op, args, ctx, refs, at):
    """Append reference dicts for literal args of `op`; return a comment string."""
    if ctx is None:
        return ""
    lits = [a[1] for a in args]
    notes = []

    def add(kind, rid, exists, extra=None):
        r = {"at": at, "kind": kind, "id": rid, "exists": exists}
        if extra:
            r.update(extra)
        refs.append(r)

    if op in (0x0c, 0x0d, 0x0e, 0x0f) and lits[0] is not None:
        tos = ctx.get("tos", [])
        idx = lits[0]
        name = tos[idx] if 0 <= idx < len(tos) else None
        add("ctos_stream", idx, name is not None, {"stream": name})
        notes.append(f"stream {name}" if name else f"Ctos[{idx}] missing")
    elif op in (0x38, 0x3c) and lits[0] is not None:
        actors = ctx.get("actors", {})
        tgt, msg = lits[0], lits[1]
        if op == 0x38 and msg == MSG_SET_FUN_COUNT:
            nfun = ctx.get("fun_count", 0)
            add("cfun_function", tgt, 0 <= tgt < nfun)
            notes.append(f"Cfun function {tgt} count = {lits[2]}")
        elif op == 0x38 and tgt == 0:
            notes.append("self")
        else:
            a = actors.get(tgt)
            extra = {"actor_type": a} if a else {}
            if message_name(op, msg):
                extra["message"] = message_name(op, msg)
            add("actor", tgt, a is not None, extra or None)
            label = f"{a} {tgt}" if a else f"actor {tgt} missing"
            if op == 0x3c and msg == MSG_SPAWN:
                label += " (spawn)"
            notes.append(label)
        if (op == 0x38 and msg not in (MSG_SET_FUN_COUNT, 124, 105)
                and lits[2] is not None and not 0 <= lits[2] <= 255):
            notes.append(f"arg {lits[2]} is stored as u8 {lits[2] & 0xff} in the mailbox")
    elif op == 0x3e and lits[0] is not None:
        a = ctx.get("actors", {}).get(lits[0])
        add("actor", lits[0], a is not None, {"actor_type": a} if a else None)
        notes.append(f"{a} {lits[0]}" if a else f"actor {lits[0]} missing")
    elif op == 0x2f and lits[0] not in (None, 0):
        a = ctx.get("actors", {}).get(lits[0])
        add("actor", lits[0], a is not None, {"actor_type": a} if a else None)
        notes.append(f"{a} {lits[0]}" if a else f"actor {lits[0]} missing")
    elif op == 0x3b and lits[0] is not None:
        nets = ctx.get("nets", [])
        idx = lits[0]
        nid = nets[idx] if 0 <= idx < len(nets) else None
        add("cnet", nid if nid is not None else idx, nid is not None,
            {"net_index": idx, "node": lits[2]})
        notes.append(f"Cnet {nid} node {lits[2]}" if nid is not None
                     else f"net index {idx} missing")
    return "; ".join(notes)


# ── disassembly / decompilation ────────────────────────────────────────────

def _fold(op, a, b):
    """Constant-fold a binary op on two literals (C int32 semantics); None if not foldable."""
    if a is None or b is None:
        return None
    if op == 0x27:
        return a + b
    if op == 0x28:
        return a - b
    if op == 0x29:
        return a * b
    if op in (0x2a, 0x2b) and b != 0:
        q = abs(a) // abs(b) * (1 if (a < 0) == (b < 0) else -1)
        return q if op == 0x2a else a - q * b
    return None


def _fmt_operand(ins):
    if ins["kind"] == "push":
        return str(ins["value"])
    if ins["kind"] in ("jmp", "jz"):
        return f"L_{ins['value']:04x}"
    return ""


def disassemble(code, start=0, end=None, ctx=None, has_arg=False, stop_at_end=True):
    """Return (asm_lines, pseudo_lines, refs) for the script at code[start:end].

    asm lines: "OFFS  HEX  mnemonic operand"; offsets are relative to `code`.
    pseudo lines: a stack-tracking decompilation (labels L_xxxx are code offsets).
    The decompiler follows fall-through order only: at a label it keeps the stack of
    the preceding instruction, which is right for the compiler's if/else output.
    """
    insns = list(tokenize(code, start, end, stop_at_end))
    targets = {i["value"] for i in insns if i["kind"] in ("jmp", "jz")}
    asm, pseudo, refs = [], [], []
    stack = [("1", 1)] + ([("arg", None)] if has_arg else [])

    def pop():
        return stack.pop() if stack else ("<empty>", None)

    for ins in insns:
        off, op = ins["off"], ins["op"]
        hexb = code[off:off + ins["size"]].hex(" ")
        if off in targets:
            pseudo.append(f"L_{off:04x}:")
        if ins["kind"] == "push":
            name = "push"
        elif ins["kind"] == "end":
            name = "end"
        elif ins["kind"] == "jmp":
            name = "jmp"
        elif ins["kind"] == "jz":
            name = "jz"
        else:
            name = OPCODES.get(op, (f"op_{op:02x}",))[0]
        asm.append(f"{off:04x}  {hexb:<14} {name} {_fmt_operand(ins)}".rstrip())

        kind = ins["kind"]
        if kind == "push":
            stack.append((str(ins["value"]), ins["value"]))
            continue
        if kind == "end":
            pseudo.append(f"return {stack[-1][0] if stack else '<empty>'}")
            continue
        if kind == "jmp":
            pseudo.append(f"goto L_{ins['value']:04x}")
            continue
        if kind == "jz":
            cond = pop()[0]
            pseudo.append(f"if not {cond}: goto L_{ins['value']:04x}")
            continue

        n = _pops_for(op)
        popped = [pop() for _ in range(n)]
        args = list(reversed(popped))  # source order: x, a, b
        note = _resolve(op, args, ctx, refs, off)
        suffix = f"  # {note}" if note else ""
        txt = [a[0] for a in args]

        if op == 0x05:
            stack.append(("context", None))
        elif op in (0x06, 0x07) or 0x3f <= op <= 0x7f:
            if op >= 0x3f:
                pseudo.append(f"op_{op:02x}({', '.join(txt)})  # no-op in the interpreter")
        elif op == 0x09:
            stack.append((f"!{txt[0]}", None))
        elif op == 0x0a:
            stack.append((f"sbyte_table[{txt[0]}]", None))
        elif op == 0x0b:
            lit = args[0][1]
            if lit is not None and lit < 0:
                stack.append((f"rand({-lit})", None))
            elif lit is not None:
                stack.append((f"g[{lit & 31}]", None))
            else:
                stack.append((f"rand_or_g({txt[0]})", None))
        elif op in (0x0c, 0x0d):
            pseudo.append(f"{OPCODES[op][0]}({txt[0]}){suffix}")
        elif op in (0x0e, 0x0f):
            stack.append((f"{OPCODES[op][0]}({txt[0]})", None))
            if note:
                pseudo.append(f"# {note}")
        elif 0x10 <= op <= 0x13:
            stack.append((_var(_BANKS[op - 0x10], txt[0], args[0][1]), None))
        elif 0x15 <= op <= 0x1c:
            bank = _BANKS[(op - 0x15) % 4]
            pseudo.append(f"{_var(bank, txt[0], args[0][1])} {'+' if op < 0x19 else '-'}= 1")
            stack.append(args[0])
        elif 0x1d <= op <= 0x20:
            pseudo.append(f"{_var(_BANKS[op - 0x1d], txt[1], args[1][1])} = {txt[0]}")
        elif 0x30 <= op <= 0x37:
            bank = _BANKS[(op - 0x30) % 4]
            pseudo.append(f"{_var(bank, txt[1], args[1][1])} {'+' if op < 0x34 else '-'}"
                          f"= {txt[0]}")
            stack.append(args[1])
        elif op in _BINOPS:
            folded = _fold(op, args[0][1], args[1][1])
            if folded is not None:
                stack.append((str(folded), folded))
            else:
                stack.append((f"({txt[0]} {_BINOPS[op]} {txt[1]})", None))
        elif op == 0x2e:
            pseudo.append(f"if not {txt[0]}: return {txt[1]}")
        elif op == 0x2f:
            who = "self" if args[0][1] == 0 else txt[0]
            stack.append((f"get_prop({who}, {txt[1]})", None))
            if note:
                pseudo.append(f"# {note}")
        elif op in _MSG_TABLES:
            mname = message_name(op, args[1][1])
            mtxt = f"{txt[1]}:{mname}" if mname else txt[1]
            pseudo.append(f"{OPCODES[op][0]}({txt[0]}, {mtxt}, {txt[2]}){suffix}")
        elif op in OPCODES:
            pseudo.append(f"{OPCODES[op][0]}({', '.join(txt)}){suffix}")
        else:
            pseudo.append(f"op_{op:02x}({', '.join(txt)})")
    return asm, pseudo, refs


def script_record(code, start, ctx=None, has_arg=False, end=None, stop_at_end=True):
    """Return a JSON-ready dict describing the script at `start`."""
    asm, pseudo, refs = disassemble(code, start, end, ctx, has_arg, stop_at_end)
    length = (end - start) if end is not None and not stop_at_end else script_length(code, start)
    rec = {"offset": start, "length": length,
           "hex": code[start:start + length].hex(), "asm": asm, "pseudo": pseudo}
    if refs:
        rec["refs"] = refs
    return rec


# ── IFF walking (SHDR-aware; iff_utils merges same-id resources and drops SHDR) ──

def _iter_resources(data):
    """Yield dicts {type, id, size, rpns_offsets, cond_sizes, cond_bytes, payload}."""
    def ru32(off):
        return struct.unpack_from("<I", data, off)[0]

    cur = None
    pos = 0x18
    while pos < len(data):
        if data[pos:pos + 4] == b"LLIF":
            pos = ((pos // _IFF_BLOCK_SIZE) + 1) * _IFF_BLOCK_SIZE
            continue
        if pos + 20 > len(data):
            break
        size = ru32(pos + 4)
        if size < 8 or size > _IFF_BLOCK_SIZE:
            break
        sub = data[pos + 16:pos + 20]
        if sub == b"RDHS":
            if cur:
                cur["payload"] = b"".join(cur.pop("_parts"))
                yield cur
            cur = {"type": data[pos + 24:pos + 28][::-1].decode("ascii", errors="replace"),
                   "id": ru32(pos + 28), "size": ru32(pos + 32),
                   "rpns_offsets": [ru32(pos + 36), ru32(pos + 40), ru32(pos + 44)],
                   "cond_sizes": [ru32(pos + 48), ru32(pos + 52)],
                   "cond_bytes": data[pos + 56:pos + size], "_parts": []}
        elif sub == b"TADS" and cur:
            cur["_parts"].append(data[pos + 20:pos + size])
        pos += size
    if cur:
        cur["payload"] = b"".join(cur.pop("_parts"))
        yield cur


def _swvr_names(data, tos_offsets):
    """Map Ctos offsets to SWVR stream names (offset is relative to the first SWVR)."""
    base = struct.unpack_from("<I", data, 0x14)[0]
    names = []
    for off in tos_offsets:
        p = base + off
        name = None
        if data[p:p + 4] == b"RVWS":
            raw = data[p + 16:p + 36].split(b"\x00")[0]
            if raw.startswith(b"ELIF"):
                name = raw[4:].decode("ascii", errors="replace")
                for n in range(len(".stream"), 0, -1):
                    if name.endswith(".stream"[:n]):
                        name = name[:-n]
                        break
        names.append(name)
    return names


def build_context(data, resources=None):
    """Return the reference-resolution context for a level (actors, Ctos, Cnet, Cfun)."""
    resources = resources if resources is not None else list(_iter_resources(data))
    ctx = {"actors": {}, "tos": [], "nets": [], "fun_count": 0}
    for r in resources:
        t, p = r["type"], r["payload"]
        if t in ("Cact", "Csac"):
            ctx["actors"][r["id"]] = t
        elif t == "Cnet":
            ctx["nets"].append(r["id"])
        elif t == "Ctos" and len(p) >= 16:
            n = struct.unpack_from("<I", p, 12)[0]
            offs = [struct.unpack_from("<I", p, 16 + 12 * k)[0] for k in range(n)
                    if 16 + 12 * k + 4 <= len(p)]
            ctx["tos"] = _swvr_names(data, offs)
        elif t == "Cfun" and len(p) >= 12:
            size = struct.unpack_from("<I", p, 4)[0]
            ctx["fun_count"] = max(0, (size - 12) // 20)
    return ctx


# ── per-level extraction ───────────────────────────────────────────────────

def _split_cond(r):
    """Split the inline SHDR load-condition bytes into (cond0, cond1, pad)."""
    s0, s1 = r["cond_sizes"]
    b = r["cond_bytes"]
    return b[:s0], b[s0:s0 + s1], b[s0 + s1:]


def extract_rpns(mis_path, out_dir):
    with open(mis_path, "rb") as f:
        data = f.read()
    resources = list(_iter_resources(data))
    ctx = build_context(data, resources)
    rpns = next((r for r in resources if r["type"] == "RPNS"), None)
    if rpns is None:
        return 0
    pool = rpns["payload"]

    # every SHDR reference into the pool
    referenced = {}
    resource_scripts = []
    for r in resources:
        if r["type"] == "RPNS":
            continue
        slots = []
        for k, off in enumerate(r["rpns_offsets"]):
            empty = off < len(pool) and pool[off] == 0
            slots.append({"slot": k, "name": SLOT_NAMES[k], "offset": off,
                          "in_range": off < len(pool), "empty": empty})
            if off < len(pool) and not empty:
                referenced.setdefault(off, []).append(
                    {"type": r["type"], "id": r["id"], "slot": k, "name": SLOT_NAMES[k]})
        if any(not s["empty"] for s in slots):
            resource_scripts.append({"type": r["type"], "id": r["id"],
                                     "rpns_offsets": r["rpns_offsets"], "slots": slots})

    # linear split of the pool into scripts at 0x00 opcodes
    scripts = []
    starts = set()
    pos = 0
    while pos < len(pool):
        n = script_length(pool, pos)
        starts.add(pos)
        if n > 1:
            refs_in = referenced.get(pos, [])
            has_arg = any(x["slot"] == 0 for x in refs_in)
            rec = script_record(pool, pos, ctx, has_arg=has_arg)
            rec["referenced_by"] = refs_in
            scripts.append(rec)
        pos += max(n, 1)
    covered = sum(s["length"] for s in scripts) + sum(
        1 for p in starts if pool[p] == 0)

    load_conditions = []
    for r in resources:
        c0, c1, pad = _split_cond(r)
        if r["type"] == "RPNS" or (c0 in (b"", b"\x00") and c1 in (b"", b"\x00")):
            continue
        load_conditions.append({
            "type": r["type"], "id": r["id"], "cond_sizes": r["cond_sizes"],
            "roles": list(COND_ROLES_CSAC if r["type"] == "Csac" else COND_ROLES),
            "cond": [script_record(c, 0, ctx, end=len(c), stop_at_end=False) for c in (c0, c1)],
            "unknown_pad": pad.hex()})

    out = {"level": os.path.splitext(os.path.basename(mis_path))[0],
           "rpns": {"id": rpns["id"], "size": len(pool), "header_size": rpns["size"],
                    "script_count": len(scripts), "scripts": scripts,
                    "unreferenced_offsets": sorted(set(referenced) - starts),
                    "leftover_bytes": len(pool) - covered},
           "resource_scripts": resource_scripts,
           "load_conditions": load_conditions}
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "rpns.json"), "w") as f:
        json.dump(out, f, indent=1)
    return len(scripts)


# ── main ───────────────────────────────────────────────────────────────────

def main():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mis_files = find_game_files("MIS")
    for mis_path in mis_files:
        level = os.path.splitext(os.path.basename(mis_path))[0]
        n = extract_rpns(mis_path, os.path.join(repo_root, "parsed", level))
        print(f"{level}.MIS → {n} RPNS scripts")


if __name__ == "__main__":
    main()
