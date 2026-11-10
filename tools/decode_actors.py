#!/usr/bin/env python3
"""Decode every Cact/Csac actor's class-specific parameters into named fields.

Companion to extract_actors.py (which only locates actors and dumps the behaviour
block as raw hex). This script re-reads the MIS files directly and writes
parsed/<LEVEL>/actors_decoded.json, one record per actor, joinable to actors.json
by actor id ("id"). Field layouts come from two sources:
  * fc-mit (Future Cop Mission Integration Tool, MIT licence,
    src/Data/Mission/ACTResource.cpp and src/Data/Mission/ACT/*.cpp);
  * the game executable SLUS_007.39, decompiled with Ghidra. Every layout
    marked "exe" was read from the class's init code (addresses below);
    "fc-mit+exe" means fc-mit's layout was checked against that code.
Confidence per class: "exe", "fc-mit+exe", "fc-mit" (not re-checked) or
"guessed" (value statistics only; none left). No class field is named
unknown_* any more (only classes with no layout fall back to unknown_<off>
words). Fields named unused_* were checked: the init copies them (or not)
and no instruction in the executable reads them; they count as decoded. Unit conversions: see "Units" below.

How the exe loads actors (all addresses in SLUS_007.39):
  0x8004d6d4  per-resource loader. Classes 87-99 are *definitions*, handled
              by their own loaders and never become actors:
              87 0x80076878, 88 0x800740b8, 89 0x800706c4, 90 0x8004ca98,
              91 0x8003bda0, 92 0x8006ed78, 93 0x80041070, 94 0x8004488c,
              95 0x800319d4 (Trigger), 96 0x80084110 (Prop), 97 0x80049ea8
              (DCSQuad), 98 0x80060838 (weapon), 99 0x8006a918 (player
              weapon). Every other class resolves its LSRa and gets its
              handler from 0x8002ae54 (class -> message handler).
  handler(msg, actor, init, ...): msg 1 = struct size, msg 2 = init. All
              inits call the BaseEntity init 0x8002a5f4 (via 0x8002ad58),
              which confirms the BaseEntity layout below. init+8 points at the
              TCAt body, so payload offset = body offset - 0x14.
  Handlers: 1 0x8005908c (init 0x800579d4), 5 0x800382b4 (0x800378bc),
            6/8/30/33/36 0x8002d664 (0x8002c584), 9 0x80032f3c (0x80032180),
            10 0x80041b6c (0x80041488), 11 0x8002dea8 (0x8002dc94; class 24
            is rewritten to 11), 12 0x80041c6c, 13 0x8002db9c (0x8002daa4,
            unused), 14/38 0x8003151c (0x80030de0), 16 0x8003015c
            (0x8002fa20), 20 0x800395a8 (0x80039504), 25 0x8002f7c8
            (0x8002eed8), 26 0x8002c3f0 (0x8002af94), 27 0x8002ed7c
            (0x8002dfc4), 28 0x800398ec (0x800397ac), 29 0x80030d3c
            (0x80030ad4), 31 0x8003a844 (0x80039e8c), 32 0x80030960
            (0x800302e8), 34 0x80042330 (0x80041e0c), 35 0x800426a8,
            37 0x800357c0 (0x800331cc), 80 0x80039c58 (unused).
            Runtime-only classes: 21 = gun sub-actor of 20/28 (0x80039298,
            init 0x80038754), 2 = projectile (0x80036624, init 0x80035904),
            15 = beam projectile (0x80037708).

Resource layout (all little-endian; sub-chunk = tag u32 + total_size u32 + body)
------------------------------------------------------------------------------
  TCAt  (fc-mit "tACT")  body:
        +0  u32  actor id (== IFF resource id)
        +4  u8   class id                      +5..7  u8[3] always 0
        +8  i32  pos_x  16.16 fixed (fc-mit calls this position_y)
        +12 i32  height, always 0 (Y comes from the terrain / ground_cast)
        +16 i32  pos_z  16.16 fixed (fc-mit position_x)
        +20 ...  class payload ("params" in actors.json: params[k] = payload word k)
  LSRa  (fc-mit "aRSL")  body: u32 actor id, then N × (type u32, id u32) slots.
        Types are FourCCs stored byte-reversed ("jboC" = Cobj). NULL = empty slot.
        Slot meaning is positional per class (see _RSL_ROLES).
  CASt  (fc-mit "tSAC", Csac only) body: u32 actor id, then 36 bytes:
        +0 i16 spawn period in VSyncs (1/60 s; loader multiplies by 5 into the
               300 Hz game clock); negative = automatic respawn
        +2 u16 max alive at once   +4 u16 flags (bit 0 = spawner starts disabled;
               message 70 toggles it)   +6 i16 spawn budget (-1 = unlimited)
        +8..35 always 0 on disc: runtime countdown and alive count live here
        (spawner code 0x800614bc / 0x80060e74).
        Csac = "spawnable actor"; Cact actors exist once at level start.

Units used in the decoded values
--------------------------------
  fx16  16.16 fixed point, 1.0 = 65536.  Positions use it (= actors.json pos_x).
  fx12  4.12 fixed point, 1.0 = 4096. Designers clearly typed decimal values
        (409 = 0.1, 819 = 0.2, 6144 = 1.5), so most u16 distances, speeds and
        heights are fx12. 1.0 game unit = 8 terrain tiles = half a Ctil chunk =
        8 Godot units in generate_scene.gd. fc-mit's "/512" heights are the same
        number in tile units (fx12 * 8).
  fx17  Teleporter destination: raw << 1 is a world fx16 position.
  deg   12-bit BAM angle, 4096 = 360 degrees. Shown as (raw mod 4096) * 360/4096.
        generate_scene.gd turns it into Godot yaw as 90 - deg. "bamr" fields
        use the code's "negative = random 0..|raw|" rule and show that.
  s     seconds. Sources (all confirmed in code): fx16s = raw/65536 (effect
        duration, *75>>14 = *300/65536 ticks); fx12s = raw/4096 (*300/4096
        ticks); fx8s = raw/256 (Pusher, *300>>8 ticks); ticks = raw/300;
        s4800 = raw/4800 for colour ramps the code steps by
        delta*4096/t * dt >> 8 per update (so the ramp lasts t/16 ticks:
        designers probably meant fx12 seconds, the game plays them ~15% faster).

Class table (TCAt +4).  "chain" lists the base structs in payload order.
------------------------------------------------------------------------
  BaseEntity   (16 B, 0x8002a5f4) bitfield u32 (entity flags), health u16,
               collision_damage u16, team u8, group_id u8, map_icon u8 (color
               = &0x1F, shape = >>5 &3), target_priority u8 (clamped to 15),
               explosion u8 (a class-91 DamageEffectSet id; its spawn_effect is
               started at init by 0x8003fa34), ambient_sound u8, uv_offset_x/y.
  BaseShooter  (16 B, 0x80060668) weapon_id u16 (<0 = none; the class-98
               weapon_id in the same level), bitfield u16 (_SHOOTER_FLAGS),
               target_select u8, target_type u8, targeting u16, fov BAM (cone
               of the search: cos(fov/2)), tracking_fov BAM (target is dropped
               when it leaves this cone, 0x8004e5f0), engage_range fx12,
               targeting_delay (time; *4096/218 at load).
               Target search 0x8004e80c(self, type, targeting, select, range,
               cos_fov, priority): type 1 = class id (1 = X1-Alpha), 2 = actor
               id, 3 = group, 4 = team, 5 = team bitmask, 7 = actor-id bit
               0x4000; select 0 = first match, 1 = nearest in cone, 2 = count,
               3 = nearest in cone with line of sight, 6 = never.
  BaseTurret   (14 B, 0x8002c584) ground_cast u8, look_target_type u8
               (-> +0xde), look_targeting u16 (-> +0xdc), gun_rotation i16 BAM
               (initial yaw), height_offset fx12, turn_speed, look_range fx12
               (-> +0xea), turn_type u8 (bit0/2: tracking, bit3: spin at
               turn_speed/300 per tick, bit4: aim at the BaseShooter's target,
               bit4/5 misc, bit6: base keeps the explosion, bit7: no
               explosion on the gun), pad. Tracking turrets (0x8002cc30)
               search every 1500 ticks (5 s) for the nearest actor matching
               (look_target_type, look_targeting) - same codes as the target
               search below - within look_range and turn towards it; with
               type 0 they face gun_rotation + 180 deg. Script messages
               0x29-0x2c set the type (msg - 0x28) and value (0x8002cb20).
               Confirmed in code.
  BasePathedEntity (20 B, 0x80050e0c) bitfield u16, move_speed (/300),
               height_offset fx12, +6 raw, +8 acceleration and +10
               deceleration (/300; the path mover 0x8005027c raises the
               current speed towards move_speed by acceleration per tick and
               lowers it by deceleration, never below the target),
               junction_seek_chance (+12, of 4096), junction_seek_range
               (+14, fx12), junction_seek_targeting (+16), junction_seek_type
               (+18 low nibble, target-search type; also bits 24-31 of the
               stored bitfield). At a junction 0x8004f330 picks the next
               node at random unless the seek succeeds. Path = Cnet in RSL
               slot 2; the actor must sit exactly on a node (0x80050dac).

  id  name                    size  chain / notes                     confidence
   1  X1Alpha (player mech)     20  BaseEntity + rotation i16,          fc-mit+exe
                                    ground_cast u8, player_index i8 (-1 =
                                    first free player slot) (0x800579d4)
   5  PathedActor               56  BE+BS+BasePathed + anim_start (AnmD  fc-mit+exe
                                    track at init), draw_flags (model draw
                                    flags, bit0 forced; msg 3 / 0x10),
                                    destroy_anim i8 (track played on msg 4;
                                    -1 none) (0x800378bc, 0x800382b4)
   6  StationaryActor           48  BE+BS+BaseTurret + pad               fc-mit+exe
   8  Turret                    52  BE+BS+BaseTurret + Turret tail. The   fc-mit+exe
                                    base is a class-11 sub-actor built
                                    from RSL slots 2/3 with ground_cast/
                                    height from BaseTurret, base_flags bit0
                                    = tilt to the terrain normal and yaw =
                                    base_rotation (0x8002dc94).
   9  Aircraft                  64  BE+BS + 32 B flight block (0x80032180) fc-mit+exe
                                    (fc-mit had orbit/spawn x and z swapped)
  10  Elevator                  40  BE + stops/heights/speeds/rotation,  fc-mit+exe
                                    trigger flags, tile_effect, end sound
                                    (0x80041488)
  11  DynamicProp               24  BE + ground_cast, align_flags (bit0 = fc-mit+exe
                                    tilt to terrain normal), rotation,
                                    height fx16
  12  WalkableProp              24  BE + rotation_y/x, height, tile_     fc-mit+exe
                                    effect (handler 0x80041c6c, msg 2)
  14  CollisionTrigger          24  BE + radius, ground_cast i16, flags,  exe
                                    filter_type/value (0x8002f8b8: same
                                    type codes as the target search; e.g.
                                    type 1 value 1 = players). Invisible
                                    cylinder; msg 0xb (0x80031120) reacts
                                    to matching actors touching it.
  16  ItemPickup (PA)           40  BE + ground_cast, filter_type/value   exe
                                    (who may pick it up, 0x8002f8b8),
                                    height fx12, box_x/y/z fx12 (pickup
                                    box half-extents, 0x8002ff6c),
                                    dispense_rate i32 fx16 units/s (when
                                    item_flags.is_set is clear: given
                                    continuously, period 300*65536/v ticks,
                                    0x8002fc90), item_flags, rotation
                                    (yaw; <0 = spin at rotational_speed)
                                    (0x8002fa20)
  20  PathedTurret              60  BE+BS+BasePathed + gun_turn_speed,    fc-mit+exe
                                    gun_rotation (<-2: limited to +-0x300
                                    around |value|), gun_flags, gun_explosion
                                    (a DamageEffectSet id: all 88 values in
                                    20/28 resolve to class-91 sets; gun =
                                    class-21 sub-actor 0x80039418)
  25  MoveableProp              32  BE + move_axis (0 yaw, 1 along local  exe
                                    x, 2 along local z, 3 vertical, 4
                                    pitch, 5 roll), move_flags (1 start
                                    at the end, 2 ping-pong, 4 walkable
                                    surface, 8 moving at start),
                                    ground_cast, stop_sound (played when
                                    it stops; the BaseEntity ambient
                                    sound loops while it moves), height,
                                    rotation, end_offset i32 fx16 (the
                                    distance; for 0/4/5 the high half is
                                    the end angle in BAM) and speed i32
                                    fx16 per second (/300 at load; BAM/s
                                    in the high half for rotations).
                                    fc-mit split the two i32s into u16s
                                    and had the axes and sound wrong
                                    (init 0x8002eed8, mover 0x8002f30c)
  26  PathedWalker              60  BE+BS+BasePathed + 8 B: behaviour     exe
                                    (state), draw_flags (as class 5),
                                    attack_anim (+1 = recoil), walk_anim
                                    (speed <= max/4 and walker_flags bit0
                                    clear), run_anim, unused_5, turn_anim
                                    (turning in place), walker_flags
                                    (0x8002af94, 0x8002b784, 0x8002b6c8;
                                    random death anims 0x41/0x42/0x44,
                                    0x8002c174)
  27  MovingShooter             60  BE+BS + 28 B: flags, ground_cast,     exe
                                    height, rotation, move offset z/x/y,
                                    speed, two pause times; shuttles
                                    between its position and pos+offset.
                                    +18..+22 are AnmD tracks for its states
                                    (0x8002e2bc / 0x8002e630): home_anim
                                    (0, paused at home), move_out_anim (1),
                                    return_anim (3), arrive_anim (2, paused
                                    at pos+offset) and attack_anim (played
                                    in state 2 when it has a target; anim
                                    event 2 fires the weapon)
  28  PathedMultiTurret        112  BE+BS+BasePathed + gun speed/rotation exe
                                    + 4 gun_flags + 4 gun_explosion +
                                    BaseShooter#2..#4 (guns 2-4 when
                                    gun_flags bit2 is clear)
  29  Teleporter (UN only)      36  BE + dest_z/dest_x (i32, world fx16 = exe
                                    raw<<1), dest height/ground_cast,
                                    ground_cast, flags, filter, radius,
                                    height. Moves matching actors that
                                    touch it (0x80030c60).
  30  StationaryActor30         48  BE+BS+BaseTurret + pad; keeps RSL     exe
                                    slots 0 and 2-4 as extra models
  31  Pusher (force field)      48  BE+BasePathed (no shooter) + 12 B:    exe
                                    radius fx12, force (<<8/300), time_on /
                                    time_off (fx8 s; on/off cycle when
                                    both != 0), falloff (0 const, 1 linear,
                                    2 linear^2, 3 d/r, 4 (d/r)^2), shape (1
                                    radial, 2 push along z within |dx|<=r,
                                    3 along x within |dz|<=r), flags (bit0
                                    enabled = script msg 110 pusher_enable,
                                    bit1 also push vertically, bit2 only
                                    players whose +0xbc != 0x10000). Adds a
                                    velocity to every player in range; the
                                    ambient sound plays only while active;
                                    may follow a Cnet (0x80039e8c init,
                                    0x8003a160 update, 0x8003a09c msgs).
                                    Was "PathedEntity31".
  32  PoliceLocker (CW pickup)  36  BE + ground_cast, filter, height,     exe
                                    use_radius fx12 (0x80030498),
                                    item_flags, rotation, refill_time
                                    (i16 seconds: after use it reopens when
                                    refill_time*300 ticks have passed; -1 =
                                    never; 0x800306a0), amount (per use, -1 =
                                    fill; 0x800306a0 -> 0x8002fc90); RSL
                                    slot 2 = item model
  33  Turret33 (M1A1)           52  BE+BS+BaseTurret + mid_rotation,      exe
                                    base_flags, base_rotation: gun + a
                                    class-6 middle part (RSL 4/5, yaw
                                    mid_rotation) + class-11 base (RSL 2/3)
  34  Sheet34                   32  BE + width, length, rotation y/x/z,   exe
                                    height, two_sided (bit0: also drawn
                                    when facing away, 0x80042128): a 5x3
                                    vertex grid (cloth/flag style surface)
  35  MapObjectiveNodeGroup     64  BE + player u8 (0/1 = player 1/2,    exe
                                    else both; the group is hung on that
                                    player, 0x800423f0), 3 unused bytes
                                    (+18/+19 copied to +0xa2/+0xa3, never
                                    read), node_states
                                    u32 (3 bits per node: bit0 radar arrow
                                    0x8005a9c4, bit1 minimap 0x8005ab8c,
                                    bit2 full map 0x8004edc8; script msg
                                    'K' sets one), 8 map icons (msg 'L'
                                    sets a colour), 8 nodes (u16 z, u16 x,
                                    raw<<9 = world fx16 - fc-mit had x/z
                                    swapped) (0x80042504)
  36  NeutralTurret (PA)        60  Turret + team_a, team_b, map_icon_a/b, exe
                                    uv_step_x/y i8, capture_radius fx12:
                                    a team_a/b player (or class 5/37 actor)
                                    within capture_radius captures it: it
                                    joins that team, targets the other,
                                    shows map_icon_a/b and shifts its UVs
                                    by uv_step * (1 or 2) (0x8002d1a0)
  37  SkyCaptain (PA)          100  Aircraft + target_team + 2 × BS:      fc-mit+exe
                                    all three guns search team
                                    target_team (type 4) (0x800331cc)
  38  AttachedCollisionBox      36  class-14 fields + attach_actor,       exe
                                    attach_bone (u8, 1-based), box size
                                    z/x/y; follows the attached actor
                                    (0x800313f0). Was "TargetLink38".
  87-90, 92-94  Effect<N>  56-76  instanced effect definitions (below)  exe
  91  DamageEffectSet           28  per-entity damage/explosion effects  exe
  95  Trigger                   12  box w/l/h fx12, ground_cast, flags,  fc-mit+exe
                                    actor ref (loader 0x800319d4)
  96  Prop                      16  rotation y/z/x (0x800188d0(y, x, z); fc-mit+exe
                                    rotation <0 = random), prop_flags
                                    (_PROP_FLAGS; 0x40: the low byte of
                                    rotation_z is an ambient sound id,
                                    0x8008363c), scale /64, spin
  97  DCSQuad                   20  flags, dcs_id i8 (Cdcs entry; <0 =   fc-mit+exe
                                    timed), height fx12, width/height fx12
                                    (skipped unless one is > 0x3f),
                                    rotation_y/x/z (0x800188d0(y, x, z);
                                    <0 = random), ground_cast, RGB
                                    (loader 0x80049ea8 -> 0x80049c38)
  98  WeaponDefinition          32  weapon table entry (below)           exe
  99  PlayerWeaponDefinition    68  weapon_slot u8 + 2 weapon blocks     exe

WeaponDefinition (class 98; loader 0x80060838 copies the 32 bytes into the
0x30-byte table at 0x800a2300 and appends 4 resolved RSL pointers; lookup by
weapon_id 0x8005ff70; fire 0x8005f6e8; projectile spawn 0x8005e47c /
0x8005ee04 / 0x8005e9e4...; projectile init 0x80035904):
  +0 weapon_id u8   +1 weapon_type u8 (projectile behaviour switch, 1-0x15;
  0 = cannot fire)  +2 ammo i16 (-1 = infinite)  +4 max_ammo i16 (only read
  for player weapons: 0x8006bd48 caps reloads at it, x1.25 / x1.5 / 9999 by
  difficulty; enemy shooters never reload)  +6 burst_count  +8 burst_delay
  +10 shot_delay (times, *4096/218 at load; negative = random up to |value|)
  +12 damage (projectile collision_damage)  +14 blast_radius fx12 (area damage
  0x800410bc)  +16 outer_radius fx12 (weapon type 13 only, a damage field
  with inner radius blast_radius; < inner -> inner*8/5, 0x8005d690)
  +18 projectile_speed (/300 at load)  +20 projectile_acceleration
  (<<8/300)  +22 max_range fx12  +24 lifetime (*4096/218)  +26 impact_effect
  (-> projectile BaseEntity.explosion slot)  +27 fire_effect (effect ids)
  +28 fire_sound  +29 unused u8 (no instruction reads +0x1d of an entry)  +30 projectile_sound
  (-> the projectile's BaseEntity.ambient_sound, 0x8005e47c -> 0x80035904)
  +31 damage_type (byte 6 of the damage message 0xe, 0x8005c344, 0x8005f6e8;
  0x8002aa1c ORs (v & 0xfe) << 3 into the victim's +0x19 and 0x8003b898
  passes (+0x19 >> 3) & 0x1e as the DamageEffectSet part mask, so bits 1-4
  pick which of the victim's DamageEffectSet part groups 1-4 play on death;
  bit0 is masked off). Confirmed in code.
  So class-98 actors are NOT placed props: the RSL Cobj is the projectile.
PlayerWeaponDefinition (class 99; loader 0x8006a918): u8 weapon_slot (the
  player's weapon choice, looked up by 0x8006aaec: 0-5 gun, 16-21 heavy, 32-36
  special = SHDR v[10..15]), u8 weapon_category (== weapon_slot >> 4 in every
  level; never read: the only byte-1 loads in the player weapon code
  read the blocks' weapon_flags), pad, then two 32-byte blocks: normal (+4) and
  powered-up (+36, used while the power-up counter is > 0). Blocks use the
  WeaponDefinition layout from +2 on (0x8006a898 applies the same unit
  conversions) but byte 0 is the weapon_type (0x8006acb0 switch) and byte 1
  player weapon_flags (bit0 projectile shadow, bit1/bit3 projectile flags
  1/2, bit2 guided: the projectile is tracked, 0x8006acb0 / 0x8006ab38).
  Weapon type 0xb (lightning) draws its fire_effect as a class-88 record
  (0x8006b23c). Kept in a 6-entry table at 0x800a3340 (0x54 B each, + 4 RSL
  pointers): only the first 6 loaded are used. Not placed either.

Instanced effects 87-90, 92-94. The loaders copy the whole TCAt body into
the table 0x800a7040[bank*256 + effect_id] (0x8003c404); spawning is
0x800402dc -> 0x8003d680 / 0x8003e984, which switches on the class to
87 0x800762b0, 88 0x80074024, 89 0x8007043c, 90 0x8004c920, 92 0x8006eb80,
93 0x80040e60, 94 0x800447f0. Csfx resources hold more records of these
very classes (Csfx parser 0x8003bfd0; its class 86 record sets the bank),
so effect ids that no actor defines live in Csfx.
  Common 32-byte header (0x8003c14c, 0x8003e984, 0x8003cae4, 0x8003c2a4):
  +0 effect_id  +1 next_effect_id (spawned when next_delay runs out, or
  at once if next_delay == 0)  +2 sound  +3 pad (0, never read)  +4
  place_yaw +6 place_pitch +8 place_distance fx12 (used for effects_flags
  bit0 "placed_at_load": spawned at the actor position offset along that
  direction)  +10 attach_node i8 (-1 = inherit)  +11 pad  +12 duration fx16 s
  (for 94 also each particle's lifetime)  +16 next_delay fx16 s
  +20 repeat_count i8 (only this byte is read, instance +7; +21
  repeat_count_hi is the authored i16's high byte)  +22 repeat_interval
  fx12 s  +24 next_count  +26 blast_damage +28 blast_radius (area damage at
  spawn 0x800410bc; class 88 uses blast_damage per touch, class 92 per
  ring sweep)  +30 effect_flags (_EFFECT_FLAGS; bit1 no_ground_kill: 94
  particles survive below the terrain, 0x800437e8; bit2: 89/93 velocity
  rotated by the placement matrix, 0x8006ff44 / 0x80040874).
  "size" in the tails is the per-class size field 0x8003db70 copies to the
  instance: 87/88/89 +32, 92 +56, 93 +34, 94 +60 (i8); 0 = the owner's
  radius, < 0 = |v|/4096 of it.
  Tails (payload offsets; field tables _EFFECT_TAILS; all confirmed in code
  unless marked plausible; rates "per 256 ticks" add v*dt/256 per update):
  87 TRAIL RIBBON (was "particle burst"; create 0x800762b0, update
    0x80075a84, draw 0x80074104, point 0x800754b8): +32 size (ribbon half
    width) +34 size_growth (added over the point's life) +36/39/42 colour_0/
    1/2 RGB +45 texture i8 (sprite 0/1 of table 0x8009b234, < 0 = animated
    4-frame sprite) +46/48/50 phase times (s4800: colour_0->1->2->black)
    +52 segment_spacing fx12 (a point is dropped per this much movement)
    +54/56/58 emit yaw/pitch/speed (bamr) +60 velocity_jitter +62 gravity
    +64 drag (v/4096 per frame) +66 flags (1 end when empty, 2 Cpyr-10 glow
    sprite per point, 4 untextured, 8 points die below ground, 0x10 world
    space; 0x2000-0x8000 runtime).
  88 LIGHTNING (create 0x80074024, handler 0x80073bbc, bolts 0x8007377c,
    re-randomise 0x80070e48, ends 0x80070730, update 0x80071b40): +32
    end_radius fx12 (far end scattered at random within it; 0x8006b23c
    shifts it <<4) +34 segment_count +36 bolt_count +39 colour RGB +42
    fade_time s4800 +44 jitter (zigzag amplitude, >>7) +46 drift fx12
    (per-point drift velocity scale; -1.0 = contract) +48 flicker_interval
    ticks (bolts re-randomised when it expires) +50 unused_50 (never read: the
    lightning code reads +0x44 and +0x48 of the record but not +0x46; 0 or
    1000) +52 width (near end; far end too unless
    flag 8) +54 flags, all confirmed: the handler 0x80073bbc turns them
    into per-end flags for 0x80070730 and bolt flags for 0x800718b8. 1 both
    ends scatter in a flat disc snapped to the terrain, 2 bolts regenerate
    only once faded, 4 the far end may jump to a random player in range
    (and damage it), 8 taper (far-end width 0), 0x10 stoppable (the create
    function sets it; the stop message then ends the effect), 0x20 both
    ends drop onto the terrain below their reference point, 0x40 the
    player seek range is doubled.
  89 FLASH DISC / STAR (create 0x8007043c, update 0x80070048 / 0x8006fb7c,
    ramps 0x8006f898, draw 0x80070158 -> 0x8001484c / 0x80014490): a
    screen-facing gouraud fan. +32 size (radius) +34 segments +36 size_rate
    (per 256 ticks) +38 centre_ramp_time / +40 rim_ramp_time fx12 s
    +42 centre_start/+45 centre_end/+48 rim_start/+51 rim_end RGB +54/56/58
    emit yaw/pitch/speed +60 gravity +62 star_shift (star: inner radius =
    radius >> n; disc: vertex phase) +64 rotation (bamr) +66 flags (1
    ping-pong ramps, 2 apply rotation, 4 star shape).
  90 MODEL SHATTER (create 0x8004c920, handler 0x8004c324, shatter
    0x8004b5f8, velocity 0x8004b310, whole model 0x8004abe4/0x8004afc0,
    landing 0x8004c0c0/0x8004b180): throws faces of the owner's model.
    +32 impact_effect (when a piece lands/expires) +33 fragment_effect
    (started on the first fragment_effect_count pieces, +34 i8, -1 = all;
    whole-model mode: on the owner) +36/38 radial speed min/max (outwards
    from the centre) +40/42 speed min/max along direction +44/46 direction
    yaw/pitch +48/50 spin min/max (BAM per 256 ticks) +52 gravity_percent
    +53 max_fragments (<= 50; 200 alive overall) +54 flags (4 flat spread,
    8 throw the whole model instead of faces, 0x10 break the owner model
    once via 0x8003a8c0 (plausible); bits 0/1 set in data but never read).
  92 SHOCKWAVE RING (was "trail"; create 0x8006eb80, handler 0x8006e814,
    update 0x8006d770, fade 0x8006e720, draw 0x8006dc24, damage
    0x8006d520): an annulus from size to size + ring_width that grows
    until max_size (then ends). +32 growth_speed (per 256 ticks) +34
    growth_decel (growth_speed -= v per 128 ticks, floor 4096) +36 max_size
    +38 ring_width +40/42 ring yaw/pitch BAM (pitch 1024 = flat) +44
    segments (rounded down to x8, >= 8) +46 inner_colour +49 outer_colour
    +52 fade_time fx12 s (both colours fade to black) +56 size (initial
    radius) +58/60 yaw/pitch speed +62 flags (1 no fade, 0x10 double
    ring, 0x40 trail mode with a 256-point buffer (0x8006c84c, drawn by
    0x8006cb58; 0x80/0x100 its options), 0x200 half ring (half the
    segments; also halves the dome's damage cone 0x200 -> 0x100 BAM), 0x400
    dome (damage via the cone test 0x80041428); 0x20 draw with the instance's own
    matrix instead of camera x placement (0x8006dc24); bits 2/3 only
    change the draw-cost estimate). blast_damage hits actors swept by the
    ring (0x800413ec / cone 0x80041428).
  93 SPRITE (create 0x80040e60, handler 0x80040874, update 0x80040a00,
    ramp 0x800403d0, Cpyr setup 0x80040734): +32 cpyr_id +34 size +36/38/40
    emit yaw/pitch/speed +42 gravity +45 colour_0 +49 colour_1 RGB +52
    fade_time s4800 +54 size_rate +56 anim_fps (300/v ticks per frame; 0 =
    still) +58/60 rotation_y/x (bamr) +62/64 their speeds (BAM per 256
    ticks; x only without flag 4) +66 flags (low byte | 0x40 = Cpyr sprite
    flags; 4 no x spin, 0x10/0x20 colour ramp on, 0x100 fade-out mode).
  94 POINT PARTICLES (create 0x800447f0, init 0x80043430, emit 0x80042af4,
    update 0x800437e8 / vortex 0x80043ca8, colour 0x80043250, draw
    0x80044260): +32 count (per emit) +34 pool_size (-1 = count, <= 700,
    halved on low memory) +36/38 speed min/max (min -1 = max) +40
    gravity_percent (vortex mode: pull towards the centre) +42/44/46 drift
    yaw/pitch/speed (the velocity particles relax towards) +48 drag_scale
    / +50 drag_time (relax rate = scale/time; time < 0 random) +52/54
    unused (copied to +0x9c/+0x9e, never read) +56/58 pitch min/max of the
    emission cone (yaw random) +60 size i8 (point size v>>1 px, clamped
    +-7, < 0 random) +61/64 colour_0/1 +68 fade_time fx12 s (|v|) +70
    colour_mode (1 fade to black, 2 ping-pong) +71 flags (1 vortex update,
    8 semi-transparent, 0x10/0x20 blend modes) +72 spawn_radius (< 0
    random) +74/75 velocity/position jitter bits (random >> (32 - n&15)).
DamageEffectSet (class 91, loader 0x8003bc7c -> table 0x800a0c70[set_id],
  referenced by BaseEntity.explosion): +0 set_id  +1 random_destroy_effect
  (used instead of part 0 at random unless set_flags bit0)  +2 set_flags
  +4 spawn_effect (started when the entity is created)  +5
  alt_destroy_effect (class 26 killed by a non-player; default effect 91)
  +6/+7 unused  +8 5 × {destroy, below-75%, below-50%, below-25% health}
  effect ids, one per hit part (0x8003b5ac, 0x8003b65c, 0x8003bae4).
  Effect ids here and in WeaponDefinition are instanced-effect ids.
  The old "ExplosionSequence" sub_effect guess at +4/+8/+16/+20/+24 was wrong.

Cross-references (collected per actor under "refs"):
  objects   Cobj ids from LSRa             paths   Cnet ids from LSRa
  actors    Trigger.triggering_actor, AttachedCollisionBox.attach_actor,
            BaseShooter/Aircraft targeting when target_type == 2
  weapons   BaseShooter.weapon_id -> actor id of the class-98 definition
  effects   effect ids (weapon impact/fire, effect chains, damage sets) ->
            actor id of the 87-90/92-94 definition, null when only Csfx
            defines it
  damage_effect_sets  BaseEntity.explosion and the 20/28 gun_explosion
            fields -> class-91 actor
  sounds    ambient/start/end/fire sound ids (index into the level sound
            table; not Cvkb resource ids, which only run 1..50)
  teams     BaseEntity.team / group_id

Initial yaw (per actor under "initial_yaw"; checked against
godot/scripts/level_layout.gd, summary printed at the end of a run). All yaws
are 12-bit BAM passed to the rotation-matrix builder 0x800188d0(yaw, x, z) or
sin/cos 0x8001846c; only the low 12 bits matter. "word" = actors.json
params[] index.
  class         field (word, bits)                      init
  1             X1Alpha.rotation (4, 0-15)              0x800579d4 (fc-mit)
  6, 30         BaseTurret.gun_rotation (9, 0-15)       0x8002c584
  8, 36         gun: BaseTurret.gun_rotation (9, 0-15); 0x8002c584
                base part: Turret.base_rotation (12, 16-31) via 0x8002dc94
  33            gun (9, 0-15); middle part mid_rotation (11, 16-31);
                base part base_rotation (12, 16-31)
  10            Elevator.rotation (8, 16-31)            0x80041488
  11            DynamicProp.rotation (4, 16-31), every sub-type 0x8002dc94
  12            WalkableProp.rotation_y (4, 0-15)       0x80041c6c
  16            ItemPickup.rotation (9, 0-15); <0 spins 0x8002fa20
  25            MoveableProp.rotation (5, 16-31)        0x8002eed8
  27            MovingShooter.rotation (9, 0-15)        0x8002dfc4
  32            PoliceLocker.rotation (7, 0-15)         0x800302e8
  34            Sheet34.rotation_y (5, 0-15)            0x80041e0c
  96            Prop.rotation_y (0, 0-15); <0 = random  0x80083a10
  97            DCSQuad.rotation_y (2, 0-15); <0 = random 0x80049c38
                (rotation_x word 2 bits 16-31, rotation_z word 3 bits 0-15)
  5, 20, 26, 28, 31  no stored yaw: heading = ratan2(dx, dz) (same BAM
                convention as the stored yaws) from the Cnet node at the
                spawn point to the next node chosen by 0x8004f330 (random
                among the links at a junction) (0x80050e0c);
                word 2 (what level_layout reads for 5/28) is BaseEntity
                team/group/map_icon.
  9, 37         no stored yaw: random heading at init (0x80032180); word 13
                / 9 are Aircraft fields.
  14, 29, 38    invisible, no model.  87-94 effects: place_yaw only for
                placed_at_load effects.  95, 35: no model.
  98, 99, 91    definitions, not placed (see above).

Output: parsed/<LEVEL>/actors_decoded.json
  {"level", "summary": {class_id: count/csac/decoded_bytes/unknown_bytes/name},
   "class_levels": {class_id: {class_name, confidence, levels: {LEVEL: count}}}
                   (global table, identical in every file),
   "actors": [ {id, kind, actor_id, class_id, class_name, confidence, chain,
                header{pos_x,height,pos_z}, fields{"Segment.name": {off, type,
                raw, value, unit?, word, word_raw}}, resources[{slot, role,
                type, id, ref}], spawn{...} (Csac), coverage{}, initial_yaw{source,
                init?, word?, bits?, bam?, deg?, parts?, note?}, refs{}} ]}
  "word" is the index into actors.json params[]; "word_raw" that whole word.
"""

