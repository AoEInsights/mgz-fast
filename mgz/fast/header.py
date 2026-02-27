"""Fast(er) parsing for recorded game headers."""
import io
import hashlib
import logging
import re
import struct
import uuid
import zlib

from mgz.util import get_version, unpack, Version, as_hex

LOGGER = logging.getLogger(__name__)
ZLIB_WBITS = -15
HEXDUMP_CONTEXT = 500


def _hexdump(data, base_offset=0, mark=None):
    """Return a hex dump string, optionally marking a specific offset with >>."""
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i+16]
        offset = base_offset + i
        marker = '>>' if mark is not None and offset <= mark < offset + 16 else '  '
        hex_part = ' '.join(f'{b:02x}' for b in chunk)
        asc_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        lines.append(f"{marker} {offset:08x}  {hex_part:<47}  {asc_part}")
    return '\n'.join(lines)
PLAYER_END = b'\xff\xff\xff\xff\xff\xff\xff\xff.\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x0b'
CLASSES = [b'\x0a', b'\x1e', b'\x46', b'\x50', b'\x14']
BLOCK_END = b'\x00\x0b'
REGEXES = {}
SKIP_OBJECTS = [
    (b'\x1e\x00\x87\x02', 252)  # 647: junk DE object, thousands per file
]


def _compile_object_search():
    """Compile regular expressions for object searching."""
    class_or = b'(' + b'|'.join(CLASSES) + b')'
    for i in range(9):
        expr = class_or + struct.pack('b', i) + b'(?!\xff\xff)(?!\x00\x00)[\x00-\xff]{4}\xff\xff\xff\xff[^\xff]'
        REGEXES[i] = re.compile(expr)


_compile_object_search()


def aoc_string(data):
    """Read AOC string."""
    length = unpack('<h', data)
    return data.read(length)


def int_prefixed_string(data):
    """Read length prefixed (4 byte) string."""
    length = unpack('<I', data)
    return data.read(length)


def de_string(data):
    """Read DE string."""
    pos = data.tell()
    got = data.read(2)
    if got != b'\x60\x0a':
        raise ValueError(f"de_string magic mismatch at pos {pos}: expected 60 0a, got {got.hex()!r}")
    length = unpack('<h', data)
    return unpack(f'<{length}s', data)


def hd_string(data):
    """Read HD string."""
    length = unpack('<h', data)
    pos = data.tell()
    got = data.read(2)
    if got != b'\x60\x0a':
        raise ValueError(f"hd_string magic mismatch at pos {pos}: expected 60 0a, got {got.hex()!r}")
    return unpack(f'<{length}s', data)


def parse_object(data, offset):
    """Parse an object."""
    class_id, object_id, instance_id, pos_x, pos_y = struct.unpack_from('<bxH14xIxff', data, offset)
    return dict(
        class_id=class_id,
        object_id=object_id,
        instance_id=instance_id,
        position=dict(
            x=pos_x,
            y=pos_y
        )
    )


def object_block(data, pos, player_number, index):
    """Parse a block of objects."""
    objects = []
    offset = None
    while True:
        if not offset:
            match = REGEXES[player_number].search(data, pos, pos + 10000)
            end = data.find(BLOCK_END, pos) - pos + len(BLOCK_END)
            if match is None:
                break
            offset = match.start() - pos
            while end + 8 < offset:
                end += data.find(BLOCK_END, pos + end) - (pos + end) + len(BLOCK_END)
        if end + 8 == offset:
            break
        pos += offset
        # Speed optimization: Skip specified fixed-length objects.
        test = data[pos:pos + 4]
        for fingerprint, offset in SKIP_OBJECTS:
            if test == fingerprint:
                break
        else:
            objects.append(dict(parse_object(data, pos), index=index))
        offset = None
        pos += 31
    return objects, pos + end


def parse_mod(header, num_players, version):
    """Parse Userpatch mod version."""
    cur = header.tell()
    name_length = unpack(f'<xx{num_players}x36x5xh', header)
    resources = unpack(f'<{name_length + 1}xIx', header)
    values = unpack(f'<{resources}f', header)
    header.seek(cur)
    if version is Version.USERPATCH15:
        number = int(values[198])
        return number // 1000, '.'.join(list(str(number % 1000)))


