"""Regression checks use controlled source fixtures, never live model services."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import subprocess
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import orchestrator as o
import demo_data as d
import research_engine as e
import http_service
o._ensure_paths()
import agent
import sources

STRICT_EDU = d.EDU_TASK + "\nscreening_mode: strict\n"


class RunFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = patch.object(o, 'OUTPUTS_DIR', self.temp.name)
        self.output.start()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.output.stop)

    def run_edu(self):
        r = o.review_step(STRICT_EDU)
        self.assertTrue(r['ok'], r.get('error'))
        return r

    def live_fixture(self):
        # Test record: explicitly controlled metadata, not a publication claim.
        return {'title': 'Short-video use and sustained attention in college students',
                'authors': ['Tester A'], 'year': 2023, 'source': 'openalex',
                'doi': '10.1234/test-fixture', 'url': 'https://example.org/record',
                'abstract': 'College students using short-video apps had attention measurements in this study.'}


class ResearchTests(RunFixture):

    def test_education_does_not_adopt_learning_rate_paper(self):
        r = self.run_edu()['result']
        self.assertEqual(len(r['references']), 1)
        self.assertIn('Short-video', r['references'][0]['title'])
        self.assertTrue(any('fine-tuning' in p['title'] for p in r['screening']['uncertain']))
        self.assertNotIn('Parameter-efficient', json.dumps(r['sections']))

    def test_no_silent_relaxation_and_negation_is_uncertain(self):
        task, _ = agent.parse_task(STRICT_EDU)
        task['inclusion'] = ['absent-criterion']
        result = e.screen(task, d.GENERIC_MOCK, agent)
        self.assertFalse(result['included'])
        task['inclusion'] = ['attention']
        task['exclusion'] = ['no external validation']
        self.assertFalse(e.screen(task, [self.live_fixture()], agent)['included'])

    def test_original_metadata_is_preserved_for_real_source(self):
        task = STRICT_EDU.replace('sources: [mock]', 'sources: [openalex]')
        p = self.live_fixture()
        with patch.object(sources, 'search_paginated', return_value=[p]):
            r = o.review_step(task)
        self.assertTrue(r['ok'])
        ref = r['result']['references'][0]
        self.assertEqual(ref['doi'], p['doi'])
        self.assertEqual(ref['abstract'], p['abstract'])
        self.assertTrue(ref['record_url'].startswith('https://doi.org/'))
        self.assertNotIn('mock://', json.dumps(r))

    def test_mixed_demo_and_real_sources_rejected(self):
        r = o.review_step(STRICT_EDU.replace('[mock]', '[mock, openalex]'))
        self.assertFalse(r['ok'])

    def test_source_failure_persists_failed_run_without_mock(self):
        task = STRICT_EDU.replace('[mock]', '[openalex]')
        with patch.object(sources, 'search_paginated', side_effect=sources.SourceError('fixture network failure')):
            r = o.review_step(task)
        self.assertFalse(r['ok'])
        stored = o.load_run(r['run_id'])
        self.assertEqual(stored['result']['status'], 'failed')
        self.assertFalse(stored['result']['references'])
        self.assertIn('fixture network failure', stored['result']['search'][0]['detail'])

    def test_zero_hits_distinct_from_request_failure(self):
        with patch.object(sources, 'search_paginated', return_value=sources.SearchResults(total=0, limit=10)):
            r = o.review_step(STRICT_EDU.replace('[mock]', '[openalex]'))
        self.assertTrue(r['ok'])
        self.assertEqual(r['result']['search'][0]['status'], '未命中')
        self.assertEqual(r['result']['stats']['adopted'], 0)

    def test_page_count_is_not_database_total_and_envelope_persists(self):
        batch = sources.SearchResults([self.live_fixture()], total=1234, limit=50,
                                      request_url='https://example.org/?limit=50', response_meta={'count':1234})
        with patch.object(sources, 'search_paginated', return_value=batch):
            r = o.review_step(STRICT_EDU.replace('[mock]', '[openalex]'))
        saved = json.loads((Path(r['artifacts']['run_dir'])/'raw_openalex.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['retrieved_count'], 1)
        self.assertEqual(saved['total_count'], 1234)
        self.assertEqual(saved['response_meta']['count'], 1234)
        self.assertTrue(saved['truncated'])
        self.assertEqual(r['result']['stats']['retrieved'], 1)
        self.assertIn('本批获取 1 条', r['result']['search_line'])
        self.assertIn('总匹配 1234', r['result']['search_line'])

    def test_missing_total_is_unknown_even_when_fifty_records_returned(self):
        batch = sources.SearchResults([self.live_fixture() for _ in range(50)], limit=50)
        with patch.object(sources, 'search_paginated', return_value=batch):
            r = o.review_step(STRICT_EDU.replace('[mock]', '[openalex]'))['result']
        self.assertEqual(r['stats']['retrieved'], 50)
        self.assertEqual(r['stats']['dedup'], 1)
        self.assertIsNone(r['search'][0]['total_count'])
        self.assertIn('总匹配 未知', r['search_line'])
        self.assertNotIn('命中 50', r['search_line'])

    def test_empty_batch_with_unknown_total_is_not_zero_database_matches(self):
        with patch.object(sources, 'search_paginated', return_value=[]):
            r = o.review_step(STRICT_EDU.replace('[mock]', '[openalex]'))['result']
        self.assertEqual(r['search'][0]['status'], '未获取记录')
        self.assertIsNone(r['search'][0]['total_count'])

    def test_openalex_adapter_retains_meta_count_and_original_record(self):
        response = {'meta':{'count':789, 'per_page':50}, 'results':[{'title':'Fixture', 'id':'https://openalex.org/W1'}]}
        with patch.object(sources, '_get_json', return_value=response):
            r = sources.search_openalex('attention', (2020,2024), 50)
        self.assertEqual(r.total, 789)
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]['source_record'], response['results'][0])
        self.assertIn('from_publication_date', r.request_url)

    def test_semantic_adapter_retains_total_and_sends_year_filter(self):
        response = {'total':345,'offset':0,'next':50,'data':[{'title':'Fixture','year':2023}]}
        with patch.object(sources, '_get_json', return_value=response):
            r = sources.search_semantic_scholar('attention', (2020,2024), 50)
        self.assertEqual(r.total, 345)
        self.assertEqual(r.response_meta['next'], 50)
        self.assertIn('year=2020-2024', r.request_url)

    def test_pubmed_empty_page_retains_positive_count_instead_of_false_zero(self):
        with patch.object(sources, '_get_json', return_value={'esearchresult':{'count':'123','idlist':[]}}):
            r = sources.search_pubmed('attention', (2020,2024), 10)
        self.assertEqual(r.total, 123)
        self.assertEqual(len(r), 0)

    def test_mesh_changes_do_not_claim_to_change_non_pubmed_queries(self):
        task = STRICT_EDU.replace('[mock]', '[openalex]')
        with patch.object(sources, 'search_paginated', return_value=[]) as search:
            a = o.review_step(task, query='attention[MeSH Terms]')
            b = o.review_step(task, query='attention[MeSH Terms] AND learning[MeSH Terms]')
        self.assertEqual(search.call_args_list[0].args[1]['topic'], search.call_args_list[1].args[1]['topic'])
        self.assertTrue(any('未用于本次检索' in n for n in b['result']['notes']))
        self.assertEqual(a['result']['search'][0]['query_origin'], 'planned_keywords')

    def test_default_topic_screen_does_not_block_on_canned_conditions(self):
        task = o.draft_step('Study sustained attention in college students using short-video apps')['yaml']
        with patch.object(sources, 'search_paginated', return_value=[self.live_fixture()]):
            response = o.review_step(task)
        r = response['result']
        self.assertEqual(r['stats']['included'], 1)
        self.assertEqual(r['stats']['provisional_adopted'], 0)
        self.assertEqual(len(r['references']), 1)
        self.assertEqual(r['references'][0]['decision'], 'included')
        self.assertFalse(r['references'][0]['pending_criteria'])
        self.assertTrue((Path(response['artifacts']['run_dir'])/'candidates.csv').exists())
        self.assertIn(self.live_fixture()['abstract'], json.dumps(r['sections'], ensure_ascii=False))
        self.assertEqual(r['screening_capability']['mode'], 'topic_relevance')
        self.assertFalse(r['screening_capability']['uninterpreted_rules'])
        check = o.verify_step([], online=False, run_id=response['run_id'])
        self.assertEqual(len(check['refs']), 1)
        stored = o.load_run(response['run_id'])['result']
        self.assertEqual(stored['stats']['included'], 1)

    def test_relaxed_topic_screen_keeps_off_topic_out_of_materials(self):
        yaml = o.draft_step('短视频使用对大学生注意力的影响')['yaml']
        task, _ = agent.parse_task(yaml)
        off_topic = dict(self.live_fixture(),title='Building crack detection',abstract='Deep learning identifies cracks in concrete.',doi='10.1234/other')
        papers = [self.live_fixture(), off_topic]
        original = agent._screen(task, copy.deepcopy(papers))
        self.assertEqual(original['included'], 2)  # Legacy empty rules have no topic check.
        with patch.object(sources,'search_paginated',return_value=papers):
            response = o.review_step(yaml)
        result = response['result']
        self.assertEqual(result['stats']['included'], 1)
        self.assertEqual(result['stats']['shortlisted'], 0)
        self.assertEqual(len(result['references']), 1)
        self.assertEqual(result['references'][0]['title'], self.live_fixture()['title'])
        self.assertNotIn('Building crack', json.dumps(result['sections']))

    def test_explicit_missing_criterion_cannot_enter_candidate_draft(self):
        task, _ = agent.parse_task(STRICT_EDU)
        task['inclusion'] = ['unmatchedmandatorycriterion']
        result = e.build_result(task, [self.live_fixture()], [], agent)
        self.assertEqual(result['stats']['shortlisted'], 0)
        self.assertFalse(result['references'])

    def test_manual_uncertain_vetoes_automatic_candidate_selection(self):
        yaml = o.draft_step('短视频使用对大学生注意力的影响')['yaml']
        with patch.object(sources,'search_paginated',return_value=[self.live_fixture()]):
            parent = o.review_step(yaml)
        record_id = parent['result']['references'][0]['record_id']
        child = o.review_decisions(parent['run_id'],{record_id:{'decision':'uncertain','reason':'人工认为暂不进入材料稿'}})
        self.assertFalse(child['result']['references'])
        self.assertEqual(len(o.load_run(parent['run_id'])['result']['references']),1)

    def test_single_unmapped_word_cannot_match_an_entire_topic(self):
        task, _ = agent.parse_task(STRICT_EDU)
        task['topic'] = 'antibiotic resistance'
        paper = dict(self.live_fixture(),title='Mechanical resistance',abstract='Resistance of concrete structures was measured.')
        self.assertFalse(e.build_result(task,[paper],[],agent)['references'])

    def test_long_english_topic_is_not_truncated(self):
        question = 'Does deep learning improve precision classification and endotyping of knee osteoarthritis using imaging or multi-omics data?'
        draft = o.draft_step(question)
        self.assertTrue(draft['ok'])
        task, errors = agent.parse_task(draft['yaml'])
        self.assertFalse(errors)
        self.assertEqual(task['topic'], question)

    def test_legacy_restore_corrects_labels_without_inventing_missing_totals(self):
        r = self.run_edu()
        r['result']['search'] = [{'source':'openalex','query':'legacy','status':'命中','count':50,'records':[]}]
        o.run_store.save(self.temp.name, r['run_id'], r)
        restored = o.load_run(r['run_id'])
        self.assertIn('本批获取 50 条', restored['result']['search_line'])
        self.assertIn('总匹配 未知', restored['result']['search_line'])
        self.assertNotIn('total_count', o.run_store.load(self.temp.name,r['run_id'])['result']['search'][0])

    def test_pubmed_count_uses_task_time_window_and_rejects_error_response(self):
        with patch.object(o, '_get_json', return_value={'esearchresult':{'count':'100'}}) as get:
            r = o.count_step('attention[Title]', STRICT_EDU)
        self.assertTrue(r['ok'])
        self.assertIn('2019/01/01', r['query'])
        self.assertIn('term=', get.call_args.args[0])
        with patch.object(o,'_get_json',return_value={'esearchresult':{'count':'0','errorlist':{'fieldsnotfound':['bad']}}}):
            self.assertFalse(o.count_step('bad[Field]')['ok'])

    def test_artifacts_include_actual_query_and_evidence(self):
        r = self.run_edu()
        folder = Path(r['artifacts']['run_dir'])
        for name in ('task.yaml','review.md','references.json','screening.json','evidence.json',
                     'search_records.json','raw_mock.json','retrieval_plan.txt','included.csv',
                     'excluded.csv','uncertain.csv','snapshot.json'):
            self.assertTrue((folder/name).exists(), name)

    def test_isolation_restore_and_archive(self):
        a, b = self.run_edu(), o.review_step(d.TASK_YAML)
        self.assertNotEqual(a['run_id'], b['run_id'])
        v = o.verify_step([{'title':'wrong input'}], online=False, run_id=a['run_id'])
        self.assertEqual([r['record_id'] for r in v['refs']], [r['record_id'] for r in a['result']['references']])
        self.assertIsNone(o.load_run(b['run_id'])['verification'])
        self.assertIsNotNone(o.load_run(a['run_id'])['verification'])
        self.assertEqual(len(o.list_runs()['runs']), 2)
        o.archive_run(a['run_id'])
        self.assertEqual(len(o.list_runs()['runs']), 1)
        self.assertTrue((Path(self.temp.name)/('.archived-'+a['run_id'])/'snapshot.json').exists())

    def test_manual_decision_creates_child_and_preserves_original(self):
        parent = self.run_edu()
        record_id = parent['result']['references'][0]['record_id']
        child = o.review_decisions(parent['run_id'], {record_id:{'decision':'excluded','reason':'人工阅读后确认不符合'}})
        self.assertNotEqual(child['run_id'], parent['run_id'])
        self.assertFalse(child['result']['references'])
        self.assertTrue(o.load_run(parent['run_id'])['result']['references'])
        self.assertEqual(child['result']['parent_run_id'], parent['run_id'])

    def test_cross_run_manual_decision_rejected(self):
        parent = self.run_edu()
        r = o.review_decisions(parent['run_id'], {'0'*24:{'decision':'included','reason':'invalid'}})
        self.assertFalse(r['ok'])

    def test_explicit_queries_reach_source(self):
        task = STRICT_EDU.replace('[mock]', '[pubmed]') + '\nqueries:\n  pubmed: attention[Title]\n'
        with patch.object(sources, 'search_paginated', return_value=[]) as search:
            r = o.review_step(task, query='selected mesh')
        self.assertTrue(r['ok'])
        self.assertEqual(search.call_args.kwargs['query'], 'attention[Title]')
        self.assertEqual(r['result']['search'][0]['query'], 'attention[Title]')

    def test_selected_mesh_query_used_for_pubmed(self):
        task = STRICT_EDU.replace('[mock]', '[pubmed]')
        with patch.object(sources, 'search_paginated', return_value=[]) as search:
            o.review_step(task, query='"Attention"[MeSH Terms]')
        self.assertEqual(search.call_args.kwargs['query'], '"Attention"[MeSH Terms]')

    def test_verification_404_is_not_nonexistence(self):
        ref = self.live_fixture()
        failure = urllib.error.HTTPError('url',404,'not found',{},None)
        with patch.object(o, '_get_json', side_effect=[failure, {'esearchresult':{'idlist':[]}}]):
            self.assertEqual(o._verify_one(ref)['verdict'], '未检出')

    def test_network_failure_never_uses_demo_cache(self):
        with patch.object(o, '_get_json', side_effect=TimeoutError('timeout')):
            r = o._verify_one(d.DEMO_REFS[0])
        self.assertEqual(r['verdict'], '暂无法核验')

    def test_verification_checks_title_author_and_publication_years(self):
        ref = {'doi':'10.1234/test-fixture','title':'Real fixture title','author':'Tester A','year':2023}
        response = {'message':{'title':[ref['title']], 'author':[{'family':'Tester'}],
                               'published-online':{'date-parts':[[2022]]},'published-print':{'date-parts':[[2023]]}}}
        with patch.object(o,'_get_json',return_value=response):
            self.assertEqual(o._verify_one(ref)['verdict'], '通过')
            self.assertEqual(o._verify_one(dict(ref,author='Other B'))['verdict'], '指向他文')
            self.assertEqual(o._verify_one(dict(ref,title='Other work'))['verdict'], '指向他文')

    def test_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            o.load_run('../other')

    def test_real_metadata_without_identifier_is_not_fabricated(self):
        p = self.live_fixture(); p['doi']=''; p['url']=''
        ref = e.normalize(p)
        self.assertEqual(ref['identifier'], '')
        self.assertEqual(ref['record_url'], '')

    def test_full_flow_requires_explicit_task(self):
        self.assertFalse(o.run_all(pico='anything')['ok'])

    def test_frontend_script_binding_restore_and_reset(self):
        r = self.run_edu()
        fixture = Path(self.temp.name)/'frontend-fixture.json'
        fixture.write_text(json.dumps(r,ensure_ascii=False),encoding='utf-8')
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(['node',str(root/'tests/test_frontend.cjs'),str(root/'index.html'),str(fixture)],
                                capture_output=True,text=True,encoding='utf-8',timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)


class HttpTests(RunFixture):
    def test_http_roundtrip_history_restore_and_removed_health(self):
        server = http_service.ThreadingHTTPServer(('127.0.0.1',0),http_service.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url='http://127.0.0.1:%d'%server.server_address[1]
        def request(path,payload=None):
            data=json.dumps(payload).encode() if payload is not None else None
            req=urllib.request.Request(url+path,data=data,headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(req) as response:
                return json.load(response)
        r=request('/api/review',{'task_yaml':STRICT_EDU,'query':'fixture'})
        self.assertTrue(r['ok'])
        verified=request('/api/verify',{'run_id':r['run_id'],'online':False})
        self.assertEqual(verified['run_id'],r['run_id'])
        self.assertEqual(request('/api/runs')['runs'][0]['run_id'],r['run_id'])
        self.assertEqual(request('/api/run?id='+r['run_id'])['verification'],verified)
        with self.assertRaises(urllib.error.HTTPError) as error:
            request('/api/health',{'module':'sleep'})
        self.assertEqual(error.exception.code,404)
        error.exception.close()


if __name__ == '__main__':
    unittest.main()
