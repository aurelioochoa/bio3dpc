"""Landscape campaign rebuild: DAT round trips, prompt relabels and hash-pinned output."""
import dataclasses
import hashlib
from pathlib import Path

import pytest

from bio3dpc import campaign
from bio3dpc.profiles import discover_root


def pack(*records):
    return campaign.write_pack(dict(magic=0x00040001, priority=9, records=[
        dict(lead=0, name=name, link=index + 10 if index < len(records) - 1 else None, kind=3, body=body)
        for index, (name, body) in enumerate(records)]))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def test_pack_round_trip_keeps_directory_links_and_omits_the_last_one():
    data = pack((b'GMessagesLevel2b.txt', b'Press (Z) to crouch.'), (b'level.lvl', b'\x01\x02'))
    parsed = campaign.read_pack(data)
    assert [r['name'] for r in parsed['records']] == [b'GMessagesLevel2b.txt', b'level.lvl']
    assert parsed['records'][0]['link'] == 10 and parsed['records'][1]['link'] is None
    assert campaign.write_pack(parsed) == data


def test_relabel_changes_only_level_prompts_and_moves_later_blobs():
    data = pack((b'GMessagesLevel2a.txt', b'Press (W) to use. Press (Z) to crouch.'),
                (b'notes.txt', b'Press (W) here stays.'), (b'tail.bin', b'tail'))
    records = campaign.read_pack(campaign.relabel_prompts(data))['records']
    assert records[0]['body'] == b'Press (0) to use. Press (OK) to crouch.'
    assert records[1]['body'] == b'Press (W) here stays.'
    assert records[2]['body'] == b'tail'


def test_index_round_trip_matches_the_original_text_layout():
    raw = b'INDX\r\n2\r\nbio1.dat\r\n002888047\r\nbio2.dat\r\n000000012\r\n'
    assert campaign.read_index(raw) == [('bio1.dat', 2888047), ('bio2.dat', 12)]
    assert campaign.write_index(campaign.read_index(raw)) == raw
    with pytest.raises(campaign.CampaignError):
        campaign.read_index(b'NOPE\r\n')


@pytest.fixture
def synthetic(monkeypatch, tmp_path):
    """A miniature collection with its own pinned hashes."""
    nokia, vx9900, vx8350 = (tmp_path / name for name in ('nokia', 'vx9900', 'vx8350'))
    for folder in (nokia, vx9900, vx8350):
        folder.mkdir()
    starters = {'bio1.dat': pack((b'a.txt', b'one')), 'bio2.dat': pack((b'b.txt', b'two'))}
    for name, data in starters.items():
        (nokia / name).write_bytes(data)
    original_index = campaign.write_index(
        [('bio1.dat', len(starters['bio1.dat'])), ('bio2.dat', len(starters['bio2.dat'])),
         ('bio3.dat', 1), ('bio4.dat', 1)])
    (nokia / 'bio3d.idx').write_bytes(original_index)
    bio3 = pack((b'GMessagesLevel2a.txt', b'Press (W).'))
    bio4 = pack((b'gene_bank_text.txt', b'short'))
    (vx9900 / 'bio3.dat').write_bytes(bio3)
    (vx8350 / 'bio4.dat').write_bytes(bio4)
    rebuilt3 = campaign.relabel_prompts(bio3)
    recipe = (campaign.PackRecipe('bio3.dat', 'vx9900', digest(bio3), digest(rebuilt3), relabel=True),
              campaign.PackRecipe('bio4.dat', 'vx8350', digest(bio4), digest(bio4)))
    index = campaign.write_index([('bio1.dat', len(starters['bio1.dat'])), ('bio2.dat', len(starters['bio2.dat'])),
                                  ('bio3.dat', len(rebuilt3)), ('bio4.dat', len(bio4))])
    profile = dataclasses.replace(campaign.N7705, asset_hashes=tuple(
        (name, digest(data)) for name, data in starters.items()) + (('bio3d.idx', digest(original_index)),))
    monkeypatch.setattr(campaign, 'N7705', profile)
    monkeypatch.setattr(campaign, 'RECIPE', recipe)
    monkeypatch.setattr(campaign, 'CAMPAIGN_HASHES', {name: value for name, value in profile.asset_hashes
                                                      if name != 'bio3d.idx'}
                        | {r.name: r.sha256 for r in recipe} | {'bio3d.idx': digest(index)})
    return dict(nokia=nokia, vx9900=vx9900, vx8350=vx8350), tmp_path / 'out'


def test_rebuild_writes_a_verified_folder_without_touching_originals(synthetic):
    folders, output = synthetic
    before = {path: path.read_bytes() for folder in folders.values() for path in folder.iterdir()}
    assert campaign.rebuild(folders, output) == output
    assert sorted(path.name for path in output.iterdir()) == sorted(campaign.CAMPAIGN_HASHES)
    assert campaign.verify_campaign(output) == []
    sizes = dict(campaign.read_index((output / 'bio3d.idx').read_bytes()))
    assert sizes['bio3.dat'] == (output / 'bio3.dat').stat().st_size
    assert {path: path.read_bytes() for path in before} == before
    # Rebuilding again replaces only its own earlier output.
    assert campaign.rebuild(folders, output) == output


def test_rebuild_rejects_unknown_donors_and_unsafe_output(synthetic):
    folders, output = synthetic
    with pytest.raises(campaign.CampaignError, match='separate from the original'):
        campaign.rebuild(folders, folders['vx9900'])
    output.mkdir()
    (output / 'keep me.txt').write_text('user file')
    with pytest.raises(campaign.CampaignError, match='Refusing to replace'):
        campaign.rebuild(folders, output)
    assert (output / 'keep me.txt').read_text() == 'user file'
    (folders['vx9900'] / 'bio3.dat').write_bytes(b'different')
    assert any('does not match' in problem for problem in campaign.check_donors(folders))
    with pytest.raises(campaign.CampaignError):
        campaign.rebuild(folders, output.with_name('other'))
    assert not output.with_name('other').exists()


def test_real_collection_rebuilds_to_the_pinned_campaign(tmp_path):
    folders = campaign.donor_folders(discover_root())
    if campaign.check_donors(folders):
        pytest.skip('Original Nokia 7705, VX9900 and VX8350 data are not available')
    output = campaign.rebuild(folders, tmp_path / 'n7705')
    assert campaign.verify_campaign(output) == []
    nokia = dict(campaign.read_index((Path(folders['nokia']) / 'bio3d.idx').read_bytes()))
    rebuilt = dict(campaign.read_index((output / 'bio3d.idx').read_bytes()))
    # Only bio7 differs from Nokia's own index; four rebuilt packs match it exactly.
    assert {name for name in nokia if nokia[name] != rebuilt[name]} == {'bio7.dat'}