def parse_player(header, player_number, num_players, save):
    """Parse a player (and objects)."""
    LOGGER.debug("[parse_player] player=%d start pos=%d save=%.2f", player_number, header.tell(), save)
    rep = 9
    if save >= 61.5:
        rep = num_players
    type_, *diplomacy, name_length = unpack(f'<bx{num_players}x{rep}i5xh', header)
    LOGGER.debug("[parse_player] player=%d type=%d name_length=%d pos=%d", player_number, type_, name_length, header.tell())
    name, resources = unpack(f'<{name_length - 1}s2xIx', header)
    resources_len = 8 if save >= 63 else 4
    LOGGER.debug("[parse_player] player=%d name=%s resources=%d resources_len=%d pos=%d",
                 player_number, name, resources, resources_len, header.tell())
    header.read(resources * resources_len)
    start_x, start_y, civilization_id, color_id = unpack('<xff9xb3xbx', header)
    LOGGER.debug("[parse_player] player=%d pos=(%.1f,%.1f) civ=%d color=%d pos=%d",
                 player_number, start_x, start_y, civilization_id, color_id, header.tell())
    offset = header.tell()
    data = header.read()
    # Skips thousands of bytes that are not easy to parse.
    object_start = re.search(b'\x0b\x00.\x00\x00\x00\x02\x00\x00', data, re.DOTALL)
    if not object_start:
        raise RuntimeError("could not find object start")
    start = object_start.end()
    LOGGER.debug("[parse_player] player=%d object_start at offset+%d", player_number, start)
    objects, end = object_block(data, start, player_number, 0)
    sleeping, end = object_block(data, end, player_number, 1)
    doppel, end = object_block(data, end, player_number, 2)
    LOGGER.debug("[parse_player] player=%d objects=%d sleeping=%d doppel=%d end_offset=%d",
                 player_number, len(objects), len(sleeping), len(doppel), end)
    if data[end + 8:end + 10] == BLOCK_END:
        end += 10
    if data[end:end + 2] == BLOCK_END:
        end += 2
    header.seek(offset + end)
    LOGGER.debug("[parse_player] player=%d after objects pos=%d", player_number, header.tell())
    device = 0
    if save >= 37:
        offset = header.tell()
        data = header.read(100)
        device = data[8]
        LOGGER.debug("[parse_player] player=%d device=%d pos=%d", player_number, device, header.tell())
        # Jump to the end of player data
        player_end = re.search(PLAYER_END, data, re.DOTALL)
        if not player_end:
            # Normally this is 26 bytes in,
            # But in some cases (probably where object parsing failed),
            # it can be tens of thousands of bytes. So we have to `read()` everything
            offset = header.tell()
            data = header.read()
            player_end = re.search(PLAYER_END, data, re.DOTALL)
            if not player_end and player_number < num_players - 1:
                # this issue happens on restored games
                # only a failure if this is not the last player, since we seek to the next block anyway
                raise RuntimeError("could not find player end")
            LOGGER.debug("[parse_player] player=%d player_end fallback search (last player or restored)", player_number)
        if player_end:
            header.seek(offset + player_end.end())
            LOGGER.debug("[parse_player] player=%d sought to end marker pos=%d", player_number, header.tell())

    LOGGER.debug("[parse_player] player=%d done pos=%d", player_number, header.tell())
    return dict(
        number=player_number,
        type=type_,
        name=name,
        diplomacy=diplomacy,
        civilization_id=civilization_id,
        color_id=color_id,
        objects=objects + sleeping + doppel,
        position=dict(
            x=start_x,
            y=start_y
        )
    ), device


def parse_lobby(data, version, save):
    """Parse lobby data."""
    LOGGER.debug("[parse_lobby] start pos=%d version=%s save=%.2f", data.tell(), version, save)
    if version is Version.DE:
        data.read(5)
        LOGGER.debug("[parse_lobby] skipped 5 bytes (DE) pos=%d", data.tell())
        if save >= 20.06:
            data.read(9)
            LOGGER.debug("[parse_lobby] skipped 9 bytes (>=20.06) pos=%d", data.tell())
        if save >= 26.16:
            data.read(5)
            LOGGER.debug("[parse_lobby] skipped 5 bytes (>=26.16) pos=%d", data.tell())
        if save >= 37:
            data.read(8)
            LOGGER.debug("[parse_lobby] skipped 8 bytes (>=37) pos=%d", data.tell())
        if save >= 64.3:
            data.read(16)
            LOGGER.debug("[parse_lobby] skipped 16 bytes (>=64.3) pos=%d", data.tell())
        if save >= 66.3:
            data.read(1)
            LOGGER.debug("[parse_lobby] skipped 1 byte (>=66.3) pos=%d", data.tell())
    data.read(8)
    if version not in (Version.DE, Version.HD):
        data.read(1)
        LOGGER.debug("[parse_lobby] skipped 1 byte (non-DE/HD) pos=%d", data.tell())
    reveal_map_id, map_size, population, game_type_id, lock_teams = unpack('I4xIIbb', data)
    LOGGER.debug("[parse_lobby] reveal_map=%d map_size=%d pop=%d game_type=%d lock_teams=%d pos=%d",
                 reveal_map_id, map_size, population, game_type_id, lock_teams, data.tell())
    if version in (Version.DE, Version.HD):
        data.read(5)
        if save >= 13.13:
            data.read(4)
            LOGGER.debug("[parse_lobby] skipped 4 bytes (>=13.13) pos=%d", data.tell())
        if save >= 25.22:
            data.read(1)
            LOGGER.debug("[parse_lobby] skipped 1 byte (>=25.22) pos=%d", data.tell())
    chat_count = unpack('<I', data)
    LOGGER.debug("[parse_lobby] chat_count=%d pos=%d", chat_count, data.tell())
    chat = []
    for _ in range(0, chat_count):
        message = data.read(unpack('<I', data)).strip(b'\x00')
        if len(message) > 0:
            chat.append(message)
    seed = None
    if version is Version.DE:
        seed = unpack('<i', data)
        LOGGER.debug("[parse_lobby] seed=%d pos=%d", seed, data.tell())
    LOGGER.debug("[parse_lobby] done pos=%d", data.tell())
    return dict(
        reveal_map_id=reveal_map_id,
        map_size=map_size,
        population=population * (25 if version not in (Version.DE, Version.HD) else 1),
        game_type_id=game_type_id,
        lock_teams=lock_teams == 1,
        chat=chat,
        seed=seed
    )


