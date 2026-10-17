"""Behavioral acceptance for local async work, draft versions and case replay."""
import copy
import gzip
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import orchestrator as o
import demo_data as d
import workbench_jobs as jobs
import run_store


class VersionAndCaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.outputs = patch.object(o, 'OUTPUTS_DIR', str(self.root / 'outputs'))
        self.outputs.start()
        self.addCleanup(self.outputs.stop)
        self.snapshot = o.review_step(d.EDU_TASK + '\nscreening_mode: strict\n')
        self.assertTrue(self.snapshot['ok'])

    def test_extractive_draft_creates_child_and_keeps_original(self):
        original_id = self.snapshot['run_id']
        old = run_store.load(o.OUTPUTS_DIR, original_id)
        child = o.synthesis_step(original_id)
        self.assertTrue(child['ok'])
        self.assertNotEqual(original_id, child['run_id'])
        self.assertEqual(child['result']['parent_run_id'], original_id)
        self.assertEqual(child['result']['synthesis']['provider'], 'extractive')
        self.assertIsNone(child['verification'])
        self.assertEqual(run_store.load(o.OUTPUTS_DIR, original_id), old)
        self.assertTrue(child['result']['synthesis']['validation']['passed'])

    def test_failed_model_keeps_saved_source_and_exposes_stage(self):
        original_id = self.snapshot['run_id']
        with patch('evidence_synthesis.synthesize', side_effect=RuntimeError('模型额度不足')):
            result = o.synthesis_step(original_id, 'claude-cli')
        self.assertFalse(result['ok'])
        self.assertEqual(result['stage'], 'synthesis')
        self.assertIn('额度', result['error'])
        self.assertNotIn('synthesis', run_store.load(o.OUTPUTS_DIR, original_id)['result'])

    def test_evidence_bundle_contains_only_this_run_and_fixed_artifacts(self):
        import io
        import zipfile
        original_id = self.snapshot['run_id']
        directory = run_store.directory(o.OUTPUTS_DIR, original_id)
        (directory / 'unrelated-private.txt').write_text('do not export', encoding='utf-8')
        with zipfile.ZipFile(io.BytesIO(o.export_bundle(original_id))) as archive:
            self.assertIn('snapshot.json', archive.namelist())
            self.assertIn('review.md', archive.namelist())
            self.assertNotIn('unrelated-private.txt', archive.namelist())
            self.assertEqual(json.loads(archive.read('snapshot.json'))['run_id'], original_id)

    def test_recorded_case_replays_with_provenance_and_new_identity(self):
        samples = self.root / 'samples'
        samples.mkdir()
        (samples / 'manifest.json').write_text(json.dumps([{'id': 'test-case', 'title': '真实流程测试夹具'}]), encoding='utf-8')
        with gzip.open(samples / 'test-case.json.gz', 'wt', encoding='utf-8') as stream:
            json.dump(self.snapshot, stream, ensure_ascii=False)
        with patch.object(o, 'HERE', str(self.root)):
            replay = o.replay_case('test-case')
        self.assertNotEqual(replay['run_id'], self.snapshot['run_id'])
        self.assertEqual(replay['replay']['original_run_id'], self.snapshot['run_id'])
        self.assertEqual(replay['result']['replay']['mode'], 'recorded_replay')
        self.assertIn('未在此次重新查询', replay['replay']['note'])
        self.assertTrue(run_store.directory(o.OUTPUTS_DIR, replay['run_id']).exists())

    def test_form_roundtrip_keeps_custom_queries_and_criteria(self):
        yaml = d.live_task_yaml(d.LUNG_TASK, ['pubmed', 'openalex']) + '\nqueries:\n  pubmed: "nodule AND CT"\n'
        edited = o.task_card(yaml, {'years': '2020-2025', 'max_papers': 15})
        o._ensure_paths()
        import agent
        raw = agent._load_yaml(edited['task_yaml'])
        self.assertEqual(raw['queries']['pubmed'], 'nodule AND CT')
        self.assertEqual(raw['must_answer'], agent._load_yaml(yaml)['must_answer'])
        self.assertEqual(raw['max_papers'], 15)
        self.assertEqual(raw['years'], '2020-2025')

    def test_form_rejects_invalid_bounds_and_empty_sources(self):
        for changes in ({'max_papers': 30}, {'sources': []}, {'years': 'wrong'}, {'must_answer': 'not-list'}, {'unrecognized': 'x'}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    o.task_card(d.EDU_TASK, changes)

    def test_clinical_concepts_do_not_split_ct_into_unrelated_terms(self):
        response = o.mesh_step('低剂量CT肺结节检测的跨机型泛化')
        self.assertTrue(response['ok'])
        self.assertIn('Solitary Pulmonary Nodule', response['query'])
        self.assertIn('Tomography, X-Ray Computed', response['query'])
        self.assertNotIn('Ctenophora', json.dumps(response))
        free = [c for c in response['chunks'] if not c['matched']]
        self.assertTrue(free)
        self.assertTrue(all('[MeSH Terms]' not in c['clause'] for c in free))

    def test_source_query_includes_user_generalization_context(self):
        import research_engine
        queries = research_engine.topic_queries({'topic': '低剂量CT肺结节检测跨机型泛化'})
        self.assertIn('generalization', queries['openalex'])
        self.assertIn('nodule', queries['openalex'])
        plain = research_engine.topic_queries({'topic': '肺结节CT检测'})
        self.assertNotIn('generalization', plain['openalex'])

    def test_only_year_conflict_is_not_reported_as_another_paper(self):
        ref = {'doi': '10.1234/test', 'title': 'A verified title', 'year': 2025}
        with patch.object(o, '_get_json', return_value={'message': {'title': ['A verified title'], 'issued': {'date-parts': [[2024]]}}}):
            checked = o._verify_one(ref)
        self.assertEqual(checked['verdict'], '年份待核')
        self.assertIn('在线发表', checked['reason'])

    def test_next_source_yield_does_not_include_previous_natural_end(self):
        import research_engine as engine
        import sources
        import agent
        task, _ = agent.parse_task(d.live_task_yaml(d.LUNG_TASK, ['pubmed', 'openalex']))
        task['screening_mode'] = 'topic'
        def paper(n):
            return {'title': 'Cross-vendor low-dose CT lung nodule detection study ' + str(n),
                    'abstract': 'Cross-vendor low-dose CT pulmonary nodule detection was evaluated in independent patients.',
                    'doi': '10.1234/fixture-' + str(n), 'year': 2024, 'source': 'pubmed'}
        def search(source, *_args, **kwargs):
            if source == 'pubmed':
                return [paper(n) for n in range(3)]
            papers = [dict(paper(4), source='openalex')]
            kwargs['on_page'](papers, {'page': 1, 'retrieved_count': 1})
            return papers
        with patch.object(sources, 'search_paginated', side_effect=search):
            records, _ = engine.retrieve(task, sources, [], '', '')
        self.assertEqual(records[1]['adaptive_batches'][0]['new_eligible'], 1)
        self.assertEqual(records[1]['adaptive_batches'][0]['new_eligible_rate'], 1.0)


class LocalJobsTests(unittest.TestCase):
    def test_progress_records_actual_events_and_result(self):
        def work(progress):
            progress({'stage': 'retrieval', 'message': 'fixture returned 7', 'returned': 7})
            return {'ok': True, 'value': 7}
        identity = jobs.submit('test', work)['job_id']
        result = self.wait(identity)
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['events'][0]['returned'], 7)
        self.assertEqual(result['result']['value'], 7)

    def wait(self, identity):
        for _ in range(100):
            result = jobs.get(identity)
            if result['status'] in {'succeeded', 'failed'}:
                return result
            time.sleep(.01)
        self.fail('local task did not complete')

    def test_error_is_failed_not_success_or_generated_record(self):
        def work(progress):
            raise ValueError('test error')
        result = self.wait(jobs.submit('test', work)['job_id'])
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['result']['ok'])
        self.assertNotIn('references', result['result'])

    def test_jobs_limit_blocks_additional_work(self):
        event = threading.Event()
        identities = []
        try:
            for _ in range(2):
                identities.append(jobs.submit('test', lambda _: (event.wait(3), {'ok': True})[1])['job_id'])
            with self.assertRaises(ValueError):
                jobs.submit('test', lambda _: {'ok': True})
        finally:
            event.set()
            for identity in identities:
                self.wait(identity)


if __name__ == '__main__':
    unittest.main()
