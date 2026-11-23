from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crawler.collection_health import publish_health, result_health

ROOT = Path(__file__).resolve().parents[1]


class ResultHealthTests(unittest.TestCase):
    def test_budget_reason_never_masks_transport_or_persistence_error(self):
        base = {'target':'dcinside-singularity','status':'partial',
                'phases':[{'status':'partial','stop_reason':'request_timeout_budget'}]}
        for addition in [{'error':'Source returned transient HTTP 504'},
                         {'error':'CrawlTimeoutError'}, {'status':'failed'},
                         {'persistence_error':'database failure'}, {'persistence_warnings':['write failed']}]:
            with self.subTest(addition=addition):
                result = {'status':'completed','results':[{**base,**addition}]}
                self.assertEqual(result_health(result,'dc')['source:dcinside-singularity'],'failure')
        result = {'status':'completed','results':[{**base,'phases':base['phases']+[{'status':'failed','stop_reason':'transport timeout'}]}]}
        self.assertEqual(result_health(result,'dc')['source:dcinside-singularity'],'failure')

    def test_live_dc_budget_incidents_replay_without_losing_real_504(self):
        from collection_history_fixture import COMMUNITY_BUDGET_RESULTS
        for fixture in COMMUNITY_BUDGET_RESULTS:
            health = result_health(fixture['result'],'dc')
            self.assertEqual(health,fixture['expected'])

    def test_community_partial_has_reason_specific_semantics(self):
        for reason,expected in [('hot_deadline','deferred'),('cycle_deadline','deferred'),
                                ('request_timeout_budget','deferred'),('deep_time_reservation','deferred'),
                                ('page_order_not_coverage_safe','failure'),('unknown','failure')]:
            with self.subTest(reason=reason):
                result={'status':'completed','results':[{'target':'dcinside-singularity','status':'partial',
                       'phases':[{'status':'partial','stop_reason':reason}]}]}
                self.assertEqual(result_health(result,'dc')['source:dcinside-singularity'],expected)

    def test_editorial_review_exclude_and_no_articles_are_normal(self):
        self.assertEqual(result_health({"status":"completed","candidate_count":0,"included_count":0,"review_count":5},'game'),{'pipeline':'success'})

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


class AIAcquisitionRecoveryHealthTests(unittest.TestCase):
    def test_unresolved_candidate_survives_other_success_without_new_failure(self):
        result = {'status': 'partial', 'discovery': {'sources': [{'source': 'gastown', 'status': 'ok',
            'pages': [{'status': 'ok', 'attempt_status': 'ok'}], 'unresolved_acquisition': {'items': 1}}],
            'fetches': [{'source': 'gastown', 'status': 'ok'}]}}
        health = result_health(result, 'ai')
        self.assertEqual(health['source:gastown'], 'deferred')
        result['discovery']['sources'][0].pop('unresolved_acquisition')
        recovery = result_health(result, 'ai')
        self.assertEqual(recovery['source:gastown'], 'success')

    def test_aggregate_view_verification_alone_does_not_reset_source_failure_history(self):
        aggregate = {'status': 'ok', 'attempt_status': 'ok',
                     'scope': {'aggregate_view': {'classification': 'verified_noncanonical_aggregate'}}}
        source = {'source': 'gpters', 'pages': [aggregate]}
        result = {'discovery': {'sources': [source]}}
        self.assertEqual(result_health(result, 'ai')['source:gpters'], 'deferred')
        source['pages'].append({'status': 'failed', 'attempt_status': 'failed'})
        self.assertEqual(result_health(result, 'ai')['source:gpters'], 'failure')
        source['pages'][-1] = {'status': 'ok', 'scope': {'aggregate_view': None}}
        source['unresolved_acquisition'] = {'items': 1}
        self.assertEqual(result_health(result, 'ai')['source:gpters'], 'deferred')
        source.pop('unresolved_acquisition')
        self.assertEqual(result_health(result, 'ai')['source:gpters'], 'success')

    def test_retained_coverage_does_not_repeat_failed_attempt_or_recover(self):
        page = {'status': 'partial', 'attempt_status': 'ok', 'scope': {'failures': [{'error': 'HTTP404'}]}}
        result = {'status': 'partial', 'discovery': {'sources': [{'source': 'gpters', 'status': 'failed', 'pages': [page]}]}}
        self.assertEqual(result_health(result, 'ai')['source:gpters'], 'deferred')
        page['attempt_status'] = 'failed'
        self.assertEqual(result_health(result, 'ai')['source:gpters'], 'failure')

    def test_content_hold_requires_http_and_rendered_text_evidence(self):
        fetch = {'source': 'paperclip', 'status': 'insufficient_content'}
        result = {'status': 'partial', 'discovery': {'sources': [{'source': 'paperclip', 'status': 'partial', 'pages': []}], 'fetches': [fetch]}}
        self.assertEqual(result_health(result, 'ai')['source:paperclip'], 'failure')
        fetch['content_evidence'] = {'reason': 'observed_public_text_too_short', 'http_status': 200, 'text_chars': 1, 'rendered_bodies': 1}
        self.assertEqual(result_health(result, 'ai')['source:paperclip'], 'success')
        fetch['content_evidence']['http_status'] = 206
        self.assertEqual(result_health(result, 'ai')['source:paperclip'], 'failure')


if __name__ == '__main__':
    unittest.main()
