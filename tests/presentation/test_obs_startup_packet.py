"""Strict startup-packet validation, the shipped fresh-root consumer and read-only status (R10 review findings 1-5).

Fixture authority, labelled: FakeCtx.session_surface() == "cli" stands in for the host owner session; FakeObs is a
loopback WebSocket double, not native OBS. Every "normal OBS root" here is a disposable simulated directory under
tmp_path; no real home or normal profile is ever used. Validation uses explicit raises, so this module is also run
under ``python -O``.
"""
import hashlib
import json
import os
import socket

import pytest

from conftest import make
from fake_obs import FakeObs
from onair.presentation import agent, obs_bootstrap as ob


def call(ctx, tool, args=None):
    return json.loads(ctx.tools[tool]["handler"](args or {}))


@pytest.fixture
def mac():
    fake = FakeObs(platform="macos").start()
    yield fake
    fake.stop()


def sdir(home):
    return home / "plugin-data" / "on-air" / "obs-startup"


def prepared(mac):
    ctx, _ = make(mac.url)
    assert call(ctx, "onair_present_publish", {"sample": True})["ok"]
    p = call(ctx, "onair_obs_setup", {"action": "prepare_startup"})
    assert p["ok"] is True and p["expected_manifest_sha256"] == p["manifest_sha256"], p
    return ctx, p


def edit_json(path, fn):
    v = json.loads(path.read_bytes())
    fn(v)
    path.write_bytes((json.dumps(v, indent=1, sort_keys=True) + "\n").encode())