def parse_map(data, version, save):
    """Parse map."""
    LOGGER.debug("[parse_map] start pos=%d version=%s save=%.2f", data.tell(), version, save)
    tile_format = '<xbbx'
    if version is Version.DE:
        if save >= 62.0:
            tile_format = '<bxxb6x'
            LOGGER.debug("[parse_map] tile_format: DE >= 62.0")
        else:
            tile_format = '<bxb6x'
            LOGGER.debug("[parse_map] tile_format: DE < 62.0")
        data.read(8)
        LOGGER.debug("[parse_map] skipped 8 bytes (DE) pos=%d", data.tell())
    size_x, size_y, zone_num = unpack('<III', data)
    tile_num = size_x * size_y
    LOGGER.debug("[parse_map] size=%dx%d zone_num=%d tile_num=%d pos=%d", size_x, size_y, zone_num, tile_num, data.tell())
    for zi in range(zone_num):
        if version in (Version.DE, Version.HD):
            data.read(2048 + (tile_num * 2))
        else:
            data.read(1275 + tile_num)
        num_floats = unpack('<I', data)
        data.read(num_floats * 4)
        data.read(4)
        LOGGER.debug("[parse_map] zone[%d] num_floats=%d pos=%d", zi, num_floats, data.tell())
    all_visible = unpack('<bx', data)
    LOGGER.debug("[parse_map] all_visible=%d, reading %d tiles pos=%d", all_visible, tile_num, data.tell())
    tiles = [unpack(tile_format, data) for _ in range(tile_num)]
    LOGGER.debug("[parse_map] after tiles pos=%d", data.tell())
    num_data = unpack('<I4x', data)
    LOGGER.debug("[parse_map] num_data=%d pos=%d", num_data, data.tell())
    data.read(num_data * 4)
    for i in range(0, num_data):
        num_obs = unpack('<I', data)
        data.read(num_obs * 8)
    x2, y2 = unpack('<II', data)
    LOGGER.debug("[parse_map] x2=%d y2=%d pos=%d", x2, y2, data.tell())
    data.read(x2 * y2 * 4)
    if save >= 61.5:
        data.read(x2 * y2 * 4)
        LOGGER.debug("[parse_map] skipped extra %d bytes (>=61.5) pos=%d", x2 * y2 * 4, data.tell())
    restore_time = unpack('<I', data)
    LOGGER.debug("[parse_map] restore_time=%d pos=%d", restore_time, data.tell())
    return dict(
        all_visible=all_visible == 1,
        restore_time=restore_time,
        dimension=size_x,
        tiles=tiles
    )


