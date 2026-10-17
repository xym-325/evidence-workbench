"""Verify the user's four-page retrieval and relaxed topic defaults."""
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import orchestrator as o
import research_engine as e
import demo_data as d
o._ensure_paths()
import agent
import sources


class HackathonFlowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        target = patch.object(o, 'OUTPUTS_DIR', temp.name)
        target.start()
        self.addCleanup(target.stop)

    def paper(self):
        return {'title': 'Pulmonary nodule detection in chest CT', 'year': 2024,
                'authors': ['Fixture A'], 'doi': '10.1234/controlled-test', 'source': 'pubmed',
                'abstract': 'A pulmonary nodule detector was evaluated on chest CT.'}

    def test_medical_draft_uses_real_sources_without_template_filters(self):
        draft = o.draft_step('低剂量CT肺结节跨机型泛化')
        task, errors = agent.parse_task(draft['yaml'])
        self.assertFalse(errors, errors)
        self.assertEqual(task['sources'], ['pubmed', 'openalex'])
        self.assertEqual(task['inclusion'], [])
        self.assertEqual(task['exclusion'], [])

    def test_all_ui_examples_use_real_sources_and_relaxed_mode(self):
        for item in d.live_examples():
            if not item.get('task_yaml'):
                continue
            task, errors = agent.parse_task(item['task_yaml'])
            self.assertFalse(errors, errors)
            self.assertNotIn('mock', task['sources'])
            self.assertEqual(task['exclusion'], [])
            self.assertIn('screening_mode: topic', item['task_yaml'])

    def test_four_pages_do_not_depend_on_reference_cap(self):
        yaml = d.live_task_yaml(d.LUNG_TASK, ['pubmed']).replace('max_papers: 25', 'max_papers: 1')
        with patch.object(sources, 'search_paginated', return_value=sources.SearchResults([self.paper()], total=1000)) as fetch:
            result = o.review_step(yaml)
        self.assertTrue(result['ok'])
        self.assertEqual(fetch.call_args.kwargs['max_pages'], 4)
        self.assertEqual(fetch.call_args.kwargs['page_size'], 50)
        self.assertEqual(len(result['result']['references']), 1)

    def test_old_imaging_exclusions_do_not_block_topic_mode(self):
        with patch.object(sources, 'search_paginated', return_value=[self.paper()]):
            result = o.review_step(d.LUNG_TASK.replace('[mock]', '[pubmed]'))
        self.assertEqual(result['result']['stats']['included'], 1)
        self.assertEqual(result['result']['screening_capability']['mode'], 'topic_relevance')

    def test_questions_are_not_added_to_actual_search(self):
        task, errors = agent.parse_task(d.LUNG_TASK)
        query = e.topic_queries(task)['pubmed']
        self.assertNotIn('abstention', query)
        self.assertNotIn('workflow', query)
        self.assertNotIn('case-mix', query)

    def test_later_page_failure_keeps_results_and_reports_partial(self):
        pages = [{'page': 1, 'retrieved_count': 1, 'status': 'ok'},
                 {'page': 2, 'retrieved_count': 0, 'status': 'error', 'error': 'HTTP 429'}]
        batch = sources.SearchResults([self.paper()], total=1000, limit=200, pages=pages,
                                      errors=[{'page': 2, 'error': 'HTTP 429'}], status='partial')
        with patch.object(sources, 'search_paginated', return_value=batch):
            result = o.review_step(d.live_task_yaml(d.LUNG_TASK, ['pubmed']))
        self.assertTrue(result['ok'])
        self.assertEqual(result['result']['status'], 'partial')
        self.assertEqual(len(result['result']['references']), 1)
        self.assertIn('HTTP 429', result['result']['search_line'])
        self.assertIn('第1批1条', result['result']['search_line'])
        self.assertEqual(result['result']['search'][0]['request_limit'], 50)

    def test_manual_override_child_keeps_topic_mode(self):
        with patch.object(sources, 'search_paginated', return_value=[self.paper()]):
            parent = o.review_step(d.live_task_yaml(d.LUNG_TASK, ['pubmed']))
        record = parent['result']['references'][0]['record_id']
        child = o.review_decisions(parent['run_id'], {record: {'decision': 'excluded', 'reason': 'test reviewer'}})
        self.assertEqual(child['result']['screening_mode'], 'topic')
        self.assertFalse(child['result']['references'])

    def test_cross_source_missing_doi_deduplicates_and_retains_metadata(self):
        first = dict(self.paper(), doi='', source='openalex', abstract='')
        second = dict(self.paper(), source='pubmed', pmid='12345678')
        unique = list(e.deduplicate([first, second]).values())
        self.assertEqual(len(unique), 1)
        self.assertEqual(unique[0]['doi'], second['doi'])
        self.assertEqual(unique[0]['pmid'], second['pmid'])
        self.assertEqual(unique[0]['abstract'], second['abstract'])
        self.assertEqual(set(unique[0]['retrieved_from']), {'openalex', 'pubmed'})


if __name__ == '__main__':
    unittest.main()
