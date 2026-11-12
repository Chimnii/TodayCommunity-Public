from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crawler.collection_health import CONTRACT, PREFIX, publish_health, result_health
from crawler.jobs.collection_alerts import (
    ALERT_STEP, LANES, OBSERVER, classify, evaluate, run_gate, validate_event, was_notified,
)
from crawler.jobs.enforce_failure_streak import GitHubApiError

ROOT = Path(__file__).resolve().parents[1]
LANE = LANES["scan-ai-practice.yml"]


def run(n, *, attempt=1, conclusion="success"):
    return {"id": n, "run_number": n, "run_attempt": attempt, "workflow_id": 8,
            "status": "completed", "conclusion": conclusion, "event": "workflow_dispatch",
            "created_at": f"2026-11-12T{n % 24:02}:00:00Z"}


def jobs(health=None, *, conclusion="success", name=LANE[2], steps=None):
    rows = list(steps or [])
    if health is not None:
        rows.extend({"name": PREFIX + k + "=" + v, "conclusion": "success"} for k, v in health.items())
    return [{"name": name, "status": "completed", "conclusion": conclusion, "steps": rows}]


class Client:
    def __init__(self, states):
        self.runs = [run(i + 1) for i in range(len(states))][::-1]
        self.jobs = {(i+1, 1): jobs(s) for i, s in enumerate(states)}
        self.observers = []
        self.requests = []
        self.attempt_runs = {(r['id'],1):dict(r) for r in self.runs}

    def get_run(self, run_id):
        return next(r for r in self.runs if r["id"] == run_id)

    def get_attempt_run(self, run_id, attempt):
        return self.attempt_runs[(run_id,attempt)]

    def get_attempt_jobs(self, run_id, attempt):
        self.requests.append((run_id, attempt))
        return self.jobs[(run_id, attempt)]

    def iter_workflow_run_pages(self, workflow, branch):
        yield self.observers if workflow == OBSERVER else self.runs


F = {"pipeline": "failure"}
S = {"pipeline": "success"}
D = {"pipeline": "deferred"}


