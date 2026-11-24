from datetime import date, datetime

import pytest

from scripts.collect_gfs_history import KST, UTC, FIELDS, run_for_day, source_url, select_messages


def test_new_year_run_and_postgres_timezone_url():
    run = run_for_day(date(2022, 1, 1))
    assert run == datetime(2021, 12, 30, 12, tzinfo=UTC)
    target = datetime(2022, 1, 1, 6, tzinfo=KST)
    assert (target - run).total_seconds() / 3600 == 33
    assert source_url(run.astimezone(KST), 33).endswith(
        'gfs.20211230/12/atmos/gfs.t12z.pgrb2.0p25.f033')
    cutoff = datetime(2021, 12, 31, 8, tzinfo=KST)
    assert (cutoff - run).total_seconds() / 3600 == 11


def test_inventory_chooses_surface_fields_and_retains_intervals():
    rows = ['1:0:d=2021123012:TMP:2 mb:33 hour fcst:']
    for i, (name, (variable, level)) in enumerate(FIELDS.items(), 2):
        period = '30-33 hour acc fcst' if name == 'precipitation' else '33 hour fcst'
        rows.append(f'{i}:{i*100}:d=2021123012:{variable}:{level}:{period}:')
    rows += ['9:900:d=2021123012:APCP:surface:0-33 hour acc fcst:',
             '10:1000:d=2021123012:TCDC:entire atmosphere:30-33 hour ave fcst:',
             '11:1100:d=2021123012:OTHER:surface:33 hour fcst:']
    selected = select_messages('\n'.join(rows))
    assert len(selected) == 7
    assert selected['temperature_2m'][:2] == (200, 299)
    assert '30-33 hour acc' in selected['precipitation'][2]
    assert 'ave' not in selected['cloud_cover'][2]
    with pytest.raises(ValueError, match='Missing GFS field'):
        select_messages(rows[0])