def parse_scenario(data, num_players, version, save):
    """Parse scenario section."""
    LOGGER.debug("[parse_scenario] start pos=%d version=%s save=%.2f", data.tell(), version, save)
    scenario_version = unpack('<f', data)
    data.read(4)
    LOGGER.debug("[parse_scenario] scenario_version=%.2f pos=%d", scenario_version, data.tell())
    if save >= 61.5:
        data.read(4)
        if save < 66.6:
            data.read(4)
        LOGGER.debug("[parse_scenario] after version-specific header pos=%d", data.tell())
    data.read(16 * 256)
    data.read(16 * 4)
    LOGGER.debug("[parse_scenario] after names+ids pos=%d", data.tell())
    if save >= 66.6:
        for i in range(0, 16):
            data.read(8)
            de_string(data)
            de_string(data)
            data.read(4)
        LOGGER.debug("[parse_scenario] after 66.6 player data pos=%d", data.tell())
    if save >= 61.5 and save < 66.6:
        data.read(64)
        LOGGER.debug("[parse_scenario] after 61.5 padding pos=%d", data.tell())
    if save < 66.6:
        for i in range(0, 16):
            data.read(12)
            if save >= 13.34:
                data.read(4)
            data.read(4)
        LOGGER.debug("[parse_scenario] after old player data pos=%d", data.tell())
    data.read(1)
    elapsed_time = unpack('<f', data)
    LOGGER.debug("[parse_scenario] elapsed_time=%.2f pos=%d", elapsed_time, data.tell())
    if version is Version.DE:
        data.read(64)
        LOGGER.debug("[parse_scenario] after DE 64-byte block pos=%d", data.tell())
    if save >= 66.6:
        data.read(68)
        LOGGER.debug("[parse_scenario] after 66.6 68-byte block pos=%d", data.tell())
    scenario_filename = aoc_string(data)
    LOGGER.debug("[parse_scenario] scenario_filename=%s pos=%d", scenario_filename, data.tell())
    data.read(24)
    LOGGER.debug("[parse_scenario] after message IDs pos=%d", data.tell())
    instructions = aoc_string(data)
    LOGGER.debug("[parse_scenario] instructions len=%d pos=%d", len(instructions), data.tell())
    for _ in range(0, 9):
        aoc_string(data)
    data.read(78)
    for _ in range(0, 16):
        aoc_string(data)
    data.read(196)
    LOGGER.debug("[parse_scenario] after strings+196 pos=%d", data.tell())
    for _ in range(0, 16):
        data.read(24)
        if version in (Version.DE, Version.HD):
            data.read(4)
    data.read(12672)
    LOGGER.debug("[parse_scenario] after 12672-byte block pos=%d", data.tell())
    if version is Version.DE:
        data.read(196)
        LOGGER.debug("[parse_scenario] skipped 196 bytes (DE) pos=%d", data.tell())
    else:
        for _ in range(0, 16):
            data.read(332)
        LOGGER.debug("[parse_scenario] skipped 16*332 bytes (non-DE) pos=%d", data.tell())
    if version is Version.HD:
        data.read(644)
        LOGGER.debug("[parse_scenario] skipped 644 bytes (HD) pos=%d", data.tell())
    data.read(88)
    if version is Version.HD:
        data.read(16)
        LOGGER.debug("[parse_scenario] skipped 16 bytes (HD) pos=%d", data.tell())
    map_id, difficulty_id = unpack('<II', data)
    LOGGER.debug("[parse_scenario] map_id=%d difficulty_id=%d pos=%d", map_id, difficulty_id, data.tell())
    remainder = data.read()
    if version is Version.DE:
        if save >= 66.3:
            settings_version = 4.5
        elif save >= 64.3:
            settings_version = 4.1
        elif save >= 63:
            settings_version = 3.9
        elif save >= 61.5:
            settings_version = 3.6
        elif save >= 37:
            settings_version = 3.5
        elif save >= 26.21:
            settings_version = 3.2
        elif save >= 26.16:
            settings_version = 3.0
        elif save >= 25.22:
            settings_version = 2.6
        elif save >= 25.06:
            settings_version = 2.5
        elif save >= 13.34:
            settings_version = 2.4
        else:
            settings_version = 2.2
        LOGGER.debug("[parse_scenario] seeking settings_version=%.1f in remainder of %d bytes", settings_version, len(remainder))
        end = remainder.find(struct.pack('<d', settings_version)) + 8
    else:
        end = remainder.find(b'\x9a\x99\x99\x99\x99\x99\xf9\x3f') + 13
    LOGGER.debug("[parse_scenario] settings anchor end=%d, seeking by %d", end, end - len(remainder))
    data.seek(end - len(remainder), 1)
    LOGGER.debug("[parse_scenario] after settings seek pos=%d", data.tell())

    if version is Version.DE:
        data.read(1)
        n_triggers = unpack("<I", data)
        LOGGER.debug("[parse_scenario] n_triggers=%d pos=%d", n_triggers, data.tell())

        for ti in range(n_triggers):
            data.read(22)
            data.read(4)

            description = int_prefixed_string(data)
            name = int_prefixed_string(data)
            short_description = int_prefixed_string(data)

            n_effects = unpack("<I", data)
            LOGGER.debug("[parse_scenario] trigger[%d] name=%s n_effects=%d pos=%d", ti, name, n_effects, data.tell())

            for _ in range(n_effects):
                data.read(216)

                text = int_prefixed_string(data)
                sound = int_prefixed_string(data)

            data.read(n_effects * 4)
            n_condition = unpack("<I", data)
            LOGGER.debug("[parse_scenario] trigger[%d] n_condition=%d pos=%d", ti, n_condition, data.tell())

            data.read(n_condition * 125)

        trigger_list_order = unpack(f"<{n_triggers}I", data)
        LOGGER.debug("[parse_scenario] after triggers pos=%d", data.tell())

        data.read(1032)  # default!
        LOGGER.debug("[parse_scenario] after 1032-byte default block pos=%d", data.tell())

    LOGGER.debug("[parse_scenario] done pos=%d", data.tell())
    return dict(
        map_id=map_id,
        difficulty_id=difficulty_id,
        instructions=instructions,
        scenario_filename=scenario_filename,
    )


def string_block(data):
    """Parse DE header string block."""
    strings = []
    while True:
        crc = unpack("<I", data)
        if 255 > crc > 0:
            break
        strings.append(de_string(data).decode('utf-8').split(':'))
    return strings