class CollectionDecisionTests(unittest.TestCase):
    def test_new_source_history_stops_at_legacy_contract_boundary(self):
        client=Client([S]*1100+[{"pipeline":"success","source:gpters":"failure"}])
        for i in range(1,1101):client.jobs[(i,1)]=jobs()
        result=evaluate(client,client.runs[0],LANE)
        self.assertEqual(result.streaks,{"source:gpters":1})
        self.assertLess(len(client.requests),5)

    def test_new_contract_not_due_is_not_legacy_boundary(self):
        source={"pipeline":"success","source:gpters":"failure"}
        client=Client([source,D,source,D,source])
        for i in (2,4):client.jobs[(i,1)][0]['steps'].append({"name":CONTRACT,"conclusion":"success"})
        self.assertTrue(evaluate(client,client.runs[0],LANE).notify)

    def test_new_success_missing_marker_is_monitor_failure(self):
        with self.assertRaises(GitHubApiError):
            classify(run(1),jobs(steps=[{"name":CONTRACT,"conclusion":"success"}]),LANE[2])

    def test_late_old_retry_does_not_rewrite_subsequent_history(self):
        client=Client([F,F,F]);client.runs[2]['run_attempt']=2
        client.jobs[(1,2)]=jobs(S);client.jobs[(1,2)][0]['started_at']='2026-11-12T05:00:00Z'
        self.assertTrue(evaluate(client,client.runs[0],LANE).notify)
        self.assertIn((1,1),client.requests)

    def test_historical_jobs_zero_uses_same_attempt_conclusion_even_during_late_retry(self):
        for later_status in ['completed','in_progress']:
            with self.subTest(status=later_status):
                client=Client([F,F,F]);client.runs[2].update(run_attempt=2,status=later_status)
                client.attempt_runs[(1,1)]['conclusion']='failure'
                client.jobs[(1,1)]=[]
                client.jobs[(1,2)]=jobs(S);client.jobs[(1,2)][0]['started_at']='2026-11-12T05:00:00Z'
                self.assertTrue(evaluate(client,client.runs[0],LANE).notify)

    def test_recovered_old_incident_stays_closed_after_a_new_failure(self):
        client=Client([F,F,F,S,F])
        self.assertFalse(evaluate(client,client.runs[2],LANE).notify)

    def test_native_upstream_failure_has_no_second_observer_failure_signal(self):
        client=Client([F,F,F]);client.runs[0]['conclusion']='failure'
        decision=run_gate(client,self.event(client.runs[0]),'owner/repo',90)
        self.assertEqual(decision.alert_components,['pipeline'])
        self.assertEqual(decision.suppression,'upstream_already_failed')
        self.assertFalse(decision.notify)

    def test_three_distinct_failures_then_quiet_four_five_and_six_reminder(self):
        for count, expected in [(1,False),(2,False),(3,True),(4,False),(5,False),(6,True)]:
            with self.subTest(count=count):
                client = Client([S] + [F]*count)
                result = evaluate(client, client.runs[0], LANE)
                self.assertEqual(result.notify, expected)
                self.assertEqual(result.streaks, {"pipeline":count})

    def test_recovery_and_recurrence_need_three_again(self):
        for count in range(1,4):
            client = Client([F,F,F,S]+[F]*count)
            decision = evaluate(client,client.runs[0],LANE)
            self.assertEqual(decision.notify,count==3)
            self.assertEqual(decision.streaks["pipeline"],count)

    def test_not_due_neither_fails_nor_recovers(self):
        client=Client([S,F,D,F,D,F])
        result=evaluate(client,client.runs[0],LANE)
        self.assertTrue(result.notify)
        self.assertEqual(result.streaks["pipeline"],3)
        client=Client([F,F,D])
        self.assertFalse(evaluate(client,client.runs[0],LANE).notify)

    def test_source_partial_outage_survives_healthy_siblings_and_cooldown(self):
        a={"pipeline":"success","source:gpters":"failure","source:hermes":"success"}
        b={"pipeline":"success","source:gpters":"deferred","source:hermes":"success"}
        client=Client([a,b,a,b,a])
        result=evaluate(client,client.runs[0],LANE)
        self.assertEqual(result.alert_components,["source:gpters"])
        self.assertEqual(result.streaks,{"source:gpters":3})

    def test_duplicate_history_rows_and_latest_attempt_only(self):
        client=Client([F,F,F]);client.runs[0]["run_attempt"]=2
        client.jobs[(3,2)]=jobs(F)
        client.runs.insert(1,dict(client.runs[0]))
        result=evaluate(client,client.runs[0],LANE)
        self.assertEqual(result.streaks["pipeline"],3)
        self.assertNotIn((3,1),client.requests)
        client.jobs[(3,2)]=jobs(S)
        self.assertFalse(evaluate(client,client.runs[0],LANE).notify)

    def test_stale_event_attempt_and_old_rerun_are_suppressed(self):
        client=Client([F,F,F,F]);old=dict(client.runs[1]);old["run_attempt"]=2
        self.assertEqual(evaluate(client,old,LANE).reason,"superseded_attempt")
        client.runs[1]["run_attempt"]=2;client.jobs[(3,2)]=jobs(F)
        self.assertEqual(evaluate(client,old,LANE).reason,"superseded_run")

    def test_delayed_third_event_not_lost_on_fourth_failure_but_recovery_suppresses(self):
        client=Client([F,F,F,F])
        self.assertTrue(evaluate(client,client.runs[1],LANE).notify)
        client.jobs[(4,1)]=jobs(S)
        self.assertFalse(evaluate(client,client.runs[1],LANE).notify)

    def test_critical_integrity_error_pages_immediately(self):
        client=Client([{"pipeline":"critical"}])
        self.assertTrue(evaluate(client,client.runs[0],LANE).notify)

    def test_jobs_zero_and_terminal_queued_job_count_as_failure(self):
        r=run(1,conclusion="failure")
        self.assertEqual(classify(r,[],LANE[2]),F)
        j=jobs();j[0].update(status="queued",conclusion=None)
        self.assertEqual(classify(r,j,LANE[2]),F)

    def test_platform_run_failure_after_real_job_success_is_not_collection_failure(self):
        self.assertEqual(classify(run(1,conclusion="failure"),jobs(S),LANE[2]),S)
        self.assertEqual(classify(run(1,conclusion="failure"),jobs(),LANE[2]),S)

    def test_raw_failed_steps_are_not_hidden_by_continue_on_error(self):
        j=jobs(conclusion="success",steps=[{"name":"Run persisting AI-practice curation","conclusion":"failure"}])
        self.assertEqual(classify(run(1),j,LANE[2]),F)

    def test_refresh_failure_counts_even_when_collection_has_source_failure(self):
        j=jobs({"pipeline":"success","source:gpters":"failure"},conclusion="failure",
               steps=[{"name":"Refresh stored community topic snapshots","conclusion":"failure"}])
        self.assertEqual(classify(run(1),j,LANE[2]),{"pipeline":"failure","source:gpters":"failure"})

    def test_diagnostic_runs_do_not_reset_production_streak(self):
        j=jobs(conclusion="failure",steps=[{"name":"Collection health: diagnostic run","conclusion":"success"}])
        self.assertEqual(classify(run(1),j,LANE[2]),D)

    def test_observer_retries_and_duplicate_events_do_not_repeat_alert(self):
        client=Client([F,F,F]);current=client.runs[0]
        client.observers=[{**run(90,attempt=2),"display_title":"Collection alert / 3 / 1"}]
        client.jobs[(90,2)]=jobs(steps=[])
        client.jobs[(90,1)]=jobs(steps=[{"name":ALERT_STEP,"conclusion":"failure"}])
        self.assertTrue(was_notified(client,current,90))
        event=self.event(current)
        result=run_gate(client,event,"owner/repo",90)
        self.assertFalse(result.notify)
        self.assertEqual(result.reason,"already_notified_run")

    @staticmethod
    def event(current):
        return {"action":"completed","workflow_run":{**current,"path":".github/workflows/scan-ai-practice.yml",
            "name":LANE[1],"head_branch":"main","repository":{"full_name":"owner/repo"},
            "head_repository":{"full_name":"owner/repo"}}}

    def test_event_security_scope_and_no_observer_recursion(self):
        event=self.event(run(1));validate_event(event,"owner/repo")
        for key,value in [("path",".github/workflows/collection-alerts.yml"),("head_branch","evil"),
                          ("event","pull_request"),("path","scan-ai-practice.yml"),
                          ("status","in_progress"),("repository",{"full_name":"attacker/repo"}),
                          ("head_repository",{"full_name":"attacker/repo"})]:
            bad=copy.deepcopy(event);bad['workflow_run'][key]=value
            with self.assertRaises(ValueError):validate_event(bad,"owner/repo")

    def test_live_run_names_are_display_titles_not_workflow_identity(self):
        # Shapes observed from the GitHub run API on 2026-11-12. Private lanes
        # have dynamic run-name values; DC retains the static workflow title.
        from collection_history_fixture import LIVE_RUN_EVENTS
        for live_event in LIVE_RUN_EVENTS:
            live_run = live_event['workflow_run']
            repository = live_run['repository']['full_name']
            filename = live_run['path'].split('/')[-1]
            with self.subTest(filename=filename):
                # All scope fields are the actual API values, not rewritten to
                # our assumptions; only the completion envelope is synthesized.
                validated, lane = validate_event(live_event, repository)
                self.assertEqual(validated, live_run)
                self.assertEqual(lane, LANES[filename])
                client = Client([S])
                client.jobs[(1, 1)][0]['name'] = LANES[filename][2]
                event = self.event(client.runs[0])
                event['workflow_run'].update(path=live_run['path'], name=live_run['name'])
                result = run_gate(client, event, 'owner/repo', 90)
                self.assertEqual(result.lane, LANES[filename][0])
                self.assertEqual(result.health, S)
                self.assertFalse(result.notify)

    def test_bad_history_is_monitor_error_not_a_false_recovery(self):
        client=Client([F,F,F]);client.jobs[(2,1)]=[]
        with self.assertRaises(GitHubApiError):evaluate(client,client.runs[0],LANE)

    def test_unreadable_monitor_retries_then_fails_distinctly(self):
        from crawler.jobs.collection_alerts import main
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'event.json';path.write_text(json.dumps(self.event(run(1))))
            with patch.dict(os.environ,{"GITHUB_REPOSITORY":"owner/repo","GITHUB_EVENT_PATH":str(path),
                    "GITHUB_TOKEN":"test-not-a-credential","GITHUB_RUN_ID":"9"}), \
                 patch('crawler.jobs.collection_alerts.run_gate',side_effect=GitHubApiError('HTTP503')) as gate, \
                 patch('crawler.jobs.collection_alerts.time.sleep'):
                with self.assertRaises(GitHubApiError):main()
                self.assertEqual(gate.call_count,3)

    def test_real_ten_failure_runs_classified_from_saved_api_evidence(self):
        from collection_history_fixture import HISTORY
        expected_success={42671258185,42673531948,42671258911}
        for record in HISTORY:
            lane=next(v for v in LANES.values() if v[0]==record['lane'])
            with self.subTest(run=record['run']['id']):
                self.assertEqual(classify(record['run'],record['jobs'],lane[2]),
                                 S if record['run']['id'] in expected_success else F)
        self.assertEqual(len(HISTORY),10)


