"""Checks benchmark data budgets and Q4 explain extraction without MongoDB access."""
import os
import io
import json
import tempfile
from pathlib import Path
from contextlib import redirect_stderr
import unittest
from unittest.mock import MagicMock, patch

from bson import ObjectId

os.environ['MONGO_URI'] = 'mongodb://unit-test.invalid:27017'

from benchmark import benchmark as runner
from benchmark.benchmark_render import render
from benchmark.benchmark_seed import SCALES, post_created_at, state_counts


class BenchmarkTest(unittest.TestCase):
    def test_rare_regex_sample_matches_only_oldest_hot_post(self):
        hot = {'_id': ObjectId()}
        oldest_id = ObjectId()
        fake_db = MagicMock()
        fake_db.posts.count_documents.return_value = 1
        fake_db.posts.find_one.side_effect = [{'_id': oldest_id}, {'_id': oldest_id}]
        with patch.object(runner, 'database', fake_db):
            query = runner.rare_regex_query(hot)
            runner.validate_rare_regex_sample(hot, query)
        self.assertEqual(query['spaceId'], hot['_id'])
        self.assertEqual(query['spaceType'], 'TOPIC')
        self.assertEqual(query['$or'][0]['title']['$regex'], 'ôn\\ thi\\ 0:')
        fake_db.posts.count_documents.assert_called_once_with(query)
        self.assertEqual(fake_db.posts.find_one.call_args.kwargs['sort'],
                         [('createdAt', 1), ('_id', 1)])

    def test_rare_regex_sample_rejects_multiple_or_recent_matches(self):
        hot = {'_id': ObjectId()}
        fake_db = MagicMock()
        query = runner.rare_regex_query(hot)
        with patch.object(runner, 'database', fake_db):
            fake_db.posts.count_documents.return_value = 2
            with self.assertRaises(RuntimeError):
                runner.validate_rare_regex_sample(hot, query)
            fake_db.posts.find_one.assert_not_called()
            fake_db.posts.count_documents.return_value = 1
            fake_db.posts.find_one.side_effect = [{'_id': ObjectId()}, {'_id': ObjectId()}]
            with self.assertRaises(RuntimeError):
                runner.validate_rare_regex_sample(hot, query)

    def test_renderer_handles_single_scale_and_query(self):
        query = {'explain': {'serverMedianMs': 3, 'executionTimeMillis': 3,
                             'totalDocsExamined': 4, 'hasSort': False},
                 'timing': {'medianMs': 12.5, 'meanMs': 13.0}}
        result = [{'scale': 5000, 'before': {'queries': {'Q1_resolved_hot': query}},
                   'after': {'queries': {'Q1_resolved_hot': query}}}]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'results.json'
            path.write_text(json.dumps(result), encoding='utf-8')
            render(path)
            summary = (Path(folder) / 'benchmark_summary.md').read_text()
            self.assertIn('| 5,000 | Q1_resolved_hot | 3 → 3 |', summary)
            self.assertIn('5k', (Path(folder) / 'benchmark_chart.svg').read_text())
            self.assertTrue((Path(folder) / 'benchmark_report.md').exists())

    def test_renderer_handles_after_only_without_inventing_before_values(self):
        query = {'explain': {'serverMedianMs': 2, 'executionTimeMillis': 2},
                 'timing': {'medianMs': 10.0, 'meanMs': 10.0}}
        result = [{'scale': 100000, 'after': {'queries': {'Q1_resolved_hot': query}}}]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'results.json'
            path.write_text(json.dumps(result), encoding='utf-8')
            render(path)
            summary = (Path(folder) / 'benchmark_summary.md').read_text()
            self.assertIn('| 100,000 | Q1_resolved_hot | — → 2 |', summary)
            self.assertIn('phase `after`', (Path(folder) / 'benchmark_report.md').read_text())

    def test_cli_selects_partial_run_and_reuse_requires_after(self):
        args = runner.parse_args(['--scales', '100000', '--queries', 'regex_hot',
                                  '--server-repeats', '10', '--client-repeats', '10'])
        self.assertEqual(args.scales, [100000])
        self.assertEqual(args.queries, ['regex_hot'])
        self.assertEqual(args.server_repeats, 10)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            runner.parse_args(['--reuse-existing'])

    def test_repeated_explain_returns_server_median_and_samples(self):
        values = iter([100, 90, 5, 7, 6])
        with patch.object(runner, 'explain_cursor', side_effect=lambda _: {
            'executionTimeMillis': next(values), 'totalDocsExamined': 2,
        }):
            result = runner.repeated_explain(lambda: object(), 3)
        self.assertEqual(result['serverSamplesMs'], [5, 7, 6])
        self.assertEqual(result['serverMedianMs'], 6)

    def test_q4_explain_extracts_lookup_and_server_estimate(self):
        space = {'_id': ObjectId()}
        raw = {'stages': [
            {'$cursor': {'executionStats': {'totalDocsExamined': 50,
                                            'totalKeysExamined': 50}}},
            {'$lookup': {'from': 'posts'}, 'totalDocsExamined': 4,
             'totalKeysExamined': 4, 'indexesUsed': ['spaceId_1_author.userId_1'],
             'executionTimeMillisEstimate': 2},
            {'$lookup': {'from': 'comments'}, 'totalDocsExamined': 12,
             'totalKeysExamined': 12, 'indexesUsed': ['author.userId_1_postId_1'],
             'executionTimeMillisEstimate': 8},
            {'$project': {}, 'executionTimeMillisEstimate': 8},
        ]}
        with patch.object(runner.database, 'command', return_value=raw) as command, \
             patch.object(runner, 'teacher_report_pipeline', return_value=[{'$match': {}}]):
            result = runner.explain_report(space)
        self.assertEqual(command.call_args.args[0], 'explain')
        self.assertEqual(result['executionTimeMillisEstimate'], 8)
        self.assertEqual(result['cursorDocsExamined'], 50)
        self.assertEqual([item['from'] for item in result['lookups']], ['posts', 'comments'])
        self.assertEqual(result['lookups'][1]['indexesUsed'], ['author.userId_1_postId_1'])


class BenchmarkSeedTest(unittest.TestCase):
    def test_post_timestamps_continue_across_milestones(self):
        self.assertLess(post_created_at(889), post_created_at(890))
        self.assertLess(post_created_at(9729), post_created_at(9730))
        self.assertEqual((post_created_at(9730) - post_created_at(9729)).total_seconds(), 37)

    def test_scale_totals_and_comment_budget(self):
        for scale, (users, classes, topics, posts, comments) in SCALES.items():
            with self.subTest(scale=scale):
                self.assertEqual(users + classes + topics + posts + comments, scale)
                states = state_counts(posts)
                self.assertEqual(sum(states.values()), posts)
                self.assertLessEqual(states['discussing'] * 6 + states['resolved'] * 3, comments)
                self.assertGreaterEqual(states['discussing'] * 7 + states['resolved'] * 3, comments)

    def test_hot_topic_has_all_three_states_at_every_scale(self):
        for scale, (_, _, _, posts, _) in SCALES.items():
            with self.subTest(scale=scale):
                hot = round(posts * .27)
                states = state_counts(hot)
                self.assertTrue(all(value > 0 for value in states.values()))
                self.assertAlmostEqual(states['resolved'] / hot, .4, delta=.01)
                self.assertAlmostEqual(states['discussing'] / hot, .4, delta=.01)
                self.assertAlmostEqual(states['unanswered'] / hot, .2, delta=.01)
