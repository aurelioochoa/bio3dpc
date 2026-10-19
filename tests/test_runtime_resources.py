"""Runtime ownership, phone geometry and preservation of acquired game data."""
import io
import struct

import pytest

from bio3dpc.helper import HelperTable
from bio3dpc.log import RunLog
from bio3dpc.machine import ArmMachine
from bio3dpc.services import (
    AEECLSID_FILEMGR, AEECLSID_MEMASTREAM, BrewRuntime, EFAILED,
    OFM_APPEND, OFM_CREATE, OFM_READ, OFM_READWRITE, SUCCESS,
)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("BIO3DPC_AUDIO", "0")
    log = RunLog(level="error", stream=io.StringIO())
    machine = ArmMachine(log)
    helper = HelperTable(machine, log)
    helper.build()
    runtime = BrewRuntime(machine, log, helper)
    yield runtime, machine
    runtime.shutdown()
    machine.close()


def invoke(runtime, address, index):
    machine = runtime.m
    method = machine.u32(machine.u32(address) + 4 * index)
    return machine.call(method, [address])


def test_file_stays_open_until_final_release_and_reclaims_mapping(env):
    runtime, machine = env
    handle = io.BytesIO(b"payload")
    baseline = set(machine.allocations)
    baseline_traps = set(machine.traps)
    obj = runtime._new_file(handle, "save.dat", "unused")
    assert invoke(runtime, obj, 0) == 2
    mapped = runtime._file_map(machine, obj, 0, 7, 0, 0, 0)
    assert machine.read(mapped, 7) == b"payload"
    assert invoke(runtime, obj, 1) == 1
    assert not handle.closed
    assert obj in runtime.files
    assert invoke(runtime, obj, 1) == 0
    assert handle.closed
    assert obj not in runtime.files
    assert not runtime.mapped_files
    assert set(machine.allocations) == baseline
    assert set(machine.traps) == baseline_traps


def test_shutdown_closes_unreleased_files_and_is_repeatable(env):
    runtime, machine = env
    handles = [io.BytesIO(b"save") for _ in range(3)]
    for handle in handles:
        obj = runtime._new_file(handle, "save.dat", "unused")
        runtime._file_map(machine, obj, 0, 4, 0, 0, 0)
    runtime.shutdown()
    runtime.shutdown()
    assert all(handle.closed for handle in handles)
    assert not runtime.files
    assert not runtime.mapped_files


def test_native_duplicate_close_is_safe_until_callback_returns(env):
    runtime, machine = env
    handle = io.BytesIO(b'level resource')
    baseline = set(machine.allocations)
    file = runtime._new_file(handle, 'level.dat', 'unused')
    release = machine.u32(machine.u32(file) + 4)

    def native_loader(m):
        assert m.call(release, [file]) == 0
        # Nokia's native loader closes a borrowed file a second time before
        # returning. Its vtable must survive that one host-to-guest callback.
        assert m.u32(file) != 0
        assert m.call(release, [file]) == 0
        return 0

    entry = machine.bind('native loader double-close', native_loader, nargs=0)
    assert machine.call(entry) == 0
    assert handle.closed
    assert file not in runtime.objects
    assert set(machine.allocations) == baseline


def test_bitmap_final_release_frees_heap_bindings_and_borrowed_destination(env):
    runtime, machine = env
    baseline = set(machine.allocations)
    baseline_traps = set(machine.traps)
    for _ in range(10):
        bitmap = runtime._new_bitmap(320, 240)
        runtime._display_set_destination(machine, 0, bitmap)
        assert invoke(runtime, bitmap, 0) == 2
        assert invoke(runtime, bitmap, 1) == 1
        assert runtime.bitmap_geometry(bitmap)[:2] == (320, 240)
        assert invoke(runtime, bitmap, 1) == 0
        assert bitmap not in runtime.objects
        assert "destination" not in runtime.roles
        assert set(machine.allocations) == baseline
        assert set(machine.traps) == baseline_traps