import json
import os
import struct
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.iff_utils import iter_iff_resources, find_game_files


# ── field tables ───────────────────────────────────────────────────────────

_FMT = {"u8": "<B", "i8": "<b", "u16": "<H", "i16": "<h", "u32": "<I", "i32": "<i"}
_SIZE = {"u8": 1, "i8": 1, "u16": 2, "i16": 2, "u32": 4, "i32": 4}

_ENTITY_FLAGS = {
    0x00000001: "disable_actor_targeting", 0x00000004: "disable_collision",
    0x00000008: "always_active", 0x00000010: "disable_map_icon",
    0x00000020: "disable_rendering", 0x00000040: "player_physics",
    0x00000080: "is_invincible", 0x00000100: "always_interactable",
    0x00000200: "actor_collision", 0x00000400: "string_push_back",
    0x00001000: "disable_destroyed_collision", 0x00002000: "obstruct_actor_path",
    0x00020000: "disable_player_targeting", 0x00040000: "disable_explosion_targeting",
    0x00080000: "has_shadow",
}
# fc-mit getShooterBitfield() masks the *entity* bitfield (a bug); the names
# belong to the shooter's own u16. Bits checked in the weapon code
# (0x80060238 / 0x8005f6e8 / 0x800600e8) are named from that code.
_SHOOTER_FLAGS = {
    0x0001: "facing_check_1", 0x0002: "prevent_back_shooting",
    0x0004: "shoot_when_facing", 0x0008: "random_spread_y",
    0x0010: "random_spread_xz", 0x0020: "fire_alternations",
    0x0040: "target_priority", 0x0080: "projectile_has_shadow",
    0x0100: "weapon_disabled", 0x0200: "weapon_actor_collision",
    0x0400: "attackable_weapon", 0x0800: "strict_facing",
    0x1000: "projectile_flag_1", 0x2000: "projectile_flag_2",
    0x4000: "allow_switch_target", 0x8000: "script_on_target_lost",
}
# Pathed bitfield: u16 at +0 plus the u8 at +18 as bits 24..31 (0x80050e0c).
# 0x100/0x200 = orient the body to the terrain normal (0x80037eb4); 0x1000 =
# guns aim in world space (0x80038a00); 0x4000 = 0x8006314c at init.
_PATHED_FLAGS = {0x0001: "enable_backtrack", 0x0008: "disable_path_obstruction",
                 0x0100: "align_to_ground_a", 0x0200: "align_to_ground_b",
                 0x0400: "disable_ease", 0x1000: "guns_world_space",
                 0x4000: "pathed_flag_14"}