class ResultHealthTests(unittest.TestCase):
    def test_community_partial_has_reason_specific_semantics(self):
        for reason,expected in [('hot_deadline','deferred'),('cycle_deadline','deferred'),
                                ('page_order_not_coverage_safe','failure'),('unknown','failure')]:
            with self.subTest(reason=reason):
                result={'status':'completed','results':[{'target':'dcinside-singularity','status':'partial',
                       'phases':[{'status':'partial','stop_reason':reason}]}]}
                self.assertEqual(result_health(result,'dc')['source:dcinside-singularity'],expected)

    def test_editorial_review_exclude_and_no_articles_are_normal(self):
        self.assertEqual(result_health({"status":"completed","candidate_count":0,"included_count":0,"review_count":5},'game'),S)

    def test_ai_budget_partial_and_prompt_deferral_are_not_failures(self):
        result={"status":"partial","candidate_count":1,"review_count":1,"discovery":{
            "sources":[{"source":"hermes","status":"partial","model_input_status":"deferred",
                        "pages":[{"status":"ok"}]}],
            "fetches":[{"source":"hermes","status":"partial","error":"Source request budget exhausted"}],
            "deferred_for_prompt_budget":["item"]}}
        self.assertEqual(result_health(result,'ai'),{"pipeline":"success","source:hermes":"deferred"})
        result['discovery']['fetches'][0]['error']='HTTP403'
        self.assertEqual(result_health(result,'ai')['source:hermes'],'failure')

    def test_ai_deferred_failed_sitemap_is_not_new_failure_or_recovery(self):
        result={"status":"partial","discovery":{"sources":[{"source":"gpters","status":"partial",
            "pages":[{"status":"deferred","scope":{"failures":[{"error":"HTTP404"}]}}]}]}}
        self.assertEqual(result_health(result,'ai'),{"pipeline":"deferred","source:gpters":"deferred"})
        result['discovery']['sources'][0]['pages'][0]['status']='partial'
        self.assertEqual(result_health(result,'ai')['source:gpters'],'failure')

    def test_missing_game_site_is_a_partial_failure(self):
        self.assertEqual(result_health({"status":"completed","observed_sites":["inven"],"missing_sites":["ruliweb"]},'game'),
                         {"pipeline":"success","source:inven":"success","source:ruliweb":"failure"})

    def test_community_cooldown_vs_failed_source(self):
        result={"status":"completed","results":[{"target":"dcinside-singularity","status":"cooldown"}]}
        self.assertEqual(result_health(result,'dc'),{"pipeline":"deferred","source:dcinside-singularity":"deferred"})
        result['results'][0]['status']='blocked'
        self.assertEqual(result_health(result,'dc')['source:dcinside-singularity'],'failure')

    def test_health_output_contains_no_text_urls_or_unsafe_component_names(self):
        with tempfile.TemporaryDirectory() as td,patch.dict(os.environ,{"GITHUB_OUTPUT":str(Path(td)/'out'),"GITHUB_STEP_SUMMARY":str(Path(td)/'summary')}):
            publish_health({"status":"completed","error_message":"secret","items":[{"body":"private"}]},'game')
            content=(Path(td)/'out').read_text()
            self.assertNotIn('secret',content);self.assertNotIn('private',content)
            self.assertIn('collection_health={"pipeline":"success"}',content)

    def test_diagnostic_write_failure_does_not_replace_original_result(self):
        with patch('builtins.open',side_effect=OSError('denied')),patch.dict(os.environ,{"GITHUB_OUTPUT":"missing"}),contextlib.redirect_stdout(io.StringIO()):
            publish_health({"status":"success"},'ai')