def parse_de(data, version, save, skip=False):
    """Parse DE-specific header."""
    LOGGER.debug("[parse_de] start pos=%d version=%s save=%.2f skip=%s", data.tell(), version, save, skip)
    if version is not Version.DE:
        LOGGER.debug("[parse_de] not DE, skipping")
        return None
    build = None
    if save >= 25.22 and not skip:
        build = unpack('<I', data)
        LOGGER.debug("[parse_de] build=%d pos=%d", build, data.tell())
    timestamp = None
    if save >= 26.16 and not skip:
        timestamp = unpack('<I', data)  # missing on console (?)
        LOGGER.debug("[parse_de] timestamp=%d pos=%d", timestamp, data.tell())
    data.read(12)
    LOGGER.debug("[parse_de] after 12-byte skip pos=%d", data.tell())
    dlc_ids = []
    dlc_count = unpack('<I', data)
    LOGGER.debug("[parse_de] dlc_count=%d pos=%d", dlc_count, data.tell())
    for i in range(0, dlc_count):
        dlc_ids.append(unpack('<I', data))
    LOGGER.debug("[parse_de] dlc_ids=%s pos=%d", dlc_ids, data.tell())
    data.read(4)
    if save >= 61.5:
        map_dimension = unpack('<I', data)
        LOGGER.debug("[parse_de] map_dimension=%d pos=%d", map_dimension, data.tell())
    else:
        difficulty_id = unpack('<I', data)
        LOGGER.debug("[parse_de] difficulty_id (pre-61.5)=%d pos=%d", difficulty_id, data.tell())
    data.read(4)
    rms_map_id = unpack('<I', data)
    LOGGER.debug("[parse_de] rms_map_id=%d pos=%d", rms_map_id, data.tell())
    data.read(4)
    victory_type_id = unpack('<I', data)
    starting_resources_id = unpack('<I', data)
    starting_age_id = unpack('<I', data)
    ending_age_id = unpack('<I', data)
    LOGGER.debug("[parse_de] victory=%d resources=%d start_age=%d end_age=%d pos=%d",
                 victory_type_id, starting_resources_id, starting_age_id, ending_age_id, data.tell())
    data.read(12)
    speed = unpack('<f', data)
    treaty_length = unpack('<I', data)
    population_limit = unpack('<I', data)
    num_players = unpack('<I', data)
    LOGGER.debug("[parse_de] speed=%.2f treaty=%d pop=%d num_players=%d pos=%d",
                 speed, treaty_length, population_limit, num_players, data.tell())
    data.read(14)
    if save >= 61.5:
        # not sure if this is difficulty under 61.5 or not
        difficulty_id = unpack('<B', data)
        LOGGER.debug("[parse_de] difficulty_id (>=61.5)=%d pos=%d", difficulty_id, data.tell())
    random_positions, all_technologies = unpack('<bb', data)
    data.read(1)
    lock_teams = unpack('<b', data)
    lock_speed = unpack('<b', data)
    multiplayer = unpack('<b', data)
    cheats = unpack('<b', data)
    record_game = unpack('<b', data)
    animals_enabled = unpack('<b', data)
    predators_enabled = unpack('<b', data)
    turbo_enabled = unpack('<b', data)
    shared_exploration = unpack('<b', data)
    team_positions = unpack('<b', data)
    LOGGER.debug("[parse_de] flags: random_pos=%d all_tech=%d lock_teams=%d lock_speed=%d multi=%d cheats=%d rec=%d pos=%d",
                 random_positions, all_technologies, lock_teams, lock_speed, multiplayer, cheats, record_game, data.tell())
    data.read(12)
    if save >= 25.06:
        data.read(1)
        LOGGER.debug("[parse_de] skipped 1 byte (>=25.06) pos=%d", data.tell())
    if save > 50:
        data.read(1)
        LOGGER.debug("[parse_de] skipped 1 byte (>50) pos=%d", data.tell())
    num_player_entries = num_players if 66.3 > save >= 37 else 8
    LOGGER.debug("[parse_de] reading %d player entries pos=%d", num_player_entries, data.tell())
    players = []
    for pi in range(num_player_entries):
        player_start = data.tell()
        data.read(4)
        color_id = unpack('<i', data)
        data.read(2)
        team_id = unpack('<b', data)
        data.read(9)
        civilization_id = unpack('<I', data)
        custom_civ_selection = None
        if save >= 61.5:
            custom_civ_count = unpack('<I', data)
            LOGGER.debug("[parse_de] player[%d] custom_civ_count=%d pos=%d", pi, custom_civ_count, data.tell())
            if save >= 63.0 and custom_civ_count > 0:
                custom_civ_selection = []
                for _ in range(custom_civ_count):
                    custom_civ_selection.append(unpack('<I', data))
        de_string(data)
        data.read(1)
        ai_name = de_string(data)
        if save >= 66.3:
            censored_name = de_string(data)
            LOGGER.debug("[parse_de] player[%d] censored_name=%s pos=%d", pi, censored_name, data.tell())
        name = de_string(data)
        type = unpack('<I', data)
        profile_id, number = unpack('<I4xi', data)
        LOGGER.debug("[parse_de] player[%d] start_pos=%d name=%s civ=%d color=%d team=%d type=%d profile=%d number=%d",
                     pi, player_start, name, civilization_id, color_id, team_id, type, profile_id, number)
        if save < 25.22:
            data.read(8)
        prefer_random = unpack('b', data)
        data.read(1)
        if save >= 25.06:
            data.read(8)
        if save >= 64.3:
            data.read(4)
            LOGGER.debug("[parse_de] player[%d] skipped 4 bytes (>=64.3) pos=%d", pi, data.tell())
        if save >= 67.2:
            _ = de_string(data)
            LOGGER.debug("[parse_de] player[%d] skipped extra de_string (>=67.2) pos=%d", pi, data.tell())

        players.append(dict(
            number=number,
            color_id=color_id,
            team_id=team_id,
            ai_name=ai_name,
            name=name,
            censored_name=censored_name if save >= 66.3 else name,
            type=type,
            profile_id=profile_id,
            civilization_id=civilization_id,
            custom_civ_selection=custom_civ_selection,
            prefer_random=prefer_random == 1
        ))
    LOGGER.debug("[parse_de] after player loop pos=%d", data.tell())
    data.read(12)
    if 66.3 > save >= 37:
        empty_slots = 8 - num_players
        LOGGER.debug("[parse_de] reading %d empty player slots pos=%d", empty_slots, data.tell())
        for _ in range(empty_slots):
            if save >= 61.5:
                data.read(4)
            data.read(12)
            de_string(data)
            data.read(1)
            de_string(data)
            de_string(data)
            data.read(38)
            if save >= 64.3:
                data.read(4)
    LOGGER.debug("[parse_de] after empty slots pos=%d", data.tell())
    data.read(4)
    rated = unpack('b', data)
    allow_specs = unpack('b', data)
    visibility = unpack('<I', data)
    hidden_civs = unpack('b', data)
    data.read(1)
    spec_delay = unpack('<I', data)
    LOGGER.debug("[parse_de] rated=%d allow_specs=%d visibility=%d hidden_civs=%d spec_delay=%d pos=%d",
                 rated, allow_specs, visibility, hidden_civs, spec_delay, data.tell())
    data.read(1)
    LOGGER.debug("[parse_de] reading string blocks pos=%d", data.tell())
    strings = string_block(data)
    data.read(8)
    for _ in range(20):
        strings += string_block(data)
    LOGGER.debug("[parse_de] after string blocks: %d strings total pos=%d", len(strings), data.tell())
    data.read(4)
    if save < 25.22:
        data.read(236)
        LOGGER.debug("[parse_de] skipped 236 bytes (<25.22) pos=%d", data.tell())
    if save >= 25.22:
        data.seek(-4, 1)
        l = unpack('<I', data)
        LOGGER.debug("[parse_de] unknown list length=%d pos=%d", l, data.tell())
        data.read(l * 4)
    unknown_entries = unpack('<Q', data)
    LOGGER.debug("[parse_de] unknown_entries (Q)=%d pos=%d", unknown_entries, data.tell())
    for _ in range(unknown_entries):
        data.read(4)
        de_string(data)
        data.read(4)
    if save >= 25.02:
        data.read(8)
        LOGGER.debug("[parse_de] skipped 8 bytes (>=25.02) pos=%d", data.tell())
    guid = data.read(16)
    LOGGER.debug("[parse_de] guid=%s pos=%d", guid.hex(), data.tell())
    lobby = de_string(data)
    LOGGER.debug("[parse_de] lobby=%s pos=%d", lobby, data.tell())
    if save >= 25.22:
        data.read(8)
        LOGGER.debug("[parse_de] skipped 8 bytes (>=25.22) pos=%d", data.tell())
    mod = de_string(data)
    LOGGER.debug("[parse_de] mod=%s pos=%d", mod, data.tell())
    data.read(33)
    if save >= 20.06:
        data.read(1)
        LOGGER.debug("[parse_de] skipped 1 byte (>=20.06) pos=%d", data.tell())
    if save >= 20.16:
        data.read(8)
        LOGGER.debug("[parse_de] skipped 8 bytes (>=20.16) pos=%d", data.tell())
    if save >= 25.06:
        data.read(21)
        LOGGER.debug("[parse_de] skipped 21 bytes (>=25.06) pos=%d", data.tell())
    if save >= 25.22:
        data.read(4)
        LOGGER.debug("[parse_de] skipped 4 bytes (>=25.22) pos=%d", data.tell())
    if save >= 26.16:
        data.read(8)
        LOGGER.debug("[parse_de] skipped 8 bytes (>=26.16) pos=%d", data.tell())
    if save >= 37:
        data.read(3)
        LOGGER.debug("[parse_de] skipped 3 bytes (>=37) pos=%d", data.tell())
    if save > 50:
        data.read(8)
        LOGGER.debug("[parse_de] skipped 8 bytes (>50) pos=%d", data.tell())
    if save >= 61.5:
        data.read(1)
        LOGGER.debug("[parse_de] skipped 1 byte (>=61.5) pos=%d", data.tell())
    if save >= 63:
        data.read(5)
        LOGGER.debug("[parse_de] skipped 5 bytes (>=63) pos=%d", data.tell())
    if save >= 66.3:
        c = unpack('<I', data)
        LOGGER.debug("[parse_de] >=66.3 extra block c=%d pos=%d", c, data.tell())
        data.read(12)
        data.read(c * 4)
    if not skip:
        de_string(data)
    if save >= 67.2:
        _ = de_string(data)
        _ = de_string(data)
    data.read(8)
    LOGGER.debug("[parse_de] after de_string+8 pos=%d", data.tell())
    if not skip and save >= 37:
        timestamp, x = unpack('<II', data)
        LOGGER.debug("[parse_de] timestamp=%d x=%d pos=%d", timestamp, x, data.tell())
    LOGGER.debug("[parse_de] done pos=%d", data.tell())
    rms_mod_id = None
    rms_filename = None
    for s in strings:
        if s[0] == 'SUBSCRIBEDMODS' and s[1] == 'RANDOM_MAPS':
            rms_mod_id = s[3].split('_')[0]
            rms_filename = s[2]
    return dict(
        players=players,
        guid=str(uuid.UUID(bytes=guid)),
        hash=hashlib.sha1(guid),
        lobby=lobby.decode('utf-8'),
        mod=mod.decode('utf-8'),
        difficulty_id=difficulty_id,
        victory_type_id=victory_type_id,
        starting_resources_id=starting_resources_id,
        starting_age_id=starting_age_id - 2 if starting_age_id > 0 else 0,
        ending_age_id=ending_age_id - 2 if ending_age_id > 0 else 0,
        speed=speed,
        population_limit=population_limit,
        treaty_length=treaty_length,
        team_together=not bool(random_positions),
        all_technologies=bool(all_technologies),
        lock_teams=bool(lock_teams),
        lock_speed=bool(lock_speed),
        multiplayer=bool(multiplayer),
        cheats=bool(cheats),
        record_game=bool(record_game),
        animals_enabled=bool(animals_enabled),
        predators_enabled=bool(predators_enabled),
        turbo_enabled=bool(turbo_enabled),
        shared_exploration=bool(shared_exploration),
        team_positions=bool(team_positions),
        build=build,
        timestamp=timestamp,
        spec_delay=spec_delay,
        rated=rated == 1,
        allow_specs=bool(allow_specs),
        hidden_civs=bool(hidden_civs),
        visibility_id=visibility,
        rms_mod_id=rms_mod_id,
        rms_map_id=rms_map_id,
        rms_filename=rms_filename,
        dlc_ids=dlc_ids
    )