_ITEM_FLAGS = {
    0x00000001: "reload_gun", 0x00000002: "reload_heavy", 0x00000004: "reload_special",
    0x00000010: "power_up_gun", 0x00000020: "power_up_heavy",
    0x00000040: "power_up_special", 0x00020000: "restore_health",
    0x00040000: "invisibility", 0x00080000: "invincibility",
    0x01000000: "is_set", 0x02000000: "pickup_consume",
}
# BaseTurret-less guns of classes 20/28 (gun sub-actor init 0x80038754).
_GUN_FLAGS = {0x01: "destructible", 0x02: "lift_mode", 0x04: "own_shooter",
              0x10: "aim_pitch", 0x20: "start_at_gun_rotation", 0x40: "targetable"}
# Classes 14/38 (0x80031120 collision handler, 0x80031004 script messages).
_COLLISION_FLAGS = {0x01: "destroy_collider", 0x02: "script_instead_of_self_destroy",
                    0x04: "once_per_frame", 0x08: "capture_player",
                    0x10: "start_disabled", 0x20: "script_on_enable"}
# Aircraft.spawn_type (0x80032180).
_AIRCRAFT_FLAGS = {0x01: "start_at_random_orbit_point", 0x02: "start_raised",
                   0x04: "takeoff", 0x08: "use_spawn_pos"}
