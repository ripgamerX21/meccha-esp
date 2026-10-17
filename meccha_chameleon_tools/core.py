#!/usr/bin/env python3
"""Core game reading engine for MECCA CHAMELEON (UE5.6) ESP.

Memory primitives, pattern scanning, FName resolution, object array,
offset resolution, and game state reading. No camouflage bridge.
"""
import struct
import math

import pymem


# ---------------------------------------------------------------------------
# Bootstrap offsets
# ---------------------------------------------------------------------------
OFFSETS = {
    "UObjectBase::ClassPrivate": 0x10,
    "UObjectBase::NamePrivate": 0x18,
    "UObjectBase::OuterPrivate": 0x20,
    "UStruct::SuperStruct": 0x40,
    "UStruct::ChildProperties": 0x50,
    "FField::Next": 0x18,
    "FField::NamePrivate": 0x20,
    "FProperty::Offset_Internal": 0x44,
    "UField::Next": 0x28,
    "UStruct::Children": 0x48,
    "FCameraCacheEntry::POV": 0x10,
    "FMinimalViewInfo::Location": 0x0,
    "FMinimalViewInfo::Rotation": 0x18,
    "FMinimalViewInfo::FOV": 0x30,
}


# ---------------------------------------------------------------------------
# Memory primitives
# ---------------------------------------------------------------------------
def rp(pm, addr):
    try:
        return struct.unpack("<Q", pm.read_bytes(addr, 8))[0]
    except Exception:
        return 0


def ru32(pm, addr):
    try:
        return struct.unpack("<I", pm.read_bytes(addr, 4))[0]
    except Exception:
        return 0


def ru16(pm, addr):
    try:
        return struct.unpack("<H", pm.read_bytes(addr, 2))[0]
    except Exception:
        return 0


def rfloat(pm, addr):
    try:
        return struct.unpack("<f", pm.read_bytes(addr, 4))[0]
    except Exception:
        return 0.0


def rvec3(pm, addr):
    try:
        return struct.unpack("<ddd", pm.read_bytes(addr, 24))
    except Exception:
        return (0.0, 0.0, 0.0)


def rvec3_f(pm, addr):
    try:
        return struct.unpack("<fff", pm.read_bytes(addr, 12))
    except Exception:
        return (0.0, 0.0, 0.0)


def read_array(pm, addr):
    try:
        data = rp(pm, addr)
        count = ru32(pm, addr + 8)
        return data, count
    except Exception:
        return 0, 0


