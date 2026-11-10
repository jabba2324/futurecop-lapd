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
    v[i]    *(0x8009fa94)[i]   61 ints of game state; v[0..56] are zeroed at start-up
                               (0x80076eb0). v[] is the game's global state block, so
                               most entries are written and read by game code; the
                               ones scripts use are all named (VARIABLE_NAMES). The
                               PA entries v[25..56] live in the Precinct Assault
                               HUD 0x80047f30, the PA stats getter 0x80059e9c (index
                               = player 0/1, or the Sky Captain AI for "0"), the Sky
                               Captain AI 0x800336dc / its init 0x800331cc and the
                               turret capture/kill code 0x8002d1a0 / 0x8002d664.
                               [C] = confirmed in code, [P] = plausible reading.
      v[2]  p1_difficulty      [C] per-player skill byte (v[38] = player 2), kept
                               across levels by main 0x80076a2c (starts at 1), loaded
                               from .DEM byte +1 (0x80059bfc). Player max health
                               x1.00 / x1.25 / x1.50 for 0 / 1 / 2, 3 = invulnerable
                               (0x800579d4; incoming damage scaled the same way in
                               0x80052f54; ammo/weapon stats +25 % / +50 % in
                               0x8006ab38, 0x8006bd48). [P] 2 is the easiest
                               setting: CW scripts add enemies when v[2] < 2 / != 2.
      v[3]  num_players        [C] 1 or 2.
      v[4]  level_exit_code    [C] written by the game, never by scripts: 4 while
                               loading (0x80024e3c), 1 in play (0x800259d8), on exit
                               (0x80025b7c) by game state: mission-end countdown
                               expired -> 0; match/cheat end states 0xb/0xc -> 0 when
                               v[16] == v[17], else 1; others 2 / 3 / 4. Main
                               0x80076a2c: 0 = go on to the next level / movie, 3 =
                               back to the front end.
      v[6..9] level_random_0..3 [C] rand() % 1000 drawn per level (0x80069b58).
      v[10..12] p1_gun / p1_heavy / p1_special, v[13..15] the same for player 2
                               [C] weapon choice (gun 0-5, heavy 16-21, special
                               32-36). 0x800573a4 copies them into the player, the
                               Cvkh/Cvkb load conditions test them, .DEM bytes
                               +2..+7 set them (0x80059bfc).
      v[16] mission_result, v[17] mission_result_ref  [C] compared by the game:
                               end-of-match screen 0x80028164 and exit 0x80025b7c
                               test v[16] == v[17]. CW scripts set v[16] = 0,
                               v[17] = 1 at start and v[16] = 1 on success; in PA
                               v[16] == v[17] means player 1 LOST (the "win" cheat
                               0x80060a0c sets v[16] = v[17] + 1 for player 1).
      v[19] pa_ai_level        [C] Sky Captain level 0..9 (HUD "L%02d" = v[19]+1),
                               clamped in 0x800331cc; it scales the AI's health,
                               speed and buying; +1 after a won 1-player PA match
                               (0x80028164); 10 = PA campaign finished (main
                               0x80076a2c). Front end / demo table set it (0x80078c88).
      v[23] game_type          [C] 0 = Crime War, 1 = Precinct Assault (team
                               scoring in the spawner 0x8006125c, PA HUD, ...).
      v[25]/v[26] p1_points / p2_points   [C] team 1 / team 2 (P2 or Sky Captain)
                               current points: HUD 0x80047f30, stats item 13; +1 per
                               turret captured/destroyed (0x8002d1a0/0x8002d664);
                               scripts add/spend them; the AI spends v[26].
      v[27]/v[28] p1_tanks / p1_jets, v[29]/v[30] p2_tanks / p2_jets   [C] unit
                               counts shown by the HUD as "%02ld/%02ld"; the AI caps
                               its own at 20 (0x800336dc). Tank = class 5, jet =
                               class 9 (the Csac the scripts spawn for them).
      v[31]/v[32] p1_points_scored / p2_points_scored   [C] stats item 14.
      v[33]/v[34] p1_outposts / p2_outposts   [C] stats item 12 ("Outposts
                               Claimed"); the HUD blinks the points when >= 30 and
                               v[33]+v[34] < 4 (an outpost is still free).
      v[35]/v[36]/v[37] secret_un / secret_ov / secret_m3a  [C] persistent flags:
                               saved in the password (0x8007f02c / 0x8007ea78) and
                               the memory-card save (0x80080b70). Set only by the
                               on_detect script of one hidden actor in UN, OV and
                               M3A respectively. [P] secret areas found.
      v[38] p2_difficulty      [C] as v[2] for player 2.
      v[39] sky_captain_mode   [C] mirror of the AI's state (0x800336dc), chosen
                               each think from two points the init 0x800331cc
                               stores: the AI's start (its base) and player 1's
                               start (the player base). 2 = defend base (also
                               the start mode): an enemy tank (class 5) within
                               8.0 or dreadnought (class 28) within 9.0 of its
                               base, or fewer than 10 of its class-8 turrets
                               within 32.0; it attacks those and drops to 0 when
                               neither holds. 1 = support its own attack: its
                               tanks within 6.5 / dreadnoughts within 7.5 of the
                               player base score > 1 (tank 1, dreadnought 10);
                               it flies to the player base. 0 = hunt: patrols
                               the midpoint between the bases and goes after
                               the player and their units (0x800330e8 sets the
                               waypoint per mode).
      v[40..43] outpost_owner_0..3  [C] team owning outpost k (0 = free); the AI
                               claims a free one for 30 points (writes its team,
                               2), a player's claim script writes 1 and takes 30
                               from v[25]. [P] Scripts also store 3 / 4 after
                               reacting to a claim (handled markers).
      v[44]/v[45] ai_buy_tanks / ai_buy_jets   [C] units the AI bought at its base
                               (1 point each); v[46] ai_buy_dreadnought / v[47]
                               ai_buy_superfortress (50 points each); v[48+2k] /
                               v[49+2k] ai_buy_tanks_outpost_k / ai_buy_jets_outpost_k
                               (k = 0..3, 2 points each). Written by the AI, read,
                               turned into spawns and cleared by PA Cfun scripts.
      v[56] announcer_event    [C] written by game code, read and cleared by PA
                               scripts (which queue Sky Captain voice streams):
                               1 it heads for the player base (mode 1,
                               0x800330e8), 2 / 3 it goes after the player
                               (0x800336dc), 4 it repaired itself (at its base
                               or a class-16 pickup with item flag 0x20000,
                               0x8003526c), 5 player 1 hit or touched it
                               (messages 9 / 0xb / 0xe in its handler
                               0x800357c0), 6 it captured a turret (0x8002d1a0),
                               7 it destroyed a turret, 8 a player-held turret
                               was destroyed (both 0x8002d664, message 5), 9 it
                               respawned (0x80035698). 1-player PA only.
      Not written or read by any script in the 15 levels: v[0], v[1], v[5], v[18],
      v[20..22], v[24] (cleared on exit by 0x80025b7c), v[57..60] (= the *ref
      pointers). No code access was found for v[0], v[1], v[5], v[18], v[20..22].
    *ref[i] *(v[57+i])         4 pointers set by 0x80069b1c:
                               *ref[0] = 0x8009faa4 game clock (1/300 s units)
                               *ref[1] = 0x8009fab4 unused: no instruction in
                                 SLUS_007.39 reads or writes it (searched absolute
                                 lui/lo16 and gp-relative (gp = 0x8009f214) accesses;
                                 the only hit is the pointer setup) and no script
                                 uses it. A free variable.
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
      TARGET KINDS. One target search 0x8004e80c serves path followers, shooters,
      turrets and zones (zone filter test 0x8002f8b8): it returns the nearest live
      actor (not self, not dead, within the caller's range) whose
        kind 1 class id (struct byte 0x16) == b    kind 2 actor id (u16 0x14) == b
        kind 3 group_id (byte 0x17) == b           kind 4 team (byte 0x18) == b
        kind 5 team is a set bit of mask b (1 << team & b)
        (kind 7 = actor-id flag 0x4000 and kind 6 = nothing exist, but no message
        selects them; zones also have kind 8 = class with class 18 counted as 1)
      e.g. "class == 1" = the players. b is a u8 (mailbox), so actor ids > 255 can
      not be targeted by message.
      1-7, 10-15  path follower (classes 5/20/26/28/31; 0x8004f188 stores mode =
                  msg-1 in path +0x3c bits 24-27 and b in path +0x42; the node
                  chooser 0x8004f330 uses them at every node) [C]:
                  1  path_random            mode 0: pick a random next node (default)
                  2-6 path_seek_<kind>      modes 1-5: prefer the neighbour node that
                     (class/actor/group/team/team_mask)  points most towards the
                     nearest actor of target kind 1-5 with value b (search range =
                     path +0x2e; if path +0x3a > 0 only with probability
                     +0x3a/4096 per node, else random)
                  7  path_goto_node         mode 6: head towards Cnet node b
                  10-14 path_flee_<kind>    modes 9-13: the same but prefer the
                     node pointing AWAY from the target
                  15 path_avoid_node        mode 14: head away from node b
                  (8, 9 and >= 16 are not accepted: modes 7/8/15 do not exist.
                  Class 26 walkers switch to the flee modes themselves when hurt.)
      19 path go (b != 0) / stop (b = 0)   20/21 enable/disable node b of own Cnet
      22 path speed = b * 0x2900 / 300 >> 4 (fx12)
      30 weapon fire on (b != 0) / hold fire (b = 0; BaseShooter +4 flag 0x100)
      31-35 shooter target = target kind msg-30 (class/actor/group/team/team_mask)
            with value b; the shooter's periodic search 0x800600e8 uses it
      38 switch weapon to the class-98 weapon definition whose actor id (low byte)
         == b (table 0x800a2300, 0x8005ff70)  (shooters, 0x8005ffb8) [C]
      41-44 turret target = target kind msg-40 (class/actor/group/team) with value b
            (searched every 0x5dc clock units in 0x8002cc30)
      45 turret on (b != 0) / off (b = 0: +0xdf flag 4 skips all turret logic)
      46 class 30: swap to model slot b (0..3)            (turrets, 0x8002cb20) [C]
      50 play animation track b   51 continue from frame b   52 hold frame b
                                                          (AnmD state, 0x800449f8)
      75 class 35: objective node b>>3 state = b&7   76 node b>>5 icon colour = b&31
      80 class 25 MoveableProp: move to the end position/rotation (b != 0) or back
         to the start (b = 0) (+0xa1, read by the mover 0x8002f30c; with the
         ping-pong flag it then toggles by itself)                (0x8002f280) [C]
      82 class 10 Elevator: 0 stop, 1-10 travel to stop b-1, >= 11 jump to stop b-11
      83 class 10 Elevator: enable (b != 0) / lock (b = 0)        (0x80041728)
      85 class 27 MovingShooter: b != 0 = move out to pos+offset (state 1; only
         from the home pause 0 or while returning 3), b = 0 = return home (state
         3; only while out 1/2)                            (0x8002e528 -> 0x8002e2bc) [C]
      86 class 27: animation speed while paused = b/16 (+0xe6; 0 = normal) [C]
      90 class 26 PathedWalker: set behaviour state b (+0x15f, 0x8002b1dc) [C]:
         0 wander (quarter speed unless walker flag 1; attacks targets it meets),
         1 attack (needs a target: ignored when sent by message), 2 flee (also
         entered automatically below 5 health), 3 walk the path ignoring targets,
         4 guard: stops and shoots at targets in range like 0/1, but never
         flees or retreats, keeps its target when shot (0x8002bfb4) and stays
         in state 4 when the target is gone (0x8002b784), 5 halt (speed 0) and shoot,
         6 run to the nearest Cnet node with flag bit 0 (retreat), 7 die (death
         animation, sets dead flag), 8 dead.
      91 class 26: b = 0 sets walker flags 0xfe (incl. 0x80 = never attack, 0x02 =
         never flee) and full speed; b != 0 clears flag 1 (slow wander again;
         bits 1-7 stay set)                                        (0x8002b4b4) [C]
      95-98 player (class 1; 0x800585b8, player data = actor +0xac) [C]:
         95 clear the player's lock-on target (+0x6c, the aim target of the weapon
            mount 0x80057e40)
         96 camera shake += b*128 (+0xd0, decays by the tick delta; damage adds to
            it too, 0x80057358; camera 0x800569ec)
         97 set camera view b (+0xa4) and show its name for 3 s: 0 Standard,
            1 Close-Up, 2 Standard Side, 3 Close-Up Side, 4 Sky, 5 Crowd Control,
            6 Crowd Control Side (5/6 fall back to 0 outside crowd-control
            regions, 0x80031b40) (names: table 0x8009ac58)
         98 set vehicle mode b: 0 walker, 1 hovercraft (+0x2fd, ignored while a
            transformation runs; the transform code 0x80055988 does the rest)
      110 class 31 (pusher field: shoves nearby players, 0x8003a160): force on
          (b != 0) / off (b = 0) (+0x140 bit 0; also resets the push) (0x8003a09c) [C]
      115 class 80 (no instances in any level): +0x11e = b (0x80039bd0)
      120 class 14/38 zone: filter value = b (+0xa2)  121 filter type = b (+0xa1;
          the target kinds above, tested by 0x8002f8b8)          (0x80031004) [C]
      122 class 14/38 zone enable (b != 0; runs on_detect when the zone has flag
          0x20) / disable (b = 0)
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
  number:name (VARIABLE_NAMES, MESSAGES, SPAWNER_MESSAGES, PROP_MESSAGES). For
  actor/group messages with a literal argument it adds the argument's meaning as a
  comment (target kind, Cnet node, camera view, walker state, ...; _msg_arg_note).
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
_KINDS = ("class", "actor", "group", "team", "team_mask")
MESSAGES = {
    1: "path_random",
    **{2 + k: f"path_seek_{n}" for k, n in enumerate(_KINDS)},
    7: "path_goto_node",
    **{10 + k: f"path_flee_{n}" for k, n in enumerate(_KINDS)},
    15: "path_avoid_node",
    19: "path_go", 20: "path_node_enable", 21: "path_node_disable", 22: "path_speed",
    30: "weapon_fire_enable",
    **{31 + k: f"target_{n}" for k, n in enumerate(_KINDS)},
    38: "set_weapon",
    **{41 + k: f"turret_target_{n}" for k, n in enumerate(_KINDS[:4])},
    45: "turret_enable", 46: "turret_set_model",
    50: "anim_play_track", 51: "anim_continue_from", 52: "anim_hold_frame",
    56: "set_health_x100", 57: "set_ambient_sound", 58: "set_collision_damage",
    59: "set_collision", 60: "damage", 61: "remove", 62: "set_map_icon",
    63: "set_invincible", 64: "set_untargetable", 65: "set_health", 66: "set_group",
    67: "set_team", 68: "set_uv_offset", 69: "set_visible",
    75: "objective_node_state", 76: "objective_node_icon", 80: "moveable_prop_goto",
    82: "elevator_goto", 83: "elevator_enable", 85: "mover_extend",
    86: "mover_pause_anim_speed", 90: "walker_set_behaviour", 91: "walker_passive",
    95: "player_clear_target", 96: "player_camera_shake", 97: "player_set_camera",
    98: "player_set_vehicle_mode",
    100: "start_effect", 101: "stop_effect", 105: "nop_105", 110: "pusher_enable",
    115: "class80_param", 120: "zone_filter_value", 121: "zone_filter_type",
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
    ("v", 2): "p1_difficulty", ("v", 3): "num_players", ("v", 4): "level_exit_code",
    ("v", 6): "level_random_0", ("v", 7): "level_random_1",
    ("v", 8): "level_random_2", ("v", 9): "level_random_3",
    ("v", 10): "p1_gun", ("v", 11): "p1_heavy", ("v", 12): "p1_special",
    ("v", 13): "p2_gun", ("v", 14): "p2_heavy", ("v", 15): "p2_special",
    ("v", 16): "mission_result", ("v", 17): "mission_result_ref",
    ("v", 19): "pa_ai_level", ("v", 23): "game_type",
    ("v", 25): "p1_points", ("v", 26): "p2_points",
    ("v", 27): "p1_tanks", ("v", 28): "p1_jets", ("v", 29): "p2_tanks", ("v", 30): "p2_jets",
    ("v", 31): "p1_points_scored", ("v", 32): "p2_points_scored",
    ("v", 33): "p1_outposts", ("v", 34): "p2_outposts",
    ("v", 35): "secret_un", ("v", 36): "secret_ov", ("v", 37): "secret_m3a",
    ("v", 38): "p2_difficulty", ("v", 39): "sky_captain_mode",
    ("v", 40): "outpost_owner_0", ("v", 41): "outpost_owner_1",
    ("v", 42): "outpost_owner_2", ("v", 43): "outpost_owner_3",
    ("v", 44): "ai_buy_tanks", ("v", 45): "ai_buy_jets",
    ("v", 46): "ai_buy_dreadnought", ("v", 47): "ai_buy_superfortress",
    **{("v", 48 + 2 * k): f"ai_buy_tanks_outpost_{k}" for k in range(4)},
    **{("v", 49 + 2 * k): f"ai_buy_jets_outpost_{k}" for k in range(4)},
    ("v", 56): "announcer_event",
    ("*ref", 0): "clock", ("*ref", 1): "unused", ("*ref", 2): "mission_end_countdown",
    ("*ref", 3): "play_sound",
}

# Readable meaning of literal message arguments (pseudo-code comments only).
_CAMERAS = ("Standard", "Close-Up", "Standard Side", "Close-Up Side", "Sky",
            "Crowd Control", "Crowd Control Side")
_WALKER_STATES = ("wander", "attack", "flee", "walk ignoring targets", "guard",
                  "halt and shoot", "retreat to flagged node", "die", "dead")
_KIND_FIELDS = ("class", "actor id", "group", "team", "team mask")


def _msg_arg_note(msg, arg):
    """Comment for message `msg` (actor/group numbering) with literal argument `arg`."""
    if arg is None:
        return ""
    kind = None
    if 2 <= msg <= 6 or 10 <= msg <= 14:
        kind = (msg - 2) % 8
        verb = "seek" if msg < 10 else "flee from"
    elif 31 <= msg <= 35 or 41 <= msg <= 44:
        kind, verb = (msg - 31) % 10, "target"
    if kind is not None:
        a = arg & 0xff
        return f"{verb} nearest {_KIND_FIELDS[kind]} {'mask ' + hex(a) if kind == 4 else a}"
    if msg in (7, 15):
        return f"{'towards' if msg == 7 else 'away from'} node {arg & 0xff}"
    if msg == 97:
        a = arg & 0xff
        return f"{_CAMERAS[a]} Camera" if a < len(_CAMERAS) else ""
    if msg == 98:
        return {0: "walker", 1: "hovercraft"}.get(arg & 0xff, "")
    if msg == 90:
        a = arg & 0xff
        return _WALKER_STATES[a] if a < len(_WALKER_STATES) else ""
    if msg == 85:
        return "move out" if arg & 0xff else "return home"
    if msg == 80:
        return "to end" if arg & 0xff else "to start"
    if msg == 121 and 1 <= (arg & 0xff) <= 5:
        return f"filter by {_KIND_FIELDS[(arg & 0xff) - 1]}"
    return ""


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
            anote = (_msg_arg_note(args[1][1], args[2][1])
                     if op in (0x38, 0x39, 0x3a) and args[1][1] is not None else "")
            if anote:
                suffix = f"{suffix}; {anote}" if suffix else f"  # {anote}"
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