# EffectHeader.effect_flags (0x8003c2a4, 0x8003d9f8, 0x8003db70, 0x8003e984,
# 0x8003cae4).
_EFFECT_FLAGS = {0x0001: "placed_at_load", 0x0002: "no_ground_kill",
                 0x0004: "rotate_velocity_with_placement",
                 0x0008: "inherit_parent_transform",
                 0x0010: "camera_shake_small", 0x0020: "light_flash",
                 0x0040: "mark_parent", 0x0100: "attach_to_parent",
                 0x0800: "chain_after_repeats", 0x1000: "stop_with_parent",
                 0x2000: "camera_shake_large"}
# Class-specific effect flags (tail_flags of each effect class; see the
# docstring section "Instanced effects" for the functions).
_FX87_FLAGS = {0x0001: "end_when_empty", 0x0002: "glow_sprites", 0x0004: "untextured",
               0x0008: "die_below_ground", 0x0010: "world_space",
               0x2000: "runtime_stopped", 0x4000: "runtime_long_trail",
               0x8000: "runtime_moving"}
_FX88_FLAGS = {0x0001: "ground_scatter", 0x0002: "regenerate_after_fade",
               0x0004: "far_end_seeks_player", 0x0008: "taper",
               0x0010: "stoppable", 0x0020: "ground_drop",
               0x0040: "wide_seek"}
_FX89_FLAGS = {0x0001: "ping_pong", 0x0002: "rotated", 0x0004: "star"}
_FX90_FLAGS = {0x04: "flat_spread", 0x08: "whole_model", 0x10: "break_owner_model"}
_FX92_FLAGS = {0x0001: "no_fade", 0x0004: "draw_cost_4", 0x0008: "draw_cost_8",
               0x0010: "double_ring", 0x0020: "own_orientation",
               0x0040: "trail", 0x0080: "trail_option_80", 0x0100: "trail_option_100",
               0x0200: "half_ring", 0x0400: "dome"}
_FX93_FLAGS = {0x0004: "no_x_spin", 0x0010: "colour_fade", 0x0020: "colour_fade_b",
               0x0100: "fade_out"}
_FX94_FLAGS = {0x01: "vortex", 0x08: "semi_transparent", 0x10: "blend_additive",
               0x20: "blend_mode_20"}
_FX94_COLOUR = {0x01: "fade_to_black", 0x02: "ping_pong"}
# PlayerWeaponDefinition block byte +1 (0x8006acb0, 0x8006ab38).
_PLAYER_WEAPON_FLAGS = {0x01: "projectile_has_shadow", 0x02: "projectile_flag_1",
                        0x04: "guided", 0x08: "projectile_flag_2"}
# Pusher (class 31) flags byte (0x8003a160, 0x8003a09c).
_PUSHER_FLAGS = {0x01: "enabled", 0x02: "push_vertical", 0x04: "player_state_filter",
                 0x40: "runtime_cycling", 0x80: "runtime_off_phase"}
# Prop (class 96) +9, Prop init 0x80083a10 / update 0x80084398 / script
# messages 0x800841c4. 0x01/0x02 set model flags 8/4 (0x80013af0: 8 = sort at the
# true depth instead of 256 nearer, 0x80016a64 -> 0x80015b68; 4 is never read).
# 0x08 and 0x10 are also toggled by script messages 0x1a / 0x1b; the loader sets
# 0x80 on every prop placed at level load (message 0x19 clears it).
# MoveableProp (class 25) +1 (0x8002eed8 / 0x8002f30c).
_MOVER_FLAGS = {0x01: "start_at_end", 0x02: "ping_pong", 0x04: "walkable_surface",
                0x08: "moving_at_start"}
_PROP_FLAGS = {0x01: "true_depth_sort", 0x02: "model_flag_4", 0x04: "ping_pong_anim",
               0x08: "anim_paused", 0x10: "hidden", 0x20: "flicker_anim",
               0x40: "ambient_sound", 0x80: "permanent"}
# MapObjectiveNodeGroup per-node state (3 bits each, 0x8005a9c4 / 0x8005ab8c /
# 0x8004edc8).
_FLAG_TABLES = {"entity": _ENTITY_FLAGS, "shooter": _SHOOTER_FLAGS,
                "pathed": _PATHED_FLAGS, "item": _ITEM_FLAGS, "gun": _GUN_FLAGS,
                "collision": _COLLISION_FLAGS, "aircraft": _AIRCRAFT_FLAGS,
                "effect": _EFFECT_FLAGS, "fx87": _FX87_FLAGS, "fx88": _FX88_FLAGS,
                "fx89": _FX89_FLAGS, "fx90": _FX90_FLAGS, "fx92": _FX92_FLAGS,
                "fx93": _FX93_FLAGS, "fx94": _FX94_FLAGS, "fx94colour": _FX94_COLOUR,
                "playerweapon": _PLAYER_WEAPON_FLAGS, "pusher": _PUSHER_FLAGS,
                "prop": _PROP_FLAGS, "mover": _MOVER_FLAGS, "none": {}}

_GROUND_CAST = {0: "high", 1: "low", 3: "middle", 255: "none"}
# Pusher (class 31, 0x8003a160): force scale by distance d / radius r.
_PUSH_FALLOFF = {0: "constant", 1: "linear (1 - d/r)", 2: "quadratic ((1 - d/r)^2)",
                 3: "increasing (d/r)", 4: "increasing_quadratic ((d/r)^2)"}
_PUSH_SHAPE = {1: "radial", 2: "band_z (push along z within |dx| <= r)",
               3: "band_x (push along x within |dz| <= r)"}
_MAP_COLORS = [
    "black", "red", "blue", "green", "cyan", "yellow", "magenta", "white", "gold",
    "dark_red", "dark_blue", "dark_green", "dark_cyan", "dark_yellow",
    "dark_magenta", "gray", "orange", "red_pulse", "blue_white_pulse",
    "green_pulse", "pink", "silver", "sage", "flashing_red", "flashing_blue",
    "flashing_green", "flashing_cyan", "flashing_yellow", "flashing_magenta",
    "flashing_white", "flashing_orange", "flashing_gray",
]
_MAP_SHAPES = ["circle", "triangle", "diamond", "shape_3"]
_MOVE_AXIS = {0: "rotate_y", 1: "local_x", 2: "local_z", 3: "vertical", 4: "rotate_x",
              5: "rotate_z"}


def _f(name, typ, conv=None):
    return (name, typ, conv)


def _unknown_fill(start, end, conv=None):
    """Fill [start, end) with u16 unknown_<off> fields (u8 for an odd tail)."""
    out = []
    off = start
    while off < end:
        if off + 2 <= end and off % 2 == 0:
            out.append(_f(f"unknown_{off}", "u16", conv))
            off += 2
        else:
            out.append(_f(f"unknown_{off}", "u8", conv))
            off += 1
    return out


_BASE_ENTITY = [
    _f("bitfield", "u32", "flags:entity"),
    _f("health", "u16"),
    _f("collision_damage", "u16"),
    _f("team", "u8", "team"),
    _f("group_id", "u8", "group"),
    _f("map_icon", "u8", "map_icon"),
    _f("target_priority", "u8"),
    _f("explosion", "u8", "explosion"),
    _f("ambient_sound", "u8", "sound"),
    _f("uv_offset_x", "u8", "uv"),
    _f("uv_offset_y", "u8", "uv"),
]
_BASE_SHOOTER = [
    _f("weapon_id", "u16", "weapon"),
    _f("bitfield", "u16", "flags:shooter"),
    _f("target_select", "u8"),
    _f("target_type", "u8"),
    _f("targeting", "u16"),
    _f("fov", "u16", "bam"),
    _f("tracking_fov", "u16", "bam"),
    _f("engage_range", "u16", "fx12"),
    _f("targeting_delay", "u16"),
]
_BASE_TURRET = [
    _f("ground_cast", "u8", "ground_cast"),
    # 0x8002c584 -> +0xde/+0xdc/+0xea; used by 0x8002cc30 in the tracking
    # turn modes: every 5 s search (0x8004e80c, select 1) for the nearest
    # actor matching (look_target_type, look_targeting) within look_range
    # and turn the gun towards it. Script msgs 0x29-0x2c set type/value.
    _f("look_target_type", "u8"),
    _f("look_targeting", "u16"),
    _f("gun_rotation", "i16", "bam"),
    _f("height_offset", "i16", "fx12"),
    _f("turn_speed", "u16", "fx12"),
    _f("look_range", "u16", "fx12"),
    _f("turn_type", "u8"),
    _f("pad_13", "u8", "zero"),
]
_TURRET = [
    _f("pad_0", "u16", "zero"),
    _f("base_flags", "u8"),
    _f("pad_3", "u8", "zero"),
    _f("base_rotation", "i16", "bam"),
]
_BASE_PATHED = [
    _f("bitfield", "u16", "flags:pathed"),
    _f("move_speed", "u16", "fx12"),
    _f("height_offset", "i16", "fx12"),
    _f("move_speed_multiplier", "u16", "fx12"),
    _f("acceleration", "u16", "fx12"),
    _f("deceleration", "u16", "fx12"),
    _f("junction_seek_chance", "u16"),
    _f("junction_seek_range", "u16", "fx12"),
    _f("junction_seek_targeting", "u16"),
    _f("junction_seek_type", "u8"),
    _f("pad_19", "u8", "zero"),
]
# Aircraft init 0x80032180 (offsets are into this 32-byte block). The target
# search triple (select/type/targeting) has the same meaning as in BaseShooter.
_AIRCRAFT = [
    _f("target_select", "u8"),
    _f("target_type", "u8"),
    _f("targeting", "u16"),
    _f("initial_state", "u8"),
    _f("spawn_type", "u8", "flags:aircraft"),
    _f("target_detection_range", "u16", "fx12"),
    _f("min_target_distance", "u16", "fx12"),
    _f("height_min", "u16", "fx12"),
    _f("height_offset", "u16", "fx12"),
    _f("height_max", "u16", "fx12"),
    _f("turn_rate", "u16", "fx12"),
    _f("move_speed", "u16", "fx12"),
    _f("orbit_area_z", "u16", "fx12"),
    _f("orbit_area_x", "u16", "fx12"),
    _f("attack_time", "u16"),
    _f("orbit_time", "u16"),
    _f("spawn_pos_z", "u16", "node16"),
    _f("spawn_pos_x", "u16", "node16"),
]
# Shared by class 98 and both blocks of class 99 (0x80060838 / 0x8006a898 /
# 0x8005f6e8 / 0x8005e47c / 0x80035904).
_WEAPON = [
    _f("weapon_id", "u8"),
    _f("weapon_type", "u8"),
    _f("ammo", "i16"),
    _f("max_ammo", "i16"),
    _f("burst_count", "u16"),
    _f("burst_delay", "i16"),
    _f("shot_delay", "i16"),
    _f("damage", "u16"),
    _f("blast_radius", "u16", "fx12"),
    _f("outer_radius", "u16", "fx12"),
    _f("projectile_speed", "u16"),
    _f("projectile_acceleration", "u16"),
    _f("max_range", "u16", "fx12"),
    _f("lifetime", "u16"),
    _f("impact_effect", "u8", "effect"),
    _f("fire_effect", "u8", "effect"),
    _f("fire_sound", "u8", "sound"),
    _f("unused_29", "u8"),
    _f("projectile_sound", "u8", "sound"),
    _f("damage_type", "u8"),
]
# Class-99 blocks: same layout from +2 on, but byte 0 is the weapon type
# (the 0x8006acb0 switch) and byte 1 player-weapon flags.
_PLAYER_WEAPON = ([_f("weapon_type", "u8"), _f("weapon_flags", "u8", "flags:playerweapon")]
                  + _WEAPON[2:])