def parse_hd(data, version, save):
    """Parse HD-specifc header."""
    LOGGER.debug("[parse_hd] start pos=%d version=%s save=%.2f", data.tell(), version, save)
    if version is not Version.HD or save <= 12.34:
        LOGGER.debug("[parse_hd] not HD or save<=12.34, skipping")
        return None
    data.read(12)
    dlc_count = unpack('<I', data)
    LOGGER.debug("[parse_hd] dlc_count=%d pos=%d", dlc_count, data.tell())
    data.read(dlc_count * 4)
    data.read(4)
    difficulty_id, map_id = unpack('<II', data)
    LOGGER.debug("[parse_hd] difficulty_id=%d map_id=%d pos=%d", difficulty_id, map_id, data.tell())
    data.read(80)
    players = []
    for pi in range(8):
        player_start = data.tell()
        data.read(4)
        color_id = unpack('<i', data)
        data.read(12)
        civilization_id = unpack('<I', data)
        hd_string(data)
        data.read(1)
        hd_string(data)
        name = hd_string(data)
        data.read(4)
        steam_id, number = unpack('<Qi', data)
        data.read(8)
        LOGGER.debug("[parse_hd] player[%d] start_pos=%d name=%s civ=%d color=%d number=%d",
                     pi, player_start, name, civilization_id, color_id, number)
        if name:
            players.append(dict(
                number=number,
                color_id=color_id,
                name=name,
                profile_id=steam_id,
                civilization_id=civilization_id
            ))
    LOGGER.debug("[parse_hd] after player loop pos=%d", data.tell())
    data.read(26)
    hd_string(data)
    data.read(8)
    hd_string(data)
    data.read(8)
    hd_string(data)
    data.read(8)
    guid = data.read(16)
    LOGGER.debug("[parse_hd] guid=%s pos=%d", guid.hex(), data.tell())
    lobby = hd_string(data)
    mod = hd_string(data)
    LOGGER.debug("[parse_hd] lobby=%s mod=%s pos=%d", lobby, mod, data.tell())
    data.read(8)
    hd_string(data)
    data.read(4)
    LOGGER.debug("[parse_hd] done pos=%d", data.tell())
    return dict(
        players=players,
        guid=str(uuid.UUID(bytes=guid)),
        lobby=lobby.decode('utf-8'),
        mod=mod.decode('utf-8'),
        map_id=map_id,
        difficulty_id=difficulty_id
    )


