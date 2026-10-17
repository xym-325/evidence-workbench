"""Policy behavior tests; fixture papers are not publication assertions."""
import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import evidence_policy as p
import research_engine as e


class EvidencePolicyTests(unittest.TestCase):
    def setUp(self):
        self.task = {"topic": "低剂量CT肺结节跨机型检测", "max_papers": 25}
        self.groups = e.topic_groups(self.task['topic'])

    def paper(self, index=1, **changes):
        result = {"title": 'Cross-vendor low-dose CT pulmonary nodule detection '+str(index),
                  "abstract": 'We evaluated pulmonary nodule detection on low-dose CT. Cross-vendor testing used independent patients and a reference standard. Sensitivity was measured with confidence intervals.',
                  "year": 2024, "doi": '10.1234/fixture-'+str(index), "source": 'pubmed',
                  "decision": 'included', "relevance_score": 5}
        result.update(changes)
        return e.normalize(result)

    def test_realistic_pool_produces_five_to_twenty_without_filling_all_slots(self):
        selected, summary = p.select([self.paper(i) for i in range(40)], self.task, self.groups)
        self.assertGreaterEqual(len(selected), 5)
        self.assertLessEqual(len(selected), 20)
        self.assertEqual(len(selected), 12)
        self.assertEqual(summary['certainty'], '未评估；推荐数量不代表证据确定性')

    def test_two_eligible_records_are_not_padded_to_five(self):
        selected, summary = p.select([self.paper(1), self.paper(2)], self.task, self.groups)
        self.assertEqual(len(selected), 2)
        self.assertIn('不足5', summary['status'])

    def test_missing_abstract_is_pending_not_negative_evidence(self):
        a = p.assess(self.paper(abstract=''), self.task, self.groups)
        self.assertEqual(a['role'], 'pending')
        self.assertFalse(a['eligible_for_recommendation'])
        self.assertEqual(a['certainty'], 'not_assessed')

    def test_adjacent_task_is_background_not_detection_validation(self):
        a = p.assess(self.paper(title='Cross-vendor low-dose lung nodule malignancy risk classification'), self.task, self.groups)
        self.assertEqual(a['role'], 'background')
        self.assertTrue(a['eligible_for_recommendation'])

    def test_lung_cancer_risk_prediction_is_not_nodule_detection(self):
        a = p.assess(self.paper(title='Integrating Deep Learning of Low-Dose CT With Clinical Data for Lung Cancer Risk Prediction'), self.task, self.groups)
        self.assertEqual(a['role'], 'background')
        self.assertTrue(a['operation_mismatch'])

    def test_negative_result_is_not_excluded_or_relabelled_support(self):
        a = p.assess(self.paper(abstract='Low-dose CT pulmonary nodule cross-vendor evaluation found no improvement in sensitivity.'), self.task, self.groups)
        self.assertEqual(a['role'], 'direct')
        self.assertEqual(a['claim_direction'], 'not_assessed')

    def test_reporting_quote_is_exact_and_absence_is_unknown(self):
        paper = self.paper()
        a = p.assess(paper, self.task, self.groups)
        for f in a['reporting_fields'].values():
            for quote in f['quotes']:
                self.assertIn(quote, paper['abstract'])
        self.assertIn('未评估全文', a['reporting_fields']['study_design']['status'])

    def test_demo_cannot_become_a_real_recommendation(self):
        selected, _ = p.select([self.paper(source='mock')], self.task, self.groups)
        self.assertEqual(selected, [])

    def test_ct_question_does_not_recommend_a_chest_xray_paper(self):
        selected, _ = p.select([self.paper(title='Nodule detection on chest X-rays')], self.task, self.groups)
        self.assertEqual(selected, [])

    def test_dataset_is_kept_as_resource_and_not_counted_as_paper(self):
        dataset = self.paper(source_record={'type': 'dataset'})
        selected, _ = p.select([dataset], self.task, self.groups)
        self.assertEqual(selected, [])
        self.assertTrue(dataset['question_assessment']['resource_only'])
        self.assertIn('不计入', dataset['question_assessment']['reason'])
        preprint = self.paper(source_record={'type': 'preprint'})
        self.assertTrue(p.assess(preprint, self.task, self.groups)['eligible_for_recommendation'])

    def test_distant_classification_does_not_outrank_detection_method(self):
        classify = self.paper(1, title='Low-dose cross-vendor pulmonary nodule malignancy classification', relevance_score=100)
        detect = self.paper(2)
        selected, _ = p.select([classify, detect], self.task, self.groups)
        self.assertEqual(selected[0]['doi'], detect['doi'])

    def test_no_proposition_is_invented_for_a_topic(self):
        papers, _ = p.select([self.paper()], self.task, self.groups)
        overview = p.overview({'topic': self.task['topic'], 'references': papers})
        self.assertEqual(overview['mode'], 'question_map')
        self.assertEqual(overview['claim_direction'], 'not_assessed')
        self.assertNotIn('support', overview)
        self.assertEqual(overview['direct'], 1)

    def test_budget_exposes_fractional_engineering_indicators(self):
        history = [{'new_eligible': 12, 'returned': 50, 'new_eligible_rate': .24}]
        decision = p.budget_decision([self.paper(i) for i in range(12)], self.task, self.groups, history)
        self.assertTrue(decision['stop'])
        self.assertGreater(decision['observed_new_eligible_rate'], 0)
        self.assertLess(decision['observed_new_eligible_rate'], 1)
        self.assertIn('不是医学', decision['threshold_basis'])

    def test_scarce_material_does_not_trigger_evidence_sufficiency(self):
        history = [{'new_eligible': 1, 'returned': 50, 'new_eligible_rate': .02}]
        decision = p.budget_decision([self.paper()], self.task, self.groups, history)
        self.assertFalse(decision['stop'])
        self.assertIn('不足5', decision['status'])


if __name__ == '__main__':
    unittest.main()