_CW_FILTER = [  # actor filter used by 0x8002f8b8 (and Trigger-like classes)
    _f("filter_type", "u8"),
    _f("filter_value", "u16"),
]

# Each class: (name, confidence, [(segment_name, fields), ...])
# confidence: "exe" = layout read from the class init code in SLUS_007.39
# (function addresses in the docstring); "fc-mit+exe" = fc-mit layout that the
# exe confirms; "fc-mit" = fc-mit only; "guessed" = value statistics only.
_CLASSES = {
    1: ("X1Alpha", "fc-mit+exe", [
        ("BaseEntity", _BASE_ENTITY),
        ("X1Alpha", [_f("rotation", "i16", "bam"), _f("ground_cast", "u8", "ground_cast"),
                     _f("player_index", "i8")])]),
    5: ("PathedActor", "fc-mit+exe", [
        ("BaseEntity", _BASE_ENTITY), ("BaseShooter", _BASE_SHOOTER),
        ("BasePathedEntity", _BASE_PATHED),
        ("PathedActor", [_f("anim_start", "u8"), _f("draw_flags", "u8"),
                         _f("destroy_anim", "i8"), _f("pad_3", "u8", "zero")])]),
    6: ("StationaryActor", "fc-mit+exe", [
        ("BaseEntity", _BASE_ENTITY), ("BaseShooter", _BASE_SHOOTER),
        ("BaseTurret", _BASE_TURRET),
        ("StationaryActor", [_f("pad_0", "u16", "zero")])]),
    8: ("Turret", "fc-mit+exe", [
        ("BaseEntity", _BASE_ENTITY), ("BaseShooter", _BASE_SHOOTER),
        ("BaseTurret", _BASE_TURRET), ("Turret", _TURRET)]),
    9: ("Aircraft", "fc-mit+exe", [
        ("BaseEntity", _BASE_ENTITY), ("BaseShooter", _BASE_SHOOTER),
        ("Aircraft", _AIRCRAFT)]),
    10: ("Elevator", "fc-mit+exe", [
        ("BaseEntity", _BASE_ENTITY),
        ("Elevator", [
            _f("num_stops", "u8"), _f("starting_position", "u8"),
            _f("height_offset_0", "i16", "fx12"), _f("height_offset_1", "i16", "fx12"),
            _f("height_offset_2", "i16", "fx12"),
            _f("stop_time_0", "u16"), _f("stop_time_1", "u16"), _f("stop_time_2", "u16"),
            _f("up_speed", "u16", "fx12"), _f("down_speed", "u16", "fx12"),
            _f("rotation", "i16", "bam"), _f("trigger_type", "u8"),
            _f("tile_effect", "u8"), _f("end_sound", "u8", "sound"),
            _f("pad_23", "u8", "zero")])]),
    11: ("DynamicProp", "fc-mit+exe", [
        ("BaseEntity", _BASE_ENTITY),
        ("DynamicProp", [
            _f("ground_cast", "u8", "ground_cast"), _f("align_flags", "u8"),
            _f("rotation", "i16", "bam"), _f("height_offset", "i32", "fx16")])]),
    12: ("WalkableProp", "fc-mit+exe", [
        ("BaseEntity", _BASE_ENTITY),
        ("WalkableProp", [
            _f("rotation_y", "i16", "bam"), _f("rotation_x", "i16", "bam"),
            _f("height_offset", "i16", "fx12"), _f("tile_effect", "u8"),
            _f("pad_7", "u8", "zero")])]),
    14: ("CollisionTrigger", "exe", [
        ("BaseEntity", _BASE_ENTITY),
        ("CollisionTrigger", [_f("radius", "u16", "fx12"), _f("ground_cast", "i16"),
                              _f("trigger_flags", "u8", "flags:collision")]
         + _CW_FILTER)]),
    16: ("ItemPickup", "fc-mit+exe", [
        ("BaseEntity", _BASE_ENTITY),
        ("ItemPickup", [
            _f("ground_cast", "u8", "ground_cast")] + _CW_FILTER + [
            _f("height_offset", "i16", "fx12"),
            _f("box_x", "i16", "fx12"), _f("box_y", "i16", "fx12"),
            _f("box_z", "i16", "fx12"), _f("dispense_rate", "i32", "fx16"),
            _f("item_flags", "u32", "flags:item"), _f("rotation", "i16", "bam"),
            _f("rotational_speed", "i16", "spin")])]),
    20: ("PathedTurret", "fc-mit+exe", [
        ("BaseEntity", _BASE_ENTITY), ("BaseShooter", _BASE_SHOOTER),
        ("BasePathedEntity", _BASE_PATHED),
        ("PathedTurret", [_f("gun_turn_speed", "i16"), _f("gun_rotation", "i16", "bam"),
                          _f("gun_flags", "u8", "flags:gun"),
                          _f("gun_explosion", "u8", "explosion"),
                          _f("pad_6", "u16", "zero")])]),
    25: ("MoveableProp", "exe", [
        ("BaseEntity", _BASE_ENTITY),
        ("MoveableProp", [
            _f("move_axis", "u8", "move_axis"), _f("move_flags", "u8", "flags:mover"),
            _f("ground_cast", "u8", "ground_cast"), _f("stop_sound", "u8", "sound"),
            _f("height_offset", "i16", "fx12"), _f("rotation", "i16", "bam"),
            # i32s (0x8002eed8). Rotation axes use the high half as BAM.
            _f("end_offset", "i32", "fx16"), _f("speed", "i32", "fx16")])]),
    26: ("PathedWalker", "exe", [
        ("BaseEntity", _BASE_ENTITY), ("BaseShooter", _BASE_SHOOTER),
        ("BasePathedEntity", _BASE_PATHED),
        ("PathedWalker", [_f("behaviour", "u8"), _f("draw_flags", "u8"),
                          _f("attack_anim", "u8"), _f("walk_anim", "u8"),
                          _f("run_anim", "u8"), _f("unused_5", "u8"),
                          _f("turn_anim", "u8"), _f("walker_flags", "u8")])]),
    27: ("MovingShooter", "exe", [
        ("BaseEntity", _BASE_ENTITY), ("BaseShooter", _BASE_SHOOTER),
        ("MovingShooter", [
            _f("move_flags", "u8"), _f("ground_cast", "u8", "ground_cast"),
            _f("height_offset", "i16", "fx12"), _f("rotation", "i16", "bam"),
            _f("move_offset_z", "i16", "fx12"), _f("move_offset_x", "i16", "fx12"),
            _f("move_offset_y", "i16", "fx12"), _f("move_speed", "i16", "fx12"),
            _f("pause_time_a", "i16"), _f("pause_time_b", "i16"),
            _f("home_anim", "u8"), _f("move_out_anim", "u8"),
            _f("return_anim", "u8"), _f("arrive_anim", "u8"), _f("attack_anim", "u8"),
            _f("destroy_flags", "u8"), _f("destroy_anim", "u8"),
            _f("pad_25", "u8", "zero"), _f("pad_26", "u16", "zero")])]),
    28: ("PathedMultiTurret", "exe", [
        ("BaseEntity", _BASE_ENTITY), ("BaseShooter", _BASE_SHOOTER),
        ("BasePathedEntity", _BASE_PATHED),
        ("MultiTurret", [
            _f("gun_turn_speed", "i16"), _f("gun_rotation", "i16", "bam")]
         + [_f(f"gun_flags_{k}", "u8", "flags:gun") for k in range(1, 5)]
         + [_f(f"gun_explosion_{k}", "u8", "explosion") for k in range(1, 5)]),
        ("BaseShooter#2", _BASE_SHOOTER), ("BaseShooter#3", _BASE_SHOOTER),
        ("BaseShooter#4", _BASE_SHOOTER)]),
    29: ("Teleporter", "exe", [
        ("BaseEntity", _BASE_ENTITY),
        ("Teleporter", [_f("dest_z", "i32", "fx17"), _f("dest_x", "i32", "fx17"),
                        _f("dest_height_offset", "i16", "fx12"),
                        _f("dest_ground_cast", "u8", "ground_cast"),
                        _f("ground_cast", "u8", "ground_cast"),
                        _f("trigger_flags", "u8"), _f("filter_type", "u8"),
                        _f("filter_value", "u16"), _f("radius", "u16", "fx12"),
                        _f("height_offset", "i16", "fx12")])]),
    30: ("StationaryActor30", "exe", [
        ("BaseEntity", _BASE_ENTITY), ("BaseShooter", _BASE_SHOOTER),
        ("BaseTurret", _BASE_TURRET), ("StationaryActor30", [_f("pad_0", "u16", "zero")])]),
    31: ("Pusher", "exe", [
        ("BaseEntity", _BASE_ENTITY), ("BasePathedEntity", _BASE_PATHED),
        ("Pusher", [_f("radius", "i16", "fx12"), _f("force", "i16"),
                    _f("time_on", "i16", "fx8s"), _f("time_off", "i16", "fx8s"),
                    _f("falloff", "u8", "falloff"), _f("shape", "u8", "push_shape"),
                    _f("pusher_flags", "u8", "flags:pusher"), _f("pad_11", "u8", "zero")])]),
    32: ("PoliceLocker", "exe", [
        ("BaseEntity", _BASE_ENTITY),
        ("PoliceLocker", [
            _f("ground_cast", "u8", "ground_cast"), _f("filter_type", "u8"),
            _f("filter_value", "u16"), _f("height_offset", "i16", "fx12"),
            _f("use_radius", "u16", "fx12"), _f("item_flags", "u32", "flags:item"),
            _f("rotation", "i16", "bam"), _f("refill_time", "i16"),
            _f("amount", "i16"), _f("pad_18", "u16", "zero")])]),
    33: ("Turret33", "exe", [
        ("BaseEntity", _BASE_ENTITY), ("BaseShooter", _BASE_SHOOTER),
        ("BaseTurret", _BASE_TURRET),
        ("Turret33", [_f("mid_rotation", "i16", "bam"), _f("base_flags", "u8"),
                      _f("pad_3", "u8", "zero"), _f("base_rotation", "i16", "bam")])]),
    34: ("Sheet34", "exe", [
        ("BaseEntity", _BASE_ENTITY),
        ("Sheet34", [_f("width", "u16", "fx12"), _f("length", "u16", "fx12"),
                     _f("rotation_y", "i16", "bam"), _f("rotation_x", "i16", "bam"),
                     _f("rotation_z", "i16", "bam"), _f("height_offset", "i16", "fx12"),
                     _f("two_sided", "u8"), _f("pad_13", "u8", "zero"),
                     _f("pad_14", "u16", "zero")])]),
    35: ("MapObjectiveNodeGroup", "exe", [
        ("BaseEntity", _BASE_ENTITY),
        ("MapObjectiveNodeGroup",
         [_f("player", "u8"), _f("unused_17", "u8"), _f("unused_18", "u8"),
          _f("unused_19", "u8"), _f("node_states", "u32", "node_states")]
         + [_f(f"map_icon_{k}", "u8", "map_icon") for k in range(8)]
         # first u16 -> world z, second -> world x (0x8004edc8)
         + [x for k in range(8) for x in (_f(f"node_{k}_z", "u16", "node16"),
                                          _f(f"node_{k}_x", "u16", "node16"))])]),
    36: ("NeutralTurret", "fc-mit+exe", [
        ("BaseEntity", _BASE_ENTITY), ("BaseShooter", _BASE_SHOOTER),
        ("BaseTurret", _BASE_TURRET), ("Turret", _TURRET),
        ("NeutralTurret", [_f("team_a", "u8", "team"), _f("team_b", "u8", "team"),
                           _f("map_icon_a", "u8", "map_icon"),
                           _f("map_icon_b", "u8", "map_icon"),
                           _f("uv_step_x", "i8"), _f("uv_step_y", "i8"),
                           _f("capture_radius", "u16", "fx12")])]),
    37: ("SkyCaptain", "fc-mit+exe", [
        ("BaseEntity", _BASE_ENTITY), ("BaseShooter", _BASE_SHOOTER),
        ("Aircraft", _AIRCRAFT),
        ("SkyCaptain", [_f("target_team", "u8"), _f("pad_1", "u16", "zero"),
                        _f("pad_3", "u8", "zero")]),
        ("BaseShooter#2", _BASE_SHOOTER), ("BaseShooter#3", _BASE_SHOOTER)]),
    38: ("AttachedCollisionBox", "exe", [
        ("BaseEntity", _BASE_ENTITY),
        ("AttachedCollisionBox", [
            _f("radius", "u16", "fx12"), _f("ground_cast", "i16"),
            _f("trigger_flags", "u8", "flags:collision")] + _CW_FILTER + [
            _f("attach_actor", "u16", "actor"), _f("attach_bone", "u8"),
            _f("pad_11", "u8", "zero"), _f("size_z", "u16", "fx12"),
            _f("size_x", "u16", "fx12"), _f("size_y", "u16", "fx12"),
            _f("pad_18", "u16", "zero")])]),
    95: ("Trigger", "fc-mit+exe", [
        ("Trigger", [_f("width", "u16", "fx12"), _f("length", "u16", "fx12"),
                     _f("height", "u16", "fx12"), _f("ground_cast", "u8", "ground_cast"),
                     _f("bitfield", "u8", "flags:none"),
                     _f("triggering_actor", "u16", "actor"), _f("pad_10", "u16", "zero")])]),
    96: ("Prop", "fc-mit+exe", [
        ("Prop", [_f("rotation_y", "i16", "bam"), _f("rotation_z", "i16", "bam"),
                  _f("rotation_x", "i16", "bam"), _f("height_offset", "i16", "fx12"),
                  _f("ground_cast", "u8", "ground_cast"), _f("prop_flags", "u8", "flags:prop"),
                  _f("animation_speed", "u8"), _f("scale_y", "u8", "scale64"),
                  _f("scale_z", "u8", "scale64"), _f("scale_x", "u8", "scale64"),
                  _f("spin_speed", "i8"), _f("spin_angle", "u8", "degrees")])]),
    97: ("DCSQuad", "fc-mit+exe", [
        ("DCSQuad", [_f("bitfield", "u8", "flags:none"), _f("dcs_id", "i8"),
                     _f("height_offset", "i16", "fx12"), _f("width", "u16", "fx12"),
                     _f("height", "u16", "fx12"), _f("rotation_y", "i16", "bamr"),
                     _f("rotation_x", "i16", "bamr"), _f("rotation_z", "i16", "bamr"),
                     _f("ground_cast", "u8", "ground_cast"), _f("red", "u8"),
                     _f("green", "u8"), _f("blue", "u8"), _f("pad_18", "u16", "zero")])]),
    98: ("WeaponDefinition", "exe", [("WeaponDefinition", _WEAPON)]),
    99: ("PlayerWeaponDefinition", "exe", [
        ("PlayerWeaponDefinition", [_f("weapon_slot", "u8"), _f("weapon_category", "u8"),
                                    _f("pad_2", "u16", "zero")]),
        ("Weapon#1", _PLAYER_WEAPON), ("Weapon#2", _PLAYER_WEAPON)]),
}
_EFFECT_SIZES = {87: 68, 88: 56, 89: 68, 90: 56, 91: 28, 92: 64, 93: 68, 94: 76}
# Common 32-byte header of the instanced effect classes 87-90/92-94, read by
# 0x8003c14c (copy into the instance), 0x8003e984 (spawn), 0x8003cae4 (timers,
# chaining) and 0x8003c2a4 (placed-at-load instances). Bytes 3 and 11 are 0 in
# every record and never read; byte 21 is the high byte of an authored i16
# repeat count (0xff with -1) but only the low byte is read (instance +7, i8).
_EFFECT_HEADER = [
    _f("effect_id", "u8"), _f("next_effect_id", "u8", "effect"),
    _f("sound", "u8", "sound"), _f("pad_3", "u8", "zero"),
    _f("place_yaw", "i16", "bam"), _f("place_pitch", "i16", "bam"),
    _f("place_distance", "i16", "fx12"), _f("attach_node", "i8"),
    _f("pad_11", "u8", "zero"),
    _f("duration", "i32", "fx16s"), _f("next_delay", "i32", "fx16s"),
    _f("repeat_count", "i8"), _f("repeat_count_hi", "u8"),
    _f("repeat_interval", "i16", "fx12s"), _f("next_count", "u16"),
    _f("blast_damage", "i16"), _f("blast_radius", "i16"),
    _f("effect_flags", "u16", "flags:effect"),
]
_RGB = lambda p: [_f(f"{p}_r", "u8"), _f(f"{p}_g", "u8"), _f(f"{p}_b", "u8")]
# Class tails, payload offsets 32.. (= Csfx body offsets 52..). "size" is the
# class's size field picked by 0x8003db70 (0 = the owner's radius, < 0 =
# |v|/4096 of it). Rates "per 256 ticks": x += v * dt / 256 with dt in 300 Hz
# clock ticks. Confirmed in code unless the docstring says plausible.
_EFFECT_TAILS = {
    # 87 trail ribbon (create 0x800762b0, update 0x80075a84, draw 0x80074104,
    # point spawn 0x800754b8): points are dropped every segment_spacing of
    # movement and joined into a quad strip whose colour fades
    # colour_0 -> colour_1 -> colour_2 -> black over the three phase times.
    87: [_f("size", "i16"), _f("size_growth", "i16")]
        + _RGB("colour_0") + _RGB("colour_1") + _RGB("colour_2")
        + [_f("texture", "i8"), _f("phase_time_0", "i16", "s4800"),
           _f("phase_time_1", "i16", "s4800"), _f("phase_time_2", "i16", "s4800"),
           _f("segment_spacing", "i16", "fx12"),
           _f("emit_yaw", "i16", "bamr"), _f("emit_pitch", "i16", "bamr"),
           _f("emit_speed", "i16", "fx12"), _f("velocity_jitter", "i16"),
           _f("gravity", "i16"), _f("drag", "i16"),
           _f("tail_flags", "u16", "flags:fx87")],
    # 88 lightning (create 0x80074024, handler 0x80073bbc, bolt builder
    # 0x8007377c, re-randomise 0x80070e48, end points 0x80070730, update
    # 0x80071b40): bolt_count jagged lines of segment_count points between
    # the owner and a far end scattered within end_radius.
    88: [_f("end_radius", "i16", "fx12"), _f("segment_count", "i16"),
         _f("bolt_count", "i16"), _f("pad_38", "u8", "zero")]
        + _RGB("colour")
        + [_f("fade_time", "i16", "s4800"), _f("jitter", "i16"),
           _f("drift", "i16", "fx12"), _f("flicker_interval", "i16", "ticks"),
           _f("unused_50", "i16"), _f("width", "i16"),
           _f("tail_flags", "u16", "flags:fx88")],
    # 89 flash disc / star (create 0x8007043c, update 0x80070048 + 0x8006fb7c,
    # ramps 0x8006f898, draw 0x80070158 -> 0x8001484c disc / 0x80014490 star):
    # a screen-facing gouraud fan, centre colour ramp and rim colour ramp.
    89: [_f("size", "i16"), _f("segments", "u8"), _f("pad_35", "u8", "zero"),
         _f("size_rate", "i16"), _f("centre_ramp_time", "i16", "fx12s"),
         _f("rim_ramp_time", "i16", "fx12s")]
        + _RGB("centre_start") + _RGB("centre_end") + _RGB("rim_start") + _RGB("rim_end")
        + [_f("emit_yaw", "i16", "bamr"), _f("emit_pitch", "i16", "bamr"),
           _f("emit_speed", "i16", "fx12"), _f("gravity", "i16"),
           _f("star_shift", "u8"), _f("pad_63", "u8", "zero"),
           _f("rotation", "i16", "bamr"), _f("tail_flags", "u16", "flags:fx89")],
    # 90 model shatter (create 0x8004c920, handler 0x8004c324, shatter
    # 0x8004b5f8, fragment velocity 0x8004b310, whole-model mode 0x8004abe4 /
    # 0x8004afc0, landing 0x8004c0c0 / 0x8004b180): throws up to
    # max_fragments faces of the owner's model.
    90: [_f("impact_effect", "u8", "effect"), _f("fragment_effect", "u8", "effect"),
         _f("fragment_effect_count", "i8"), _f("pad_35", "u8", "zero"),
         _f("radial_speed_min", "i16"), _f("radial_speed_max", "i16"),
         _f("direction_speed_min", "i16"), _f("direction_speed_max", "i16"),
         _f("direction_yaw", "i16", "bam"), _f("direction_pitch", "i16", "bam"),
         _f("spin_min", "i16"), _f("spin_max", "i16"),
         _f("gravity_percent", "i8", "percent"), _f("max_fragments", "u8"),
         _f("tail_flags", "u8", "flags:fx90"), _f("pad_55", "u8", "zero")],
    # 92 shockwave ring (create 0x8006eb80, handler 0x8006e814, update
    # 0x8006d770, fade 0x8006e720, draw 0x8006dc24, damage 0x8006d520):
    # an annulus from size to size + ring_width that grows until max_size.
    92: [_f("growth_speed", "i16"), _f("growth_decel", "i16"),
         _f("max_size", "i16"), _f("ring_width", "i16"),
         _f("ring_yaw", "i16", "bam"), _f("ring_pitch", "i16", "bam"),
         _f("segments", "u16")]
        + _RGB("inner_colour") + _RGB("outer_colour")
        + [_f("fade_time", "i16", "fx12s"), _f("pad_54", "u8", "zero"),
           _f("pad_55", "u8", "zero"), _f("size", "i16"),
           _f("yaw_speed", "i16"), _f("pitch_speed", "i16"),
           _f("tail_flags", "u16", "flags:fx92")],
    # 93 sprite (create 0x80040e60, handler 0x80040874, update 0x80040a00,
    # colour ramp 0x800403d0, Cpyr setup 0x80040734).
    93: [_f("cpyr_id", "u8"), _f("pad_33", "u8", "zero"), _f("size", "i16"),
         _f("emit_yaw", "i16", "bam"), _f("emit_pitch", "i16", "bam"),
         _f("emit_speed", "i16", "fx12"), _f("gravity", "i16"), _f("pad_44", "u8")]
        + _RGB("colour_0") + [_f("pad_48", "u8")] + _RGB("colour_1")
        + [_f("fade_time", "i16", "s4800"), _f("size_rate", "i16"),
           _f("anim_fps", "i16"), _f("rotation_y", "i16", "bamr"),
           _f("rotation_x", "i16", "bamr"), _f("rotation_y_speed", "i16"),
           _f("rotation_x_speed", "i16"), _f("tail_flags", "u16", "flags:fx93")],
    # 94 point particles (create 0x800447f0, init 0x80043430, emit 0x80042af4,
    # update 0x800437e8 / 0x80043ca8 (vortex), colour 0x80043250, draw
    # 0x80044260).
    94: [_f("count", "i16"), _f("pool_size", "i16"),
         _f("speed_min", "i16"), _f("speed_max", "i16"),
         _f("gravity_percent", "i16", "percent"),
         _f("drift_yaw", "i16", "bam"), _f("drift_pitch", "i16", "bam"),
         _f("drift_speed", "i16", "fx12"), _f("drag_scale", "i16"),
         _f("drag_time", "i16"), _f("unused_52", "i16"), _f("unused_54", "i16"),
         _f("pitch_min", "i16", "bam"), _f("pitch_max", "i16", "bam"),
         _f("size", "i8")]
        + _RGB("colour_0") + _RGB("colour_1")
        + [_f("pad_67", "u8"), _f("fade_time", "i16", "fx12s"),
           _f("colour_mode", "u8", "flags:fx94colour"),
           _f("tail_flags", "u8", "flags:fx94"), _f("spawn_radius", "i16"),
           _f("velocity_jitter_bits", "u8"), _f("position_jitter_bits", "u8")],
}
for _cid in _EFFECT_SIZES:
    if _cid == 91:
        continue
    _CLASSES[_cid] = (f"Effect{_cid}", "exe",
                      [("EffectHeader", _EFFECT_HEADER),
                       (f"Effect{_cid}", _EFFECT_TAILS[_cid])])
