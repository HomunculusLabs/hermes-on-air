from pathlib import Path
import tempfile
import hashlib
import io
import tarfile

import pytest

from onair import scene_scaffold


def archive(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as tar:
        for name, body in files.items():
            data = body.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return output.getvalue()


def test_scaffold_integrity_and_no_overwrite(tmp_path, monkeypatch):
    blobs = [archive({name: "import {x} from 'three';" for name in spec['files']})
             for spec in scene_scaffold.PACKAGES]
    specs = [dict(spec, sha256=hashlib.sha256(blob).hexdigest())
             for spec, blob in zip(scene_scaffold.PACKAGES, blobs)]
    monkeypatch.setattr(scene_scaffold, 'PACKAGES', specs)
    monkeypatch.setattr(scene_scaffold, '_download', lambda url: blobs[[s['url'] for s in specs].index(url)])
    dest = tmp_path / 'scene'
    scene_scaffold.scaffold(dest)
    starter = Path(scene_scaffold.__file__).parent / 'scene_starter'
    files = [p for p in starter.rglob('*') if p.is_file()]
    assert {'avatar.js', 'avatar_logic.js', 'quality.js'} <= {p.name for p in files}
    for source in files:
        assert (dest / source.relative_to(starter)).read_bytes() == source.read_bytes()
    assert (dest / 'vendor' / 'three' / 'build' / 'three.module.js').is_file()
    (dest / 'keep.json').write_text('owner')
    with pytest.raises(scene_scaffold.SceneError, match='exists'):
        scene_scaffold.scaffold(dest)
    assert (dest / 'keep.json').read_text() == 'owner'
    monkeypatch.setattr(scene_scaffold, '_download', lambda url: b'bad')
    with pytest.raises(scene_scaffold.SceneError, match='digest'):
        scene_scaffold.scaffold(dest, force=True)
    assert (dest / 'keep.json').read_text() == 'owner'
    assert not (tmp_path / 'missing').exists()
    with pytest.raises(scene_scaffold.SceneError, match='digest'):
        scene_scaffold.scaffold(tmp_path / 'missing')
    assert not (tmp_path / 'missing').exists()


def test_scaffold_refuses_symlinked_parent(tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    link = tmp_path / 'link'
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(scene_scaffold.SceneError, match='symlink'):
        scene_scaffold.scaffold(link / 'scene')
    assert not (outside / 'scene').exists()


@pytest.mark.parametrize('component', ['plugin-data', 'on-air', 'scene'])
def test_scaffold_refuses_each_linked_home_component(tmp_path, component):
    home = tmp_path / 'home'
    scene = home / 'plugin-data' / 'on-air' / 'scene'
    scene.mkdir(parents=True)
    target = {'plugin-data': home / 'plugin-data', 'on-air': home / 'plugin-data' / 'on-air', 'scene': scene}[component]
    outside = tmp_path / 'outside'
    target.rename(outside)
    target.symlink_to(outside, target_is_directory=True)
    with pytest.raises(scene_scaffold.SceneError, match='symlink'):
        scene_scaffold.scaffold(scene, force=True, home=home)
    assert outside.is_dir()
    if component == 'scene':
        assert not list(outside.iterdir())


def test_scaffold_refuses_parent_swap_before_staging(tmp_path, monkeypatch):
    blobs = [archive({name: "content" for name in spec['files']}) for spec in scene_scaffold.PACKAGES]
    specs = [dict(spec, sha256=hashlib.sha256(blob).hexdigest())
             for spec, blob in zip(scene_scaffold.PACKAGES, blobs)]
    monkeypatch.setattr(scene_scaffold, 'PACKAGES', specs)
    monkeypatch.setattr(scene_scaffold, '_download', lambda url: blobs[[s['url'] for s in specs].index(url)])
    home = tmp_path / 'home'
    parent = home / 'plugin-data' / 'on-air'
    parent.mkdir(parents=True)
    outside = tmp_path / 'outside'
    (outside / 'on-air').mkdir(parents=True)
    temporary_directory = scene_scaffold.tempfile.TemporaryDirectory
    def swap(*args, **kwargs):
        (home / 'plugin-data').rename(tmp_path / 'saved')
        (home / 'plugin-data').symlink_to(outside, target_is_directory=True)
        return temporary_directory(*args, **kwargs)
    monkeypatch.setattr(scene_scaffold.tempfile, 'TemporaryDirectory', swap)
    with pytest.raises(scene_scaffold.SceneError, match='symlink'):
        scene_scaffold.scaffold(parent / 'scene', home=home)
    assert not list((outside / 'on-air').iterdir())


def test_scene_parent_falls_back_when_descriptor_path_cannot_hold_directories(tmp_path, monkeypatch):
    """A descriptor path that lists as a directory but refuses children must not block scaffolding."""
    from onair import scene_paths
    monkeypatch.setattr(scene_paths, '_can_stage_under', lambda path: False)
    with scene_paths.scene_parent(tmp_path, ('plugin-data', 'on-air')) as parent:
        assert parent == tmp_path / 'plugin-data' / 'on-air' and parent.is_dir()
        with tempfile.TemporaryDirectory(dir=parent) as staged:
            assert Path(staged).is_dir()


def test_descriptor_probe_reports_unusable_parent(tmp_path):
    from onair import scene_paths
    assert scene_paths._can_stage_under(tmp_path) is True
    assert scene_paths._can_stage_under(tmp_path / 'missing') is False