def dist(a, b):
    return math.sqrt(
        (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2
    )


# ---------------------------------------------------------------------------
# Pattern scanner
# ---------------------------------------------------------------------------
class PatternScanner:
    CHUNK_SIZE = 0x200000

    def __init__(self, pm, module_name):
        self.pm = pm
        self.module = pymem.process.module_from_name(pm.process_handle, module_name)
        if not self.module:
            raise RuntimeError(f"Module {module_name} not found")
        self.base = self.module.lpBaseOfDll
        self.size = self.module.SizeOfImage

    def _match_at(self, data, offset, pattern, mask):
        for j in range(len(pattern)):
            if mask[j] and data[offset + j] != pattern[j]:
                return False
        return True

    def scan_all(self, pattern, mask):
        pat_len = len(pattern)
        if pat_len == 0 or self.size == 0:
            return
        step = self.CHUNK_SIZE
        for start in range(0, self.size, step):
            end = min(start + step + pat_len, self.size)
            read_size = end - start
            try:
                data = self.pm.read_bytes(self.base + start, read_size)
            except Exception:
                continue
            scan_len = len(data) - pat_len
            for i in range(scan_len):
                if self._match_at(data, i, pattern, mask):
                    yield self.base + start + i

    def scan(self, pattern, mask):
        for addr in self.scan_all(pattern, mask):
            return addr
        return 0


# ---------------------------------------------------------------------------
# FName resolution
# ---------------------------------------------------------------------------
class FNameResolver:
    BLOCK_TABLE_OFFSETS = (
        0x8, 0x10, 0x18, 0x20, 0x28, 0x30, 0x38,
        0x40, 0x48, 0x50, 0x58, 0x60, 0x68, 0x70,
    )

    def __init__(self, pm, fname_pool):
        self.pm = pm
        self.fname_pool = fname_pool
        self.block_table_off = 0x10
        self.header_style = "ue5"
        self._detect_layout()

    def _read_entry(self, entry_id, table_off, style):
        block_idx = entry_id >> 16
        within = (entry_id & 0xFFFF) << 1
        block_addr = rp(self.pm, self.fname_pool + table_off + block_idx * 8)
        if not block_addr:
            return None
        hdr = ru16(self.pm, block_addr + within)
        if style == "ue4":
            is_wide = hdr & 1
            length = hdr >> 1
        elif style == "custom":
            is_wide = hdr & 1
            length = (hdr >> 6) & 0x3FF
        else:
            length = hdr & 0x3FF
            is_wide = (hdr >> 10) & 1
        if length == 0 or length > 512:
            return None
        if is_wide:
            raw = self.pm.read_bytes(block_addr + within + 2, length * 2)
            return raw.decode("utf-16-le", errors="ignore")
        raw = self.pm.read_bytes(block_addr + within + 2, length)
        return raw.decode("latin-1")

    def _detect_layout(self):
        for off in self.BLOCK_TABLE_OFFSETS:
            for style in ("custom", "ue5", "ue4"):
                try:
                    if self._read_entry(0, off, style) == "None":
                        self.block_table_off = off
                        self.header_style = style
                        return
                except Exception:
                    continue

    def resolve(self, entry_id):
        try:
            name = self._read_entry(entry_id, self.block_table_off, self.header_style)
            if name is not None:
                return name
        except Exception:
            pass
        for off in self.BLOCK_TABLE_OFFSETS:
            for style in ("custom", "ue5", "ue4"):
                if off == self.block_table_off and style == self.header_style:
                    continue
                try:
                    name = self._read_entry(entry_id, off, style)
                    if name is not None:
                        self.block_table_off = off
                        self.header_style = style
                        return name
                except Exception:
                    continue
        return None


# ---------------------------------------------------------------------------
# UE Object array
# ---------------------------------------------------------------------------
class UObjectArray:
    def __init__(self, pm, guobject_array, fname_pool):
        self.pm = pm
        self.guobject_array = guobject_array
        self.fnames = FNameResolver(pm, fname_pool)
        self._meta_class_addr = None
        self._class_cache = {}

    def obj_name(self, obj):
        return self.fnames.resolve(ru32(self.pm, obj + OFFSETS["UObjectBase::NamePrivate"]))

    def obj_class(self, obj):
        return rp(self.pm, obj + OFFSETS["UObjectBase::ClassPrivate"])

    def class_name(self, obj):
        if not obj:
            return ""
        cls = self.obj_class(obj)
        return self.obj_name(cls) if cls else ""

    def iter_objects(self):
        ptr = rp(self.pm, self.guobject_array + 0x10)
        if not ptr:
            return
        for chunk_idx in range(64):
            chunk = rp(self.pm, ptr + chunk_idx * 8)
            if not chunk:
                break
            for within in range(0x10000):
                obj = rp(self.pm, chunk + within * 0x18)
                if obj:
                    yield obj

    def _meta_class(self):
        if self._meta_class_addr is None or not self._meta_class_addr:
            for obj in self.iter_objects():
                if self.obj_name(obj) == "Class":
                    self._meta_class_addr = obj
                    break
        return self._meta_class_addr

    def find_class(self, name):
        cached = self._class_cache.get(name)
        if cached:
            if self.obj_name(cached) == name:
                return cached
            del self._class_cache[name]
        meta = self._meta_class()
        if not meta:
            return 0
        for obj in self.iter_objects():
            if self.obj_class(obj) == meta and self.obj_name(obj) == name:
                self._class_cache[name] = obj
                return obj
        return 0

    def find_first_instance(self, class_name, skip_default=True):
        cls = self.find_class(class_name)
        if not cls:
            return 0
        for obj in self.iter_objects():
            if self.obj_class(obj) == cls:
                name = self.obj_name(obj)
                if skip_default and name and name.startswith("Default__"):
                    continue
                return obj
        return 0


# ---------------------------------------------------------------------------
# Offset resolver
# ---------------------------------------------------------------------------
class OffsetResolver:
    def __init__(self, pm, objects):
        self.pm = pm
        self.objects = objects
        self.cache = dict(OFFSETS)

    def field_name(self, field):
        return self.objects.fnames.resolve(
            ru32(self.pm, field + self.cache["FField::NamePrivate"])
        )

    def _resolve_on_class(self, cls, prop_name):
        prop = rp(self.pm, cls + self.cache["UStruct::ChildProperties"])
        depth = 0
        while prop and depth < 512:
            name = self.field_name(prop)
            if name == prop_name:
                return ru32(self.pm, prop + self.cache["FProperty::Offset_Internal"])
            prop = rp(self.pm, prop + self.cache["FField::Next"])
            depth += 1
        return None

    def resolve(self, class_name, prop_name):
        key = f"{class_name}::{prop_name}"
        if key in self.cache:
            return self.cache[key]
        cls = self.objects.find_class(class_name)
        if not cls:
            return None
        offset = self._resolve_on_class(cls, prop_name)
        seen = {cls}
        while offset is None:
            super_cls = rp(self.pm, cls + self.cache["UStruct::SuperStruct"])
            if not super_cls or super_cls in seen:
                break
            seen.add(super_cls)
            offset = self._resolve_on_class(super_cls, prop_name)
            cls = super_cls
        if offset is not None:
            self.cache[key] = offset
        return offset

    def resolve_map(self, mapping):
        out = {}
        for key, (cls, prop) in mapping.items():
            val = self.resolve(cls, prop)
            if val is None:
                raise RuntimeError(f"Could not resolve offset {key} ({cls}.{prop})")
            out[key] = val
        return out


# ---------------------------------------------------------------------------
# Game reader
# ---------------------------------------------------------------------------
class MecchaESP:
    PROCESS_NAME = "PenguinHotel-Win64-Shipping.exe"
    MODULE_NAME = "PenguinHotel-Win64-Shipping.exe"

    GUOBJECT_SIG = bytes([
        0x48, 0x8D, 0x05, 0x00, 0x00, 0x00, 0x00,
        0x48, 0x89, 0x01, 0x45, 0x8B, 0xD1,
    ])
    GUOBJECT_MASK = bytes([1, 1, 1, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1])

    FNAMEPOOL_PATTERNS = (
        (bytes([0x48, 0x8D, 0x0D, 0x00, 0x00, 0x00, 0x00,
                0xE8, 0x00, 0x00, 0x00, 0x00,
                0x4C, 0x8B, 0xC0]),
         bytes([1, 1, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 1, 1])),
        (bytes([0x48, 0x8D, 0x0D, 0x00, 0x00, 0x00, 0x00,
                0xE8, 0x00, 0x00, 0x00, 0x00,
                0x48, 0x8B]),
         bytes([1, 1, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 1])),
        (bytes([0x48, 0x8D, 0x35, 0x00, 0x00, 0x00, 0x00]),
         bytes([1, 1, 1, 0, 0, 0, 0])),
        (bytes([0x48, 0x8D, 0x3D, 0x00, 0x00, 0x00, 0x00]),
         bytes([1, 1, 1, 0, 0, 0, 0])),
    )
    FNAMEPOOL_DELTA = 0xE3B40

    OFFSET_MAP = {
        "UWorld::GameState": ("World", "GameState"),
        "UWorld::OwningGameInstance": ("World", "OwningGameInstance"),
        "UGameInstance::LocalPlayers": ("GameInstance", "LocalPlayers"),
        "UPlayer::PlayerController": ("Player", "PlayerController"),
        "UEngine::GameViewport": ("Engine", "GameViewport"),
        "UGameViewportClient::World": ("GameViewportClient", "World"),
        "AGameStateBase::PlayerArray": ("GameStateBase", "PlayerArray"),
        "APlayerState::PawnPrivate": ("PlayerState", "PawnPrivate"),
        "AController::PlayerState": ("Controller", "PlayerState"),
        "APlayerController::AcknowledgedPawn": ("PlayerController", "AcknowledgedPawn"),
        "APlayerController::PlayerCameraManager": ("PlayerController", "PlayerCameraManager"),
        "APlayerCameraManager::CameraCachePrivate": ("PlayerCameraManager", "CameraCachePrivate"),
        "AActor::RootComponent": ("Actor", "RootComponent"),
        "USceneComponent::RelativeLocation": ("SceneComponent", "RelativeLocation"),
    }

    def __init__(self):
        self.pm = pymem.Pymem(self.PROCESS_NAME)
        self.guobject_array = self._scan_guobject_array()
        if not self.guobject_array:
            raise RuntimeError("Could not find GUObjectArray via pattern scan")
        self.fname_pool = self._scan_fname_pool()
        if not self.fname_pool:
            raise RuntimeError("Could not find FNamePool")
        self.objects = UObjectArray(self.pm, self.guobject_array, self.fname_pool)
        self.resolver = OffsetResolver(self.pm, self.objects)
        self.offsets = self.resolver.resolve_map(self.OFFSET_MAP)
        for key in ("FCameraCacheEntry::POV", "FMinimalViewInfo::Location",
                     "FMinimalViewInfo::Rotation", "FMinimalViewInfo::FOV",
                     "UStruct::ChildProperties", "FField::Next",
                     "FProperty::Offset_Internal", "FField::NamePrivate"):
            self.offsets[key] = OFFSETS[key]
        self.gengine = self.objects.find_first_instance("GameEngine")
        if not self.gengine:
            raise RuntimeError("Could not find GEngine instance")

    def _scan_guobject_array(self):
        scanner = PatternScanner(self.pm, self.MODULE_NAME)
        addr = scanner.scan(self.GUOBJECT_SIG, self.GUOBJECT_MASK)
        if not addr:
            return 0
        rel = struct.unpack("<i", self.pm.read_bytes(addr + 3, 4))[0]
        return addr + 7 + rel

    def _scan_fname_pool(self):
        delta_candidate = self.guobject_array - self.FNAMEPOOL_DELTA
        if self._verify_fname_pool(delta_candidate):
            return delta_candidate
        scanner = PatternScanner(self.pm, self.MODULE_NAME)
        for sig, mask in self.FNAMEPOOL_PATTERNS:
            for addr in scanner.scan_all(sig, mask):
                rel = struct.unpack("<i", self.pm.read_bytes(addr + 3, 4))[0]
                candidate = addr + 7 + rel
                if self._verify_fname_pool(candidate):
                    return candidate
        return delta_candidate

    def _verify_fname_pool(self, pool_addr):
        resolver = FNameResolver(self.pm, pool_addr)
        return resolver.resolve(0) == "None"

    def _get_world(self):
        viewport = rp(self.pm, self.gengine + self.offsets["UEngine::GameViewport"])
        if not viewport:
            return 0
        return rp(self.pm, viewport + self.offsets["UGameViewportClient::World"])

    def _get_local_controller(self, world):
        if not world:
            return 0
        gi = rp(self.pm, world + self.offsets["UWorld::OwningGameInstance"])
        if not gi:
            return 0
        lp_data, lp_count = read_array(self.pm, gi + self.offsets["UGameInstance::LocalPlayers"])
        if not lp_data or lp_count == 0:
            return 0
        local_player = rp(self.pm, lp_data)
        if not local_player:
            return 0
        return rp(self.pm, local_player + self.offsets["UPlayer::PlayerController"])

    def get_camera(self):
        world = self._get_world()
        if not world:
            return None
        pc = self._get_local_controller(world)
        if not pc:
            return None
        cam = rp(self.pm, pc + self.offsets["APlayerController::PlayerCameraManager"])
        if not cam:
            return None
        cc = cam + self.offsets["APlayerCameraManager::CameraCachePrivate"]
        pov = cc + self.offsets["FCameraCacheEntry::POV"]
        loc = rvec3(self.pm, pov + self.offsets["FMinimalViewInfo::Location"])
        rot = rvec3(self.pm, pov + self.offsets["FMinimalViewInfo::Rotation"])
        fov = rfloat(self.pm, pov + self.offsets["FMinimalViewInfo::FOV"])
        return {"loc": loc, "rot": rot, "fov": fov}

    def get_actor_root_pos(self, actor):
        root = rp(self.pm, actor + self.offsets["AActor::RootComponent"])
        if not root:
            return None
        return rvec3(self.pm, root + self.offsets["USceneComponent::RelativeLocation"])

    def get_actor_root_rotation(self, actor):
        root = rp(self.pm, actor + self.offsets["AActor::RootComponent"])
        if not root:
            return None
        return rvec3_f(self.pm, root + 0x80)

    def _detect_role(self, pawn):
        try:
            name = self.objects.class_name(pawn)
            if "Hunter" in name:
                return "Hunter", True, False
            if "Survivor" in name:
                return "Survivor", False, True
        except Exception:
            pass
        return "Unknown", False, False

    def get_invincible(self, actor):
        try:
            if ru32(self.pm, actor + 0x174) == 1:
                return True
            if ru32(self.pm, actor + 0x1D8) == 1:
                return True
        except Exception:
            pass
        return False

    def iter_actors(self, max_actors=500, class_filter=None):
        world = self._get_world()
        if not world:
            return
        count = 0
        for obj in self.objects.iter_objects():
            if count >= max_actors:
                break
            try:
                cls_name = self.objects.class_name(obj)
                if not cls_name or cls_name.startswith("Default__"):
                    continue
                if class_filter and class_filter not in cls_name:
                    continue
                root = rp(self.pm, obj + self.offsets.get("AActor::RootComponent", 0))
                if not root:
                    continue
                pos = rvec3(self.pm, root + self.offsets.get("USceneComponent::RelativeLocation", 0))
                if pos is None or (pos[0] == 0 and pos[1] == 0 and pos[2] == 0):
                    continue
                count += 1
                yield {"actor": obj, "pos": pos, "class_name": cls_name}
            except Exception:
                continue

    def iter_players(self, include_local=True, team_filter=False, enemy_only=False):
        world = self._get_world()
        if not world:
            return
        gs = rp(self.pm, world + self.offsets.get("UWorld::GameState", 0))
        if not gs:
            return
        pa_data, pa_count = read_array(self.pm, gs + self.offsets["AGameStateBase::PlayerArray"])
        if not pa_data or pa_count == 0:
            return
        local_pc = self._get_local_controller(world)
        local_pawn = 0
        if local_pc:
            local_pawn = rp(self.pm, local_pc + self.offsets["APlayerController::AcknowledgedPawn"])
        local_role, local_is_hunter, local_is_survivor = "Unknown", False, False
        if local_pawn:
            local_role, local_is_hunter, local_is_survivor = self._detect_role(local_pawn)
        seen = set()
        for i in range(pa_count):
            ps = rp(self.pm, pa_data + i * 8)
            if not ps or ps in seen:
                continue
            seen.add(ps)
            pawn = rp(self.pm, ps + self.offsets["APlayerState::PawnPrivate"])
            if not pawn:
                continue
            if not include_local and pawn == local_pawn:
                continue
            pos = self.get_actor_root_pos(pawn)
            if pos is None:
                continue
            role, is_hunter, is_survivor = self._detect_role(pawn)
            is_enemy = False
            if local_is_hunter and is_survivor:
                is_enemy = True
            elif local_is_survivor and is_hunter:
                is_enemy = True
            if enemy_only and not is_enemy:
                continue
            yield {
                "is_local": pawn == local_pawn,
                "pos": pos,
                "actor": pawn,
                "player_state": ps,
                "idx": i,
                "role": role,
                "is_hunter": is_hunter,
                "is_survivor": is_survivor,
                "is_enemy": is_enemy,
            }

    def cleanup(self):
        """No-op — camouflage bridge removed in this build."""
        pass