# 91 is not an instanced effect: 0x8003bc7c stores it in its own 256-entry
# table (0x800a0c70) indexed by BaseEntity.explosion. Readers: 0x8003b4e0,
# 0x8003b5ac, 0x8003b7d8, 0x8003b838, 0x8003b898, 0x8003bae4, 0x8003fa34.
_CLASSES[91] = ("DamageEffectSet", "exe", [("DamageEffectSet", [
    _f("set_id", "u8"), _f("random_destroy_effect", "u8", "effect"),
    _f("set_flags", "u16"), _f("spawn_effect", "u8", "effect"),
    _f("alt_destroy_effect", "u8", "effect"), _f("unused_6", "u8"),
    _f("unused_7", "u8")]
    + [x for k in range(5) for x in (
        _f(f"part_{k}_destroy_effect", "u8", "effect"),
        _f(f"part_{k}_damage_75_effect", "u8", "effect"),
        _f(f"part_{k}_damage_50_effect", "u8", "effect"),
        _f(f"part_{k}_damage_25_effect", "u8", "effect"))])])

# Positional LSRa slot roles (fc-mit check_RSL()/getters where known).
_RSL_ROLES = {
    1: ["legs", "unused", "cockpit", "weapon", "beacon_lights", "pilot"],
    5: ["alive_model", "destroyed_model", "path"],
    6: ["gun_model", "gun_destroyed_model"],
    8: ["gun_model", "gun_destroyed_model", "base_model", "base_destroyed_model"],
    9: ["model", "destroyed_model"],
    10: ["model", "unused"],
    11: ["alive_model", "destroyed_model"],
    12: ["alive_model", "destroyed_model"],
    16: ["item_model", "unused"],
    20: ["alive_model", "destroyed_model", "path", "gun_model"],
    25: ["alive_model", "destroyed_model"],
    26: ["alive_model", "destroyed_model", "path"],
    28: ["alive_model", "destroyed_model", "path", "gun_model_1", "gun_model_2",
         "gun_model_3", "gun_model_4"],
    # 30: slot 0 and slots 2-4 are kept as alternative models (0x8002c584).
    30: ["gun_model", "gun_destroyed_model", "extra_model_2", "extra_model_3",
         "extra_model_4"],
    31: ["alive_model", "destroyed_model", "path"],
    32: ["model", "destroyed_model", "item_model"],
    # 33: gun + class-11 base (slots 2/3) + a class-6 middle turret (slots 4/5).
    33: ["gun_model", "gun_destroyed_model", "base_model", "base_destroyed_model",
         "mid_model", "mid_destroyed_model"],
    36: ["gun_model", "gun_destroyed_model", "base_model", "base_destroyed_model"],
    37: ["model", "destroyed_model"],
    96: ["model"],
    98: ["projectile_model", "model_1", "model_2", "model_3"],
    99: ["projectile_model", "slot_1", "slot_2", "projectile_model_2"],
}