class ObserverWorkflowTests(unittest.TestCase):
    def test_ordinary_failures_keep_raw_steps_failed_but_only_third_observer_signals(self):
        # Offline contract test against the actual YAML and GitHub's documented
        # job-level continue-on-error semantics. It does not send notifications
        # or claim to execute GitHub's orchestration service locally.
        failing_command = subprocess.run([sys.executable, '-c', 'raise SystemExit(1)'],
                                         capture_output=True, check=False)
        self.assertEqual(failing_command.returncode, 1)
        for filename,lane in LANES.items():
            public = filename == 'scan-dcinside.yml'
            directory = ROOT/'public-mirror/.github/workflows' if public else ROOT/'.github/workflows'
            if public and not directory.exists():directory=ROOT/'.github/workflows'
            path=directory/filename
            if not path.exists():continue  # private lanes aren't exported
            workflow=path.read_text()
            raw_job=workflow.split('jobs:\n',1)[1]
            self.assertEqual(len(re.findall(r'^  [a-z][a-z0-9_-]*:$',raw_job,re.M)),1)
            self.assertRegex(raw_job,r'(?m)^    continue-on-error: true$')
            self.assertNotRegex(raw_job,r'(?m)^        continue-on-error:')
            observer=(directory/'collection-alerts.yml').read_text()
            self.assertIn("if: steps.decision.outputs.notify == 'true'",observer)
            self.assertIn('exit 1',observer)
            self.assertNotIn('continue-on-error:',observer)
            for count in (1,2,3):
                with self.subTest(lane=lane[0],count=count):
                    client=Client([F]*count)
                    for job_rows in client.jobs.values():
                        job_rows[0]['name']=lane[2]
                        job_rows[0]['conclusion']='failure'
                        job_rows[0]['steps'].insert(0,{'name':'Collection process','conclusion':'failure'})
                    # A failed collector job is allowed by the job-level policy,
                    # so the original workflow does not emit a failed-run signal.
                    upstream=client.runs[0]
                    self.assertEqual(upstream['conclusion'],'success')
                    event=CollectionDecisionTests.event(upstream)
                    event['workflow_run'].update(path='.github/workflows/'+filename,name=lane[1])
                    decision=run_gate(client,event,'owner/repo',90)
                    self.assertEqual(decision.notify,count==3)
                    self.assertEqual(decision.streaks,{'pipeline':count})
                    self.assertEqual(client.jobs[(count,1)][0]['conclusion'],'failure')
                    self.assertEqual(client.jobs[(count,1)][0]['steps'][0]['conclusion'],'failure')
                    self.assertEqual(decision.suppression,'')

    def test_private_component_markers_cover_the_configured_sources(self):
        if not (ROOT/'ai_practice').exists():self.skipTest('private packages not exported')
        from crawler.collection_health import AI_SOURCES
        cases=[('scan-ai-practice.yml',AI_SOURCES),
               ('scan-game-news.yml',set(json.loads((ROOT/'game_news/sites.json').read_text())['sites']))]
        for filename,sources in cases:
            text=(ROOT/'.github/workflows'/filename).read_text()
            for source in sources:
                self.assertEqual(text.count(PREFIX+'source:'+source+'='),1)
            self.assertEqual(text.count(PREFIX+'pipeline='),1)

    def test_all_emitted_component_names_are_ascii_and_short(self):
        from crawler.collection_health import AI_SOURCES
        for key in ['pipeline']+['source:'+s for s in AI_SOURCES]+['source:fmkorea-best-munich-search']:
            for state in ['success','failure','deferred','critical']:
                name=PREFIX+key+'='+state
                self.assertLess(len(name.encode('ascii')),100)

    def test_minimal_permissions_trusted_checkout_no_artifacts_and_bounded_scope(self):
        files=[ROOT/'.github/workflows/collection-alerts.yml',ROOT/'public-mirror/.github/workflows/collection-alerts.yml']
        for path in files:
            if not path.exists():continue
            text=path.read_text()
            self.assertIn('types: [completed]',text)
            self.assertIn('ref: main',text)
            self.assertIn('actions: read',text)
            self.assertIn('contents: read',text)
            self.assertNotIn(': write',text)
            self.assertNotIn('${{ secrets.',text)
            self.assertNotIn('download-artifact',text)
            self.assertNotIn('workflows: [Collection alerts',text)
            self.assertIn('group: collection-alerts-${{ github.event.workflow_run.id }}',text)