def rehash(d):
    """Make a tampered payload hash-consistent again (an attacker/editor can always do this)."""
    def fix(m):
        for n in m["files"]:
            b = (d / n).read_bytes()
            m["files"][n] = {"sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b)}
    edit_json(d / ob.MANIFEST_FILE, fix)
    return hashlib.sha256((d / ob.MANIFEST_FILE).read_bytes()).hexdigest()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---- finding 3: malformed / edited packets never mint prepared readiness --------------------------------------
MANIFEST_MUTATIONS = {
    "empty_files": lambda m: m.update(files={}),
    "files_list": lambda m: m.update(files=[]),
    "wrong_bytes": lambda m: m["files"][ob.COLLECTION_FILE].update(bytes=m["files"][ob.COLLECTION_FILE]["bytes"] + 1),
    "bytes_bool": lambda m: m["files"][ob.SETTINGS_FILE].update(bytes=True),
    "extra_key": lambda m: m.update(extra=1),
    "wrong_version": lambda m: m.update(version=2),
    "version_true": lambda m: m.update(version=True),
    "settings_unbound": lambda m: m["files"].pop(ob.SETTINGS_FILE),
    "traversal_name": lambda m: m["files"].update({"../user.ini": m["files"].pop(ob.SETTINGS_FILE)}),
}


@pytest.mark.parametrize("kind", sorted(MANIFEST_MUTATIONS))
def test_malformed_manifest_is_drift_in_status_and_never_overwritten(mac, owner_home, kind):
    ctx, _ = prepared(mac)
    d = sdir(owner_home)
    edit_json(d / ob.MANIFEST_FILE, MANIFEST_MUTATIONS[kind])
    before = {n: (d / n).read_bytes() for n in ob.FILES}
    out = call(ctx, "onair_obs_setup", {"action": "configure"})
    assert out["bootstrap_required"] is True and "why" in out, out
    pkt = out["startup_packet"]
    assert pkt["prepared"] is False and pkt["drift"] is True and pkt["problem"], pkt
    assert "apply it" not in out["next_agent_action"] and "prepare_startup" in out["next_agent_action"]
    again = call(ctx, "onair_obs_setup", {"action": "prepare_startup"})
    assert again["ok"] is False and again["status"] == "drift", again
    assert {n: (d / n).read_bytes() for n in ob.FILES} == before        # not silently "updated"


@pytest.mark.parametrize("edit", ["firstrun_false", "extra_general", "wrong_collection_select", "remote_url",
                                  "second_browser", "start_order"])
def test_hash_consistent_semantic_edits_are_refused(mac, owner_home, edit):
    ctx, _ = prepared(mac)
    d = sdir(owner_home)
    s, c = d / ob.SETTINGS_FILE, d / ob.COLLECTION_FILE
    if edit == "firstrun_false":
        edit_json(s, lambda v: v["user_ini"]["General"].update(FirstRun="false"))
    elif edit == "extra_general":
        edit_json(s, lambda v: v["user_ini"]["General"].update(LastVersion="9"))
    elif edit == "wrong_collection_select":
        edit_json(s, lambda v: v["user_ini"]["Basic"].update(SceneCollection="Somebody"))
    elif edit == "remote_url":
        edit_json(s, lambda v: v["loopback_target"].update(url="http://192.0.2.7:80/present"))
    elif edit == "second_browser":
        edit_json(c, lambda v: v["sources"].append(dict(next(x for x in v["sources"] if x["id"] == "browser_source"),
                                                        name="extra", uuid="x")))
    else:
        edit_json(s, lambda v: v["start_order"].reverse())
    rehash(d)
    st = ob.status(d)
    assert st["prepared"] is False and st["drift"] is True, st
    with pytest.raises(ob.PacketError):
        ob.validate(d)


def test_unrelated_symlink_inside_packet_dir_is_refused(mac, owner_home, tmp_path):
    ctx, _ = prepared(mac)
    d = sdir(owner_home)
    (tmp_path / "foreign").mkdir()
    (d / "notes").symlink_to(tmp_path / "foreign")
    st = ob.status(d)
    assert st["prepared"] is False and st["problem"] == "symlink", st
    out = call(ctx, "onair_obs_setup", {"action": "prepare_startup"})
    assert out["ok"] is False and out["status"] == "refused_symlink" and os.listdir(tmp_path / "foreign") == []


def test_extra_regular_file_in_packet_dir_is_drift(mac, owner_home):
    ctx, _ = prepared(mac)
    (sdir(owner_home) / "extra.txt").write_text("x")
    assert ob.status(sdir(owner_home))["problem"] == "membership"


# ---- finding 5: intact but stale packet ------------------------------------------------------------------------
def test_intact_packet_for_old_url_is_stale_then_reprepare_updates(mac, owner_home):
    ctx, first = prepared(mac)
    rt = agent.runtime()
    old_url = rt.surface.url
    rt.stop_serving()
    port = free_port()
    while f":{port}/" in old_url:
        port = free_port()
    rt.port = port
    assert call(ctx, "onair_present_control", {"action": "serve"})["ok"]
    assert rt.surface.url != old_url
    d = sdir(owner_home)
    before = {n: (d / n).read_bytes() for n in ob.FILES}
    out = call(ctx, "onair_obs_setup", {"action": "configure"})
    pkt = out["startup_packet"]
    assert out["bootstrap_required"] is True and pkt["prepared"] is False and pkt["stale"] is True, pkt
    assert pkt["intact"] is True and pkt["presentation_url"] == old_url
    assert "prepare_startup" in out["next_agent_action"] and "do not apply" in out["next_agent_action"]
    assert {n: (d / n).read_bytes() for n in ob.FILES} == before           # status stays read-only
    upd = call(ctx, "onair_obs_setup", {"action": "prepare_startup"})
    assert upd["ok"] and upd["status"] == "updated" and upd["previous_manifest_sha256"] == first["manifest_sha256"]
    fresh = call(ctx, "onair_obs_setup", {"action": "configure"})["startup_packet"]
    assert fresh["prepared"] is True and not fresh.get("stale") and fresh["manifest_sha256"] == upd["manifest_sha256"]


def test_prepared_current_packet_recommends_apply_with_digest(mac, owner_home):
    ctx, p = prepared(mac)
    out = call(ctx, "onair_obs_setup", {"action": "configure"})
    assert out["startup_packet"]["prepared"] is True and out["startup_packet"]["manifest_sha256"] == p["manifest_sha256"]
    assert "manifest_sha256" in out["next_agent_action"] and "FRESH OBS" in out["next_agent_action"]


# ---- finding 4: registration/status/refusals create nothing (actual host helpers when importable) -------------
def test_default_dir_is_derived_without_creating(owner_home):
    d = ob.default_dir()
    assert d == owner_home / "plugin-data" / "on-air" / "obs-startup"
    assert ob.status()["prepared"] is False
    assert not (owner_home / "plugin-data").exists()


def test_host_helpers_present_and_plugin_data_dir_would_create(owner_home):
    """Records that this run had the REAL host helpers (not a passing-because-import-failed run)."""
    storage = pytest.importorskip("plugins.plugin_storage")
    hc = pytest.importorskip("hermes_constants")
    assert hc.get_hermes_home() == owner_home and not (owner_home / "plugin-data").exists()
    probe = owner_home / "probe-home"
    os.environ["HERMES_HOME"] = str(probe)
    try:
        storage.plugin_data_dir("on-air")
        assert (probe / "plugin-data" / "on-air").is_dir()    # the hazard the R10 default_dir() inherited
    finally:
        os.environ["HERMES_HOME"] = str(owner_home)


def test_registration_status_inspect_unserved_and_non_owner_create_nothing(mac, owner_home):
    ctx, _ = make(mac.url)
    assert call(ctx, "onair_obs_setup", {"action": "inspect"})["ok"]
    assert call(ctx, "onair_obs_setup", {"action": "prepare_startup"})["ok"] is False   # unserved
    ob.status()
    for surface in ("telegram", "on_air", ""):
        c2, _ = make(mac.url, surface=surface)
        assert call(c2, "onair_obs_setup", {"action": "prepare_startup"})["refused"] is True
    assert not (owner_home / "plugin-data").exists()


def test_plugin_data_ancestor_link_is_never_followed(mac, owner_home, tmp_path):
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (owner_home / "plugin-data").symlink_to(foreign)
    ctx, _ = make(mac.url, surface="telegram")                       # registration + refused caller
    assert call(ctx, "onair_obs_setup", {"action": "prepare_startup"})["refused"] is True
    ctx, _ = make(mac.url)
    st = ob.status()
    assert st["prepared"] is False and st["problem"] == "symlink"
    assert call(ctx, "onair_present_publish", {"sample": True})["ok"]
    out = call(ctx, "onair_obs_setup", {"action": "prepare_startup"})
    assert out["ok"] is False and out["status"] == "refused_symlink"
    cfg = call(ctx, "onair_obs_setup", {"action": "configure"})
    assert cfg["bootstrap_required"] is True and cfg["startup_packet"]["prepared"] is False
    assert os.listdir(foreign) == []


# ---- findings 1 + 2: the shipped consumer ------------------------------------------------------------------------
@pytest.fixture
def pkt(mac, owner_home):
    _, p = prepared(mac)
    return sdir(owner_home), p["expected_manifest_sha256"]


@pytest.fixture
def normal(tmp_path):
    n = tmp_path / "simulated-normal-obs"          # disposable stand-in for a normal OBS root
    n.mkdir()
    (n / "global.ini").write_text("sentinel")
    return n


def test_consumer_ordinary_apply_writes_prestart_and_exact_collection(pkt, tmp_path, normal):
    d, sha = pkt
    target = tmp_path / "owned-obs"
    out = ob.apply(d, target, sha, prohibited_roots=[normal])
    assert out["ok"] is True and out["applied"] == "files_written_obs_not_started", out
    assert (target / "user.ini").read_text() == ("[General]\nFirstRun=true\n[Basic]\nSceneCollection=On Air startup\n"
                                                 "SceneCollectionFile=OnAirStartup\n")
    assert (target / "basic/scenes" / ob.COLLECTION_FILE).read_bytes() == (d / ob.COLLECTION_FILE).read_bytes()
    assert sorted(str(p.relative_to(target)) for p in target.rglob("*")) == [
        "basic", "basic/scenes", "basic/scenes/OnAirStartup.json", "user.ini"]
    assert out["manifest_sha256"] == sha and (normal / "global.ini").read_text() == "sentinel"


def _assert_refused(out, target, problem=None):
    assert out["ok"] is False and out["applied"] is False and out["status"] == "refused", out
    if problem:
        assert out["problem"] == problem, out
    assert not os.path.lexists(target)


def test_consumer_refuses_unbound_settings_from_review_probe(pkt, tmp_path):
    d, sha = pkt
    edit_json(d / ob.MANIFEST_FILE, lambda m: m["files"].pop(ob.SETTINGS_FILE))
    edit_json(d / ob.SETTINGS_FILE, lambda v: v["user_ini"]["General"].update(FirstRun="false"))
    new = hashlib.sha256((d / ob.MANIFEST_FILE).read_bytes()).hexdigest()
    for expect in (sha, new):
        _assert_refused(ob.apply(d, tmp_path / "fresh", expect), tmp_path / "fresh")


def test_consumer_refuses_hash_consistent_firstrun_false_even_when_digest_matches(pkt, tmp_path):
    d, _ = pkt
    edit_json(d / ob.SETTINGS_FILE, lambda v: v["user_ini"]["General"].update(FirstRun="false"))
    _assert_refused(ob.apply(d, tmp_path / "fresh", rehash(d)), tmp_path / "fresh", "prestart_unbound")


@pytest.mark.parametrize("expect", [None, "", "0" * 64, "XYZ"])
def test_consumer_requires_the_producers_manifest_digest(pkt, tmp_path, expect):
    d, _ = pkt
    _assert_refused(ob.apply(d, tmp_path / "fresh", expect), tmp_path / "fresh", "unbound")


@pytest.mark.parametrize("kind", sorted(MANIFEST_MUTATIONS))
def test_consumer_refuses_malformed_manifests(pkt, tmp_path, kind):
    d, _ = pkt
    edit_json(d / ob.MANIFEST_FILE, MANIFEST_MUTATIONS[kind])
    new = hashlib.sha256((d / ob.MANIFEST_FILE).read_bytes()).hexdigest()
    _assert_refused(ob.apply(d, tmp_path / "fresh", new), tmp_path / "fresh")


def test_consumer_refuses_packet_file_symlink_and_payload_edit(pkt, tmp_path):
    d, sha = pkt
    (d / ob.COLLECTION_FILE).write_bytes((d / ob.COLLECTION_FILE).read_bytes() + b" ")
    _assert_refused(ob.apply(d, tmp_path / "a", sha), tmp_path / "a", "size_mismatch")
    real = tmp_path / "real.json"
    real.write_bytes(b"{}")
    (d / ob.COLLECTION_FILE).unlink()
    (d / ob.COLLECTION_FILE).symlink_to(real)
    _assert_refused(ob.apply(d, tmp_path / "b", sha), tmp_path / "b", "symlink")


def test_consumer_refuses_symlink_ancestor_target(pkt, tmp_path):
    d, sha = pkt
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (tmp_path / "alias").symlink_to(foreign)
    out = ob.apply(d, tmp_path / "alias" / "fresh", sha)
    assert out["ok"] is False and out["problem"] == "unsafe_target" and os.listdir(foreign) == []


def test_consumer_refuses_normal_root_and_descendants(pkt, tmp_path, normal):
    d, sha = pkt
    for target in (normal, normal / "fresh-child", normal / "basic" / "x"):
        out = ob.apply(d, target, sha, prohibited_roots=[normal])
        assert out["ok"] is False and out["status"] == "refused", (target, out)
    assert sorted(os.listdir(normal)) == ["global.ini"] and (normal / "global.ini").read_text() == "sentinel"


def test_consumer_refuses_link_to_normal_root_and_parent_of_normal(pkt, tmp_path, normal):
    d, sha = pkt
    (tmp_path / "via").symlink_to(normal)
    for target in (tmp_path / "via" / "fresh",):
        assert ob.apply(d, target, sha, prohibited_roots=[normal])["problem"] == "unsafe_target"
    assert sorted(os.listdir(normal)) == ["global.ini"]


@pytest.mark.parametrize("bad", ["relative/fresh", "{tmp}/x/../fresh", "{tmp}/missing-parent/fresh"])
def test_consumer_refuses_relative_traversal_and_missing_parent(pkt, tmp_path, bad):
    d, sha = pkt
    out = ob.apply(d, bad.format(tmp=tmp_path), sha)
    assert out["ok"] is False and out["problem"] == "unsafe_target", out
    assert not (tmp_path / "fresh").exists() and not (tmp_path / "missing-parent").exists()


def test_consumer_refuses_existing_target_keeping_sentinel(pkt, tmp_path):
    d, sha = pkt
    t = tmp_path / "exists"
    t.mkdir()
    (t / "s").write_text("keep")
    out = ob.apply(d, t, sha)
    assert out["ok"] is False and out["problem"] == "unsafe_target" and (t / "s").read_text() == "keep"


def test_normal_roots_default_covers_macos_and_linux(tmp_path):
    assert ob.normal_obs_roots(tmp_path) == [tmp_path / "Library/Application Support/obs-studio",
                                             tmp_path / ".config/obs-studio"]