for _cid, (_n, _c, _segs) in _CLASSES.items():
    _sz = sum(_SIZE[t] for _s, fl in _segs for _nm, t, _cv in fl)
    assert _sz == {1: 20, 5: 56, 6: 48, 8: 52, 9: 64, 10: 40, 11: 24, 12: 24, 14: 24,
                   16: 40, 20: 60, 25: 32, 26: 60, 27: 60, 28: 112, 29: 36, 30: 48,
                   31: 48, 32: 36, 33: 52, 34: 32, 35: 64, 36: 60, 37: 100, 38: 36,
                   95: 12, 96: 16, 97: 20, 98: 32, 99: 68,
                   **_EFFECT_SIZES}[_cid], (_cid, _sz)


# ── helpers ────────────────────────────────────────────────────────────────

def _sub_chunks(raw):
    """Walk little-endian (tag u32 + total_size u32) sub-chunks; yield (tag, body)."""
    pos = 0
    while pos + 8 <= len(raw):
        tag = raw[pos:pos + 4].decode("ascii", errors="replace")
        size = struct.unpack_from("<I", raw, pos + 4)[0]
        if size < 8 or pos + size > len(raw):
            break
        yield tag, raw[pos + 8:pos + size]
        pos += size


def _flag_names(value, table):
    names = []
    bit = 1
    while bit <= value:
        if value & bit:
            names.append(table.get(bit, f"bit_{bit.bit_length() - 1}"))
        bit <<= 1
    return names


def _convert(raw, conv):
    """Return (value, unit) for a raw field value."""
    if conv is None or conv == "zero":
        return raw, None
    if conv == "fx12":
        return round(raw / 4096.0, 5), "fx12"
    if conv == "fx16":
        return round(raw / 65536.0, 5), "fx16"
    if conv == "fx17":     # Teleporter destination: world fx16 = raw << 1
        return round(raw / 32768.0, 5), "fx16"
    if conv == "bam":
        return round((raw % 4096) * 360.0 / 4096.0, 3), "deg"
    if conv == "bamr":     # BAM, negative = random 0..|raw| (rand % |raw|)
        if raw < 0:
            return f"random 0..{round(-raw * 360.0 / 4096.0, 3)}", "deg"
        return round((raw % 4096) * 360.0 / 4096.0, 3), "deg"
    if conv == "fx16s":
        return round(raw / 65536.0, 5), "s"
    if conv == "fx12s":    # fx12 seconds (clock: v * 300 / 4096 ticks)
        return round(raw / 4096.0, 5), "s"
    if conv == "fx8s":     # Pusher on/off times: v * 300 / 256 ticks
        return round(raw / 256.0, 5), "s"
    if conv == "s4800":    # colour-ramp times stepped as delta*16*dt/t: t/16 ticks
        return round(raw / 4800.0, 5), "s"
    if conv == "ticks":    # 300 Hz game-clock ticks
        return round(raw / 300.0, 5), "s"
    if conv == "percent":
        return raw, "%"
    if conv == "falloff":
        return _PUSH_FALLOFF.get(raw, f"unknown_{raw}"), None
    if conv == "push_shape":
        return _PUSH_SHAPE.get(raw, f"unknown_{raw}"), None
    if conv == "node_states":
        return [{"node": k, "radar": bool(raw >> 3 * k & 1),
                 "minimap": bool(raw >> 3 * k & 2), "map": bool(raw >> 3 * k & 4)}
                for k in range(8) if raw >> 3 * k & 7], "flags"
    if conv == "degrees":
        return raw, "deg"
    if conv == "uv":
        return round(raw / 256.0, 5), "uv"
    if conv == "scale64":
        return round(raw / 64.0, 5), "scale"
    if conv == "node16":   # fc-mit: tile units = raw / 16  ->  game units = raw / 128
        return round(raw / 128.0, 5), "fx16"
    if conv == "spin":     # fc-mit ItemPickup.getRotationSpeed(): raw * 0.0021 rad/s
        return round(raw * 0.0021, 5), "rad/s"
    if conv.startswith("flags:"):
        return _flag_names(raw, _FLAG_TABLES[conv[6:]]), "flags"
    if conv == "ground_cast":
        return _GROUND_CAST.get(raw, f"unknown_{raw}"), None
    if conv == "move_axis":
        return _MOVE_AXIS.get(raw, f"unknown_{raw}"), None
    if conv == "map_icon":
        if raw == 0:
            return None, None
        return {"color": _MAP_COLORS[raw & 0x1F],
                "shape": _MAP_SHAPES[(raw >> 5) & 3]}, None
    if conv == "sound":
        return (f"sound {raw}" if raw else None), "sound_id"
    if conv == "actor":
        return (f"actor {raw}" if raw != 0xFFFF else None), "actor_id"
    if conv == "weapon":
        return (f"weapon {raw}" if raw else None), "weapon_id"
    if conv == "effect":
        return (f"effect {raw}" if raw else None), "effect_id"
    if conv == "explosion":
        return (f"damage_effect_set {raw}" if raw else None), "explosion_id"
    if conv in ("team", "group"):
        return raw, conv
    raise ValueError(conv)


def _decode_payload(cls, payload, warnings):
    """Decode a TCAt class payload; return (class_name, confidence, chain, fields,
    decoded_bytes, unknown_bytes)."""
    if cls not in _CLASSES:
        fields = {}
        for name, typ, _ in _unknown_fill(0, len(payload)):
            off = int(name.split("_")[1])
            v = struct.unpack_from(_FMT[typ], payload, off)[0]
            fields[f"Unknown.{name}"] = _field_record(off, typ, v, None, payload)
        return f"unknown class {cls}", "none", ["Unknown"], fields, 0, len(payload)

    name, conf, segs = _CLASSES[cls]
    fields = {}
    off = 0
    known = unknown = 0
    for seg, flist in segs:
        for fname, typ, conv in flist:
            v = struct.unpack_from(_FMT[typ], payload, off)[0]
            rec = _field_record(off, typ, v, conv, payload)
            if conv == "zero" and v != 0:
                warnings[(cls, f"{seg}.{fname}")] += 1
                rec["unexpected_nonzero"] = True
            fields[f"{seg}.{fname}"] = rec
            if fname.startswith("unknown") or fname.startswith("gun_unknown"):
                unknown += _SIZE[typ]
            else:
                known += _SIZE[typ]
            off += _SIZE[typ]
    return name, conf, [s for s, _ in segs], fields, known, unknown


def decode_definition(cls, payload):
    """Decode a definition/effect payload (TCAt body[20:]) with the class
    tables above; shared with extract_sfx_meta.py for the Csfx records.
    Returns (class_name, {segment: {field: {off, type, raw, value, unit?}}})."""
    name, _conf, _chain, fields, _k, _u = _decode_payload(cls, payload, Counter())
    out = defaultdict(dict)
    for key, rec in fields.items():
        seg, fname = key.split(".", 1)
        out[seg][fname] = {k: v for k, v in rec.items() if k not in ("word", "word_raw")}
    return name, dict(out)


def _field_record(off, typ, raw, conv, payload):
    value, unit = _convert(raw, conv)
    word = off // 4
    rec = {"off": off, "type": typ, "raw": raw, "value": value}
    if unit:
        rec["unit"] = unit
    rec["word"] = word
    if word * 4 + 4 <= len(payload):
        rec["word_raw"] = hex(struct.unpack_from("<I", payload, word * 4)[0])
    return rec


def _decode_lsra(body, cls):
    slots = []
    roles = _RSL_ROLES.get(cls, [])
    for k, i in enumerate(range(4, len(body) - 7, 8)):
        rtype = body[i:i + 4][::-1].decode("ascii", errors="replace")
        rid = struct.unpack_from("<I", body, i + 4)[0]
        slot = {"slot": k, "role": roles[k] if k < len(roles) else f"slot_{k}",
                "type": rtype, "id": rid}
        if rtype == "Cobj":
            slot["ref"] = f"cobj{rid:03d}"
        elif rtype == "Cnet":
            slot["ref"] = f"cnet {rid}"
        elif rtype == "NULL":
            slot["ref"] = None
        else:
            slot["ref"] = f"{rtype} {rid}"
        slots.append(slot)
    return slots


def _decode_cast(body, warnings):
    """CASt (fc-mit tSAC): spawn parameters of a Csac actor."""
    ticks, limit, flags, budget = struct.unpack_from("<hHHh", body, 4)
    tail = body[12:]
    if any(tail):
        warnings[("CASt", "tail")] += 1
    return {
        "actor_id": struct.unpack_from("<I", body, 0)[0],
        "spawn_ticks": ticks,
        "spawn_seconds": round(abs(ticks) / 60.0, 3),
        "automatic": ticks < 0,
        "max_alive": limit,
        "flags": flags,
        "starts_disabled": bool(flags & 1),
        "spawn_budget": budget,
        "unlimited": budget < 0,
        "unknown_tail_hex": tail.hex() if any(tail) else None,
        "decoded_bytes": 4 + 8, "unknown_bytes": len(tail),
    }


_TARGET_TYPES = {1: "class", 2: "actor", 3: "group", 4: "team", 5: "team_mask",
                 7: "actor_flag_4000"}


def _resolve_targeting(rec):
    """Resolve each (target_type, targeting) pair from the target search
    0x8004e80c: 1 = actor class id (1 = the X1-Alpha players), 2 = actor id,
    3 = group id, 4 = team, 5 = bitmask of teams, 7 = actors whose id has bit
    0x4000; 6 = no search."""
    for seg in ("BaseShooter", "BaseShooter#2", "BaseShooter#3", "BaseShooter#4",
                "Aircraft"):
        tt = rec["fields"].get(f"{seg}.target_type")
        if tt is None:
            continue
        tg = rec["fields"][f"{seg}.targeting"]
        kind = _TARGET_TYPES.get(tt["raw"])
        if kind == "actor":
            tg["value"], tg["unit"] = f"actor {tg['raw']}", "actor_id"
        elif kind:
            tg["value"], tg["unit"] = tg["raw"], f"target_{kind}"


