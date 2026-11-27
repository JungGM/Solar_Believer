import argparse,gzip,json,tempfile,unittest
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
import collector as c
from export_results import export,verify
class Tests(unittest.TestCase):
    loc={'location_id':'test','latitude':37,'longitude':127,'members':[]}
    def task(self):return next(c.tasks([self.loc],date(2022,1,1),date(2022,1,1)))
    def body(self,null=False):
        start=datetime(2021,12,31,15,tzinfo=timezone.utc)
        h={'time':[int((start+timedelta(hours=i)).timestamp()) for i in range(24)]}
        for v in c.VARIABLES:h[v+'_previous_day1']=[None if null else 0]*24
        return json.dumps({'hourly':h,'hourly_units':{}}).encode()
    def test_order(self):
        t=list(c.tasks([self.loc],date(2022,1,1),date(2026,9,30)))
        self.assertEqual(len(t),228);self.assertEqual(t[0]['start_date'],'2026-09-01');self.assertEqual(t[-1]['start_date'],'2022-01-01')
    def test_null_and_axis(self):
        self.assertEqual(c.validate(self.body(),self.task())['state'],'complete')
        self.assertEqual(c.validate(self.body(True),self.task())['state'],'unavailable')
        d=json.loads(self.body());d['hourly']['time'][0]+=3600
        with self.assertRaises(ValueError):c.validate(json.dumps(d).encode(),self.task())
    def test_quota(self):
        self.assertEqual(c.quota_wait([[0,8500]],2,1),86400)
        self.assertEqual(c.quota_wait([[0,8500]],2,86401),0)
    def test_save_resume_export_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'locations.json').write_text(json.dumps([self.loc]))
            args=argparse.Namespace(output=str(root/'out'),start=date(2022,1,1),end=date(2022,1,1),max_requests=1)
            body=self.body()
            class Response:
                def __enter__(self):return self
                def __exit__(self,*a):pass
                def read(self):return body
            with patch.object(c,'ROOT',root),patch.object(c,'urlopen',return_value=Response()) as http,patch.object(c.time,'sleep'):
                c.run(args);self.assertEqual(http.call_count,1)
            files=list((root/'out/responses').rglob('*.gz'));self.assertEqual(len(files),1);verify(files[0])
            state=json.loads((root/'out/state.json').read_text())
            for t in c.tasks([self.loc],args.start,args.end):state['tasks'][c.task_id(t)]={'state':'partial'}
            c.atomic(root/'out/state.json',state)
            with patch.object(c,'ROOT',root),patch.object(c,'urlopen') as http:c.run(args);http.assert_not_called()
            export(root/'out',root/'export');self.assertTrue((root/'export/manifest.json').exists())
            e=json.loads(gzip.decompress(files[0].read_bytes()));e['body_sha256']='bad';files[0].write_bytes(gzip.compress(json.dumps(e).encode()))
            with self.assertRaises(ValueError):verify(files[0])

    def test_db_outage_preserves_raw_and_recovers_without_api_recall(self):
        class FakeWriter:
            def __init__(self):
                self.fail_now = True
                self.requests = {}
                self.hourly = set()
            def check_shared_quota(self, cost, now_utc, daily_budget=8500):
                return 0.0
            def ingest_parsed_envelope(self, parsed, record_api_call=False, call_cost=0.0):
                if self.fail_now:
                    raise RuntimeError("Simulated DB connection lost")
                req_id = parsed["request_id"]
                is_dup = req_id in self.requests
                self.requests[req_id] = parsed["canonical_task_id"]
                added = 0
                for target_at, _, _ in parsed["hourly_rows"]:
                    k = (req_id, target_at)
                    if k not in self.hourly:
                        self.hourly.add(k)
                        added += 1
                return {
                    "status": "duplicate" if (is_dup and added == 0) else "inserted",
                    "request_id": req_id,
                    "canonical_task_id": parsed["canonical_task_id"],
                    "file_task_id": parsed["file_task_id"],
                    "hourly_inserted": added,
                }
            def close(self):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "locations.json").write_text(json.dumps([self.loc]))
            writer = FakeWriter()
            args = argparse.Namespace(
                output=str(root / "out"), start=date(2022, 1, 1), end=date(2022, 1, 1),
                max_requests=1, db_writer=writer,
            )
            body = self.body()
            class Response:
                def __enter__(self): return self
                def __exit__(self, *a): pass
                def read(self): return body

            # 1. Run while DB fails -> raw file is preserved, db_ingested is False
            with patch.object(c, "ROOT", root), patch.object(c, "urlopen", return_value=Response()) as http, patch.object(c.time, "sleep"):
                c.run(args)
                self.assertEqual(http.call_count, 1)
            state = json.loads((root / "out/state.json").read_text())
            only_task = next(iter(state["tasks"].values()))
            self.assertFalse(only_task["db_ingested"])
            self.assertEqual(len(writer.requests), 0)

            # 2. DB recovers -> flush pending file to DB without calling API again
            writer.fail_now = False
            for t in c.tasks([self.loc], args.start, args.end):
                tid = c.task_id(t)
                if tid not in state["tasks"]:
                    state["tasks"][tid] = {"state": "partial", "db_ingested": True}
            c.atomic(root / "out/state.json", state)
            with patch.object(c, "ROOT", root), patch.object(c, "urlopen") as http:
                c.run(args)
                http.assert_not_called()
            self.assertEqual(len(writer.requests), 1)
            self.assertEqual(len(writer.hourly), 24)

            # 3. Verify import_to_db idempotency on repeated batch run
            from import_to_db import run_migration
            s1 = run_migration(root / "out", validate_only=False, ignore_checkpoint=True, writer=writer)
            self.assertEqual(s1["db_duplicate_requests"], 1)
            self.assertEqual(s1["db_inserted_hourly_rows"], 0)
if __name__=='__main__':unittest.main()