def decompress(data):
    """Decompress header bytes."""
    prefix_size = 8
    header_len, chapter_address = unpack('<II', data)
    LOGGER.debug("[decompress] header_len=%d chapter_address=%d raw_pos_after_prefix=%d", header_len, chapter_address, data.tell())
    zlib_header = data.read(header_len - prefix_size)
    LOGGER.debug("[decompress] read %d compressed bytes, raw_pos_now=%d", len(zlib_header), data.tell())
    decompressed = zlib.decompress(zlib_header, wbits=ZLIB_WBITS)
    LOGGER.debug("[decompress] decompressed to %d bytes", len(decompressed))
    return io.BytesIO(decompressed)


def parse_version(header, data):
    """Parse and compute game version."""
    LOGGER.debug("[parse_version] header_pos=%d raw_pos=%d", header.tell(), data.tell())
    log = unpack('<I', data)
    LOGGER.debug("[parse_version] log_version=%d", log)
    game, save = unpack('<7sxf', header)
    LOGGER.debug("[parse_version] game_version=%s save_version_raw=%.2f", game, save)
    if save == -1:
        save = unpack('<I', header)
        LOGGER.debug("[parse_version] new-style save int=%d", save)
        if save == 37:
            save = 37.0
        else:
            save /= (1<<16)
    version = get_version(game.decode('ascii'), round(save, 2), log)
    LOGGER.debug("[parse_version] detected version=%s save=%.2f", version, round(save, 2))
    return version, game.decode('ascii'), round(save, 2), log