def _collect_refs(rec, weapon_actor, effect_actor, explosion_actor):
    refs = defaultdict(list)
    for s in rec["resources"]:
        if s["type"] == "Cobj":
            refs["objects"].append(s["ref"])
        elif s["type"] == "Cnet":
            refs["paths"].append(s["id"])
    for key, f in rec["fields"].items():
        conv_unit = f.get("unit")
        if f["value"] is None:
            continue
        if conv_unit == "actor_id":
            refs["actors"].append(f["raw"])
        elif conv_unit == "sound_id":
            refs["sounds"].append(f["raw"])
        elif conv_unit == "weapon_id":
            refs["weapons"].append({"weapon_id": f["raw"],
                                    "actor": weapon_actor.get(f["raw"])})
        elif conv_unit == "effect_id":
            refs["effects"].append({"effect_id": f["raw"],
                                    "actor": effect_actor.get(f["raw"])})
        elif conv_unit == "explosion_id":
            refs["damage_effect_sets"].append({"set_id": f["raw"],
                                               "actor": explosion_actor.get(f["raw"])})
        elif conv_unit == "team" and key.startswith("BaseEntity."):
            refs["team"] = f["raw"]
        elif conv_unit == "group" and key.startswith("BaseEntity."):
            refs["group_id"] = f["raw"]
    return dict(refs)


# ── per-level decoding ─────────────────────────────────────────────────────

def decode_level(mis_path, warnings, xstats):
    with open(mis_path, "rb") as f:
        data = f.read()
    level = os.path.splitext(os.path.basename(mis_path))[0]

    records = []
    cnet_ids = set()
    for res_type, res_id, raw in iter_iff_resources(data):
        if res_type == "Cnet":
            cnet_ids.add(res_id)
        if res_type not in ("Cact", "Csac"):
            continue
        chunks = dict(_sub_chunks(raw))
        tcat = chunks["TCAt"]
        actor_id, cls = struct.unpack_from("<IB", tcat, 0)
        x, h, z = struct.unpack_from("<iii", tcat, 8)
        payload = tcat[20:]
        name, conf, chain, fields, known, unknown = _decode_payload(cls, payload, warnings)
        rec = {
            "id": res_id, "kind": res_type, "actor_id": actor_id,
            "class_id": cls, "class_name": name, "confidence": conf,
            "chain": ["ACTResource"] + chain,
            "header": {
                "unknown_5": tcat[5:8].hex(),
                "pos_x": {"raw": x, "value": round(x / 65536.0, 5), "unit": "fx16"},
                "height": {"raw": h, "value": round(h / 65536.0, 5), "unit": "fx16"},
                "pos_z": {"raw": z, "value": round(z / 65536.0, 5), "unit": "fx16"},
            },
            "fields": fields,
            "resources": _decode_lsra(chunks.get("LSRa", b""), cls),
            "coverage": {"payload_bytes": len(payload), "decoded_bytes": known,
                         "unknown_bytes": unknown},
        }
        if any(tcat[5:8]):
            warnings[("header", "unknown_5")] += 1
        if "CASt" in chunks:
            rec["spawn"] = _decode_cast(chunks["CASt"], warnings)
        extra = sorted(set(chunks) - {"TCAt", "LSRa", "CASt"})
        if extra:
            rec["unknown_chunks"] = {t: chunks[t].hex() for t in extra}
        rec["initial_yaw"] = _initial_yaw(rec)
        _crosscheck(rec, payload, xstats)
        records.append(rec)

    # Definition tables: weapon id -> class-98 actor, effect id -> 87..94 actor.
    # Separate id spaces: weapons (0x800a2300), instanced effects 87-90/92-94
    # (0x800a7040, bank 0) and class-91 damage effect sets (0x800a0c70).
    weapon_actor, effect_actor, explosion_actor = {}, {}, {}
    for rec in records:
        if rec["class_id"] == 98:
            weapon_actor.setdefault(rec["fields"]["WeaponDefinition.weapon_id"]["raw"], rec["id"])
        elif rec["class_id"] == 91:
            explosion_actor.setdefault(rec["fields"]["DamageEffectSet.set_id"]["raw"], rec["id"])
        elif rec["class_id"] in _EFFECT_SIZES:
            effect_actor.setdefault(rec["fields"]["EffectHeader.effect_id"]["raw"], rec["id"])
    for rec in records:
        _resolve_targeting(rec)
        rec["refs"] = _collect_refs(rec, weapon_actor, effect_actor, explosion_actor)
        for p in rec["refs"].get("paths", []):
            if p not in cnet_ids:
                warnings[("Cnet missing", f"{level} cnet {p}")] += 1
    return level, records


def _summary(records):
    by = defaultdict(lambda: {"count": 0, "csac": 0, "decoded_bytes": 0, "unknown_bytes": 0})
    for r in records:
        s = by[r["class_id"]]
        s["class_name"] = r["class_name"]
        s["confidence"] = r["confidence"]
        s["count"] += 1
        s["csac"] += r["kind"] == "Csac"
        s["decoded_bytes"] += r["coverage"]["decoded_bytes"]
        s["unknown_bytes"] += r["coverage"]["unknown_bytes"]
    return {str(k): dict(v) for k, v in sorted(by.items())}


# ── initial yaw (from the per-class init code) ─────────────────────────────

# class -> (field whose low 12 bits are the BAM yaw passed to the rotation
# matrix builder 0x800188d0 / sin-cos 0x8001846c, init function), or a string
# when there is no stored yaw. See the docstring table.
_INITIAL_YAW = {
    1: ("X1Alpha.rotation", "0x800579d4"),
    6: ("BaseTurret.gun_rotation", "0x8002c584"),
    8: ("BaseTurret.gun_rotation", "0x8002c584"),
    30: ("BaseTurret.gun_rotation", "0x8002c584"),
    33: ("BaseTurret.gun_rotation", "0x8002c584"),
    36: ("BaseTurret.gun_rotation", "0x8002c584"),
    10: ("Elevator.rotation", "0x80041488"),
    11: ("DynamicProp.rotation", "0x8002dc94"),
    12: ("WalkableProp.rotation_y", "0x80041c6c"),
    16: ("ItemPickup.rotation", "0x8002fa20"),
    25: ("MoveableProp.rotation", "0x8002eed8"),
    27: ("MovingShooter.rotation", "0x8002dfc4"),
    32: ("PoliceLocker.rotation", "0x800302e8"),
    34: ("Sheet34.rotation_y", "0x80041e0c"),
    96: ("Prop.rotation_y", "0x80083a10"),
    97: ("DCSQuad.rotation_y", "0x80049c38"),
    5: "path", 20: "path", 26: "path", 28: "path", 31: "path",
    9: "random", 37: "random",
}
# Extra parts with their own yaw (sub-actors built by the init code).
_PART_YAW = {
    8: {"base": "Turret.base_rotation"},
    36: {"base": "Turret.base_rotation"},
    33: {"mid": "Turret33.mid_rotation", "base": "Turret33.base_rotation"},
}


def _initial_yaw(rec):
    """Return the initial-yaw record for an actor (see _INITIAL_YAW)."""
    cls = rec["class_id"]
    src = _INITIAL_YAW.get(cls)
    if src is None:
        return {"source": "none"}
    if isinstance(src, str):
        out = {"source": src}
        if src == "path":
            out["note"] = ("BAM = ratan2(dx, dz) from the Cnet node at the spawn "
                           "position to the next node (random at junctions) "
                           "(0x80050e0c)")
        else:
            out["note"] = "random heading at init (0x80032180)"
        return out
    fld, fn = src
    f = rec["fields"][fld]
    out = {"source": fld, "init": fn, "word": f["word"],
           "bits": "0-15" if f["off"] % 4 == 0 else "16-31",
           "bam": f["raw"] & 0xFFF, "deg": round((f["raw"] & 0xFFF) * 360.0 / 4096.0, 3)}
    if cls in (16, 96) and f["raw"] < 0:
        out["note"] = "negative: " + ("random yaw" if cls == 96 else "spinning pickup")
    parts = {}
    for part, pf in _PART_YAW.get(cls, {}).items():
        g = rec["fields"][pf]
        parts[part] = {"source": pf, "word": g["word"],
                       "bits": "0-15" if g["off"] % 4 == 0 else "16-31",
                       "bam": g["raw"] & 0xFFF}
    if parts:
        out["parts"] = parts
    return out


def _layout_bam(cls, params):
    """Reimplementation of godot/scripts/level_layout.gd rotation_y_degrees()
    word selection, to report where it disagrees with the exe."""
    p = params
    if cls == 10 and len(p) >= 9:
        return (p[8] >> 16) & 0xFFF
    if cls == 96 and p:
        return p[0] & 0xFFF
    if cls in (8, 36, 37) and len(p) >= 10:
        return p[9] & 0xFFF
    if cls in (5, 28) and len(p) >= 3:
        return p[2] & 0xFFF
    if cls == 98 and p:
        return p[0] & 0xFFF
    if cls == 11 and len(p) >= 5:
        return (p[4] >> 16) & 0xFFF
    if cls == 1 and len(p) >= 5:
        return p[4] & 0xFFF
    if cls == 9 and len(p) >= 14:
        return p[13] & 0xFFF
    if cls == 99 and len(p) >= 6:
        return p[5] & 0xFFF
    return None


def _crosscheck(rec, payload, stats):
    """Compare the exe's initial yaw with the word level_layout.gd reads."""
    cls = rec["class_id"]
    words = [struct.unpack_from("<I", payload, i)[0] for i in range(0, len(payload) - 3, 4)]
    layout = _layout_bam(cls, words)
    yaw = rec["initial_yaw"]
    if "bam" not in yaw:
        if layout is not None:
            stats[(cls, f"layout reads a word, exe: {yaw['source']}")] += 1
        return
    if layout is None:
        stats[(cls, "exe has a yaw, layout reads none")] += 1
        return
    stats[(cls, "agree" if layout == yaw["bam"] else "DISAGREE")] += 1


# ── main ───────────────────────────────────────────────────────────────────

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    parsed = os.path.join(repo_root, "parsed")

    mis_files = find_game_files("MIS")

    warnings = Counter()
    xstats = Counter()
    global_table = defaultdict(lambda: {"levels": {}, "class_name": None})
    totals = defaultdict(lambda: Counter())
    n_total = 0
    decoded = []
    for mis_path in mis_files:
        level, records = decode_level(mis_path, warnings, xstats)
        summary = _summary(records)
        decoded.append((level, summary, records))
        for cid, s in summary.items():
            g = global_table[cid]
            g["class_name"] = s["class_name"]
            g["confidence"] = s["confidence"]
            g["levels"][level] = s["count"]
            t = totals[cid]
            t["count"] += s["count"]
            t["decoded_bytes"] += s["decoded_bytes"]
            t["unknown_bytes"] += s["unknown_bytes"]

    # The global class x level table is embedded in every level file so that
    # the only outputs stay parsed/<LEVEL>/actors_decoded.json.
    class_levels = {k: global_table[k] for k in sorted(global_table, key=int)}
    for level, summary, records in decoded:
        out_dir = os.path.join(parsed, level)
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "actors_decoded.json"), "w") as f:
            json.dump({"level": level, "summary": summary, "class_levels": class_levels,
                       "actors": records}, f, indent=1)
        n_total += len(records)
        print(f"{level}.MIS → {len(records)} actors decoded")

    print(f"\nClass coverage over all levels ({n_total} actors):")
    print(f"  {'id':>3} {'name':24} {'conf':8} {'count':>5} {'decoded%':>8}  levels")
    all_dec = all_unk = 0
    for cid in sorted(totals, key=int):
        t = totals[cid]
        tot = t["decoded_bytes"] + t["unknown_bytes"]
        all_dec += t["decoded_bytes"]
        all_unk += t["unknown_bytes"]
        g = global_table[cid]
        pct = 100.0 * t["decoded_bytes"] / tot if tot else 100.0
        print(f"  {cid:>3} {g['class_name']:24} {g['confidence']:8} {t['count']:>5} "
              f"{pct:7.1f}%  {len(g['levels'])}")
    print(f"  payload bytes decoded: {all_dec}/{all_dec + all_unk} "
          f"({100.0 * all_dec / (all_dec + all_unk):.1f}%)")
    print("\nInitial yaw (exe) vs godot/scripts/level_layout.gd:")
    for (cls, what), n in sorted(xstats.items()):
        print(f"  class {cls:>3}: {what:32} {n}")
    if warnings:
        print("\nWarnings (expected-zero fields that are not, missing refs):")
        for k, n in sorted(warnings.items(), key=str):
            print(f"  {k}: {n}")


if __name__ == "__main__":
    main()