def test_runtime_singletons_survive_release(env):
    runtime, machine = env
    display = runtime._new_display()
    output = machine.alloc(4)
    runtime._display_get_device_bitmap(machine, display, output)
    bitmap = machine.u32(output)
    for obj in (runtime.shell, display, bitmap):
        assert invoke(runtime, obj, 1) == 1
        assert obj in runtime.objects
    assert runtime._new_display() == display
    runtime._display_get_device_bitmap(machine, display, output)
    assert machine.u32(output) == bitmap


def test_createinstance_transfers_one_ephemeral_reference(env):
    runtime, machine = env
    output = machine.alloc(4)
    baseline = set(machine.allocations)
    assert runtime._shell_create_instance(
        machine, runtime.shell, AEECLSID_MEMASTREAM, output) == SUCCESS
    stream = machine.u32(output)
    buffer = machine.alloc(16)
    runtime._memstream_set(machine, stream, buffer, 16, 0, 0)
    assert invoke(runtime, stream, 1) == 0
    assert stream not in runtime.memstreams
    assert set(machine.allocations) == baseline
    assert runtime._shell_create_instance(
        machine, runtime.shell, AEECLSID_FILEMGR, output) == SUCCESS
    manager = machine.u32(output)
    assert runtime.refcounts[manager] == 2
    assert invoke(runtime, manager, 1) == 1


@pytest.mark.parametrize("width,height", [(176, 220), (320, 240)])
def test_runtime_geometry_reaches_device_info_and_framebuffer(env, width, height):
    existing, machine = env
    runtime = BrewRuntime(machine, existing.log, existing.helper,
                          width=width, height=height)
    try:
        info = machine.alloc(64)
        runtime._shell_get_device_info(machine, runtime.shell, info)
        assert struct.unpack("<4H", machine.read(info, 8)) == (width, height) * 2
        output = machine.alloc(4)
        runtime._display_get_device_bitmap(machine, runtime._new_display(), output)
        bitmap = machine.u32(output)
        assert runtime.bitmap_geometry(bitmap)[:4] == (width, height, width * 2, 16)
        fw, fh, rgb = runtime.snapshot_rgb()
        assert (fw, fh, len(rgb)) == (width, height, width * height * 3)
    finally:
        runtime.shutdown()


def test_save_overlay_preserves_assets_for_writes_and_file_operations(env, tmp_path):
    runtime, machine = env
    assets, saves = tmp_path / "assets", tmp_path / "saves"
    assets.mkdir()
    original = assets / "savedata.dat"
    original.write_bytes(b"original")
    runtime.data_dir, runtime.save_dir = str(assets), str(saves)
    name = machine.cstring("fs:/~/savedata.dat")
    payload = machine.cstring("CHANGED!")
    file = runtime._filemgr_open_file(machine, 0, name, OFM_READWRITE)
    assert file
    assert runtime._file_write(machine, file, payload, 8) == 8
    assert runtime._file_truncate(machine, file, 8) == SUCCESS
    runtime._file_release(machine, file)
    assert original.read_bytes() == b"original"
    assert (saves / "savedata.dat").read_bytes() == b"CHANGED!"
    file = runtime._filemgr_open_file(machine, 0, name, OFM_APPEND)
    assert runtime._file_write(machine, file, payload, 1) == 1
    runtime._file_release(machine, file)
    assert (saves / "savedata.dat").read_bytes() == b"CHANGED!C"
    file = runtime._filemgr_open_file(machine, 0, name, OFM_READ)
    assert runtime.files[file]["host"] == str(saves / "savedata.dat")
    runtime._file_release(machine, file)
    renamed = machine.cstring("fs:/~/moved.dat")
    assert runtime._filemgr_rename(machine, 0, name, renamed) == SUCCESS
    assert runtime._filemgr_remove(machine, 0, renamed) == SUCCESS
    assert original.read_bytes() == b"original"
    # Source-only mutation cannot remove or rename an acquired asset.
    assert runtime._filemgr_remove(machine, 0, name) == EFAILED
    assert runtime._filemgr_rename(machine, 0, name, renamed) == EFAILED
    created = machine.cstring("fs:/~/new-save.dat")
    file = runtime._filemgr_open_file(machine, 0, created, OFM_CREATE)
    assert file
    runtime._file_release(machine, file)
    assert (saves / "new-save.dat").exists()
    assert not (assets / "new-save.dat").exists()