def parse_players(header, num_players, version, save):
    """Parse all players."""
    LOGGER.debug("[parse_players] start pos=%d num_players=%d version=%s save=%.2f", header.tell(), num_players, version, save)
    cur = header.tell()
    gaia = b'Gaia' if version in (Version.DE, Version.HD) else b'GAIA'
    anchor = header.read().find(b'\x05\x00' + gaia + b'\x00')
    rev = 43
    if save >= 61.5:
        rev = 7 + (num_players * 4)
    target = cur + anchor - num_players - rev
    LOGGER.debug("[parse_players] gaia anchor at cur+%d, rev=%d, seeking to %d", anchor, rev, target)
    header.seek(target)
    mod = parse_mod(header, num_players, version)
    LOGGER.debug("[parse_players] mod=%s pos=%d", mod, header.tell())
    players = [parse_player(header, number, num_players, save) for number in range(num_players)]
    LOGGER.debug("[parse_players] after %d players pos=%d", num_players, header.tell())
    cur = header.tell()
    pv = b'\x00\x00\x00@'
    if save >= 61.5:
        pv = b'\x66\x66\x06\x40'
    points_version = header.read().find(pv)
    LOGGER.debug("[parse_players] points_version marker found at cur+%d", points_version)
    header.seek(cur)
    header.read(points_version)
    for pi in range(num_players):
        pver = unpack('<f', header)
        entries = unpack('<i', header)
        header.read(5 + (entries * 44))
        points = unpack('<i', header)
        header.read(8 + (points * 32))
        LOGGER.debug("[parse_players] points block[%d] pver=%.2f entries=%d points=%d pos=%d", pi, pver, entries, points, header.tell())
    LOGGER.debug("[parse_players] done pos=%d", header.tell())
    return [p[0] for p in players], mod, players[0][1]


def parse_metadata(header, save, skip_ai=True):
    """Parse recorded game metadata."""
    LOGGER.debug("[parse_metadata] start pos=%d save=%.2f", header.tell(), save)
    ai = unpack('<I', header)
    LOGGER.debug("[parse_metadata] ai=%d pos=%d", ai, header.tell())

    if ai > 0:
        LOGGER.debug("[parse_metadata] AI present, scanning for end-of-AI marker")
        if not skip_ai:
            raise RuntimeError("don't know how to parse ai")

        offset = header.tell()
        data = header.read()
        # Jump to the end of ai data
        ai_end = re.search(
            b'\00' * 4096,
            data)
        if not ai_end:
            raise RuntimeError("could not find ai end")
        header.seek(offset + ai_end.end())
        LOGGER.debug("[parse_metadata] AI end found, pos=%d", header.tell())

    game_speed, owner_id, num_players, cheats = unpack('<24xf17xhbxb', header)
    LOGGER.debug("[parse_metadata] game_speed=%.2f owner_id=%d num_players=%d cheats=%d pos=%d",
                 game_speed, owner_id, num_players, cheats, header.tell())

    if save < 61.5:
        header.read(60)
        LOGGER.debug("[parse_metadata] skipped 60 bytes (<61.5) pos=%d", header.tell())
    else:
        header.read(24 + (num_players * 4))
        LOGGER.debug("[parse_metadata] skipped %d bytes (>=61.5) pos=%d", 24 + (num_players * 4), header.tell())

    LOGGER.debug("[parse_metadata] done pos=%d", header.tell())
    return dict(
        speed=game_speed,
        owner_id=owner_id,
        cheats=cheats == 1
    ), num_players


def parse(data):
    """Parse recorded game header."""
    LOGGER.debug("[parse] start")
    try:
        header = decompress(data)
        LOGGER.debug("[parse] decompressed OK")
        version, game, save, log = parse_version(header, data)
        LOGGER.debug("[parse] version=%s game=%s save=%.2f log=%d", version, game, save, log)
        if version not in (Version.USERPATCH15, Version.DE, Version.HD):
            raise RuntimeError(f"{version} not supported")
        LOGGER.debug("[parse] calling parse_de")
        de = parse_de(header, version, save)
        LOGGER.debug("[parse] parse_de done, calling parse_hd")
        hd = parse_hd(header, version, save)
        LOGGER.debug("[parse] parse_hd done, calling parse_metadata")
        metadata, num_players = parse_metadata(header, save)
        LOGGER.debug("[parse] parse_metadata done num_players=%d, calling parse_map", num_players)
        map_ = parse_map(header, version, save)
        LOGGER.debug("[parse] parse_map done, calling parse_players")
        players, mod, device = parse_players(header, num_players, version, save)
        LOGGER.debug("[parse] parse_players done, calling parse_scenario")
        scenario = parse_scenario(header, num_players, version, save)
        LOGGER.debug("[parse] parse_scenario done, calling parse_lobby")
        lobby = parse_lobby(header, version, save)
        LOGGER.debug("[parse] parse_lobby done")
    except (struct.error, zlib.error, AssertionError, MemoryError, ValueError) as e:
        hdr = locals().get('header')
        if hdr is not None:
            fail_pos = hdr.tell()
            start = max(0, fail_pos - HEXDUMP_CONTEXT)
            hdr.seek(start)
            context_bytes = hdr.read(HEXDUMP_CONTEXT * 2)
            LOGGER.debug(
                "[parse] FAILURE at header pos=%d\n%s",
                fail_pos,
                _hexdump(context_bytes, base_offset=start, mark=fail_pos),
            )
        raise RuntimeError(f"could not parse: {e}")
    return dict(
        version=version,
        game_version=game,
        save_version=save,
        log_version=log,
        players=players,
        map=map_,
        de=de,
        hd=hd,
        mod=de.get('dlc_ids') if de else mod,
        metadata=metadata,
        scenario=scenario,
        lobby=lobby,
        device=device
    )