class CliHealthTests(unittest.TestCase):
    def test_ai_main_success_busy_exception_and_diagnostic_io_failure(self):
        if not (ROOT/'ai_practice').exists():self.skipTest('private AI package not exported')
        from ai_practice import runner
        for outcome,exit_code in [({'status':'success'},None),(runner.RunBusy('busy'),None),(RuntimeError('original'),1)]:
            with self.subTest(outcome=str(outcome)),tempfile.TemporaryDirectory() as td:
                with patch.dict(os.environ,{'GITHUB_OUTPUT':str(Path(td)/'out'),'GITHUB_STEP_SUMMARY':str(Path(td)/'summary')}), \
                    patch('sys.argv',['ai','--run-root',td]),patch.object(runner,'configure_utf8_stdio'), \
                    patch.object(runner,'run_pipeline',side_effect=outcome if isinstance(outcome,Exception) else None,
                                 return_value=outcome),contextlib.redirect_stdout(io.StringIO()) as stdout:
                    if exit_code:
                        with self.assertRaises(SystemExit) as raised:runner.main()
                        self.assertEqual(raised.exception.code,exit_code)
                        self.assertIn('original',stdout.getvalue())
                    else:runner.main()
                    health=json.loads((Path(td)/'out').read_text().split('=',1)[1])
                    self.assertEqual(health['pipeline'],'failure' if exit_code else 'deferred' if isinstance(outcome,runner.RunBusy) else 'success')

    def test_game_main_success_and_exception_preserve_exit(self):
        if not (ROOT/'game_news').exists():self.skipTest('private game package not exported')
        from game_news import runner
        for outcome in [{'status':'completed'},RuntimeError('original')]:
            with self.subTest(outcome=str(outcome)),tempfile.TemporaryDirectory() as td, \
                patch.dict(os.environ,{'GITHUB_OUTPUT':str(Path(td)/'out'),'GITHUB_STEP_SUMMARY':str(Path(td)/'summary')}), \
                patch.object(runner,'configure_utf8_stdio'),patch.object(runner,'run_pipeline',
                    side_effect=outcome if isinstance(outcome,Exception) else None,return_value=outcome), \
                contextlib.redirect_stdout(io.StringIO()):
                if isinstance(outcome,Exception):
                    with self.assertRaises(SystemExit) as raised:runner.main(['--run-root',td])
                    self.assertEqual(raised.exception.code,1)
                    self.assertIs(raised.exception.__cause__,outcome)
                else:runner.main(['--run-root',td])
                self.assertIn('collection_health=',(Path(td)/'out').read_text())

    def test_dc_and_fm_main_emit_result_without_rewriting_partial_exit_policy(self):
        from types import SimpleNamespace
        from crawler.jobs import run_all_sources,run_fmkorea_sources
        args=SimpleNamespace(mode='hot',persist=False,headless=True,max_pages_per_target=1)
        for module,function in [(run_all_sources,'run_all_targets'),(run_fmkorea_sources,'run_local_fmkorea_sources')]:
            with self.subTest(module=module.__name__),tempfile.TemporaryDirectory() as td, \
                patch.dict(os.environ,{'GITHUB_OUTPUT':str(Path(td)/'out'),'GITHUB_STEP_SUMMARY':str(Path(td)/'summary')}), \
                patch.object(module,'parse_args',return_value=args),patch.object(module,function,return_value={
                    'status':'completed','results':[{'target':'source','status':'partial',
                    'phases':[{'status':'partial','stop_reason':'hot_deadline'}]}]}),contextlib.redirect_stdout(io.StringIO()):
                module.main()
                self.assertIn('"source:source":"deferred"',(Path(td)/'out').read_text())


if __name__ == '__main__':
    unittest.main()
