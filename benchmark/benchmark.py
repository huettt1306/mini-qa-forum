"""Reset and benchmark the disposable school_forum data at three scales."""
import argparse
import json
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.db import database, teacher_report, teacher_report_pipeline
from benchmark.benchmark_seed import HOT_TITLE, SCALES, SCALES_NAMES, append_to_scale, validate_scale
from ensure_indexes import ensure_indexes

RARE_REGEX_TERM = 'ôn thi 0:'
OUT = ROOT / 'benchmark_results'
OUT.mkdir(exist_ok=True)
MANIFEST = OUT / "seed_manifest.json"
SEED_VERSION = 2


def stages(value):
    found = []
    if isinstance(value, dict):
        if isinstance(value.get('stage'), str):
            found.append(value['stage'])
        for child in value.values():
            found.extend(stages(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(stages(child))
    return found


def time_query(factory, count=12):
    for _ in range(2):
        list(factory())
    samples = []
    for _ in range(count):
        start = time.perf_counter()
        list(factory())
        samples.append((time.perf_counter() - start) * 1000)
    return {'samplesMs': samples, 'meanMs': statistics.mean(samples),
            'medianMs': statistics.median(samples), 'minMs': min(samples),
            'maxMs': max(samples)}


def explain_cursor(cursor):
    result = cursor.explain()
    stats = result['executionStats']
    plan = result['queryPlanner']['winningPlan']
    names = stages(plan)
    return {'executionTimeMillis': stats['executionTimeMillis'],
            'totalKeysExamined': stats['totalKeysExamined'],
            'totalDocsExamined': stats['totalDocsExamined'],
            'nReturned': stats['nReturned'], 'stages': names,
            'hasSort': 'SORT' in names, 'plan': plan}


def explain_report(space):
    raw = database.command('explain', {
        'aggregate': 'users', 'pipeline': teacher_report_pipeline(space),
        'cursor': {},
    }, verbosity='executionStats')
    stages = raw.get('stages', [])
    cursor = next((stage['$cursor'] for stage in stages if '$cursor' in stage), {})
    cursor_stats = cursor.get('executionStats', {})
    lookups = [stage for stage in stages if stage.get('$lookup')]
    # The last aggregation stage reports elapsed server time as an estimate.
    last = stages[-1] if stages else {}
    return {
        'executionTimeMillis': raw.get('executionStats', {}).get('executionTimeMillis'),
        'executionTimeMillisEstimate': last.get('executionTimeMillisEstimate'),
        'cursorDocsExamined': cursor_stats.get('totalDocsExamined'),
        'cursorKeysExamined': cursor_stats.get('totalKeysExamined'),
        'lookups': [{
            'from': stage['$lookup'].get('from'),
            'totalDocsExamined': stage.get('totalDocsExamined'),
            'totalKeysExamined': stage.get('totalKeysExamined'),
            'indexesUsed': stage.get('indexesUsed'),
            'executionTimeMillisEstimate': stage.get('executionTimeMillisEstimate'),
        } for stage in lookups],
        'stageNames': [next((key for key in stage if key.startswith('$')), 'unknown')
                       for stage in stages],
    }


def repeated_explain(factory, count, kind='cursor'):
    read = explain_report if kind == 'report' else explain_cursor
    for _ in range(2):
        read(factory())
    measurements = [read(factory()) for _ in range(count)]
    key = 'executionTimeMillisEstimate' if kind == 'report' else 'executionTimeMillis'
    times = [row[key] for row in measurements]
    if any(value is None for value in times):
        raise RuntimeError(f'Explain thiếu {key}')
    representative = measurements[len(measurements) // 2].copy()
    representative[key] = statistics.median(times)
    representative['serverSamplesMs'] = times
    representative['serverMedianMs'] = statistics.median(times)
    return representative


def sample_ids():
    hot = database.topics.find_one({'title': HOT_TITLE})
    assert hot, 'Không có chủ đề lớn'
    space = database.subject_classes.find_one()
    assert space, 'Không có lớp môn'
    post = database.posts.find_one({'spaceId': hot['_id'], 'commentCount': {'$gt': 0}})
    assert post, 'Chủ đề lớn không có phản hồi'
    mine = database.posts.find_one({'spaceId': space['_id']})
    assert mine, 'Lớp môn không có bài viết'
    return hot, space, post, mine


def query_factories(hot, post, mine):
    q1 = lambda: database.posts.find({'spaceType': 'TOPIC', 'spaceId': hot['_id'],
                                      'correctCommentCount': {'$gt': 0}}).sort(
        [('createdAt', -1), ('_id', -1)]).limit(11)
    q2 = lambda: database.comments.find({'postId': post['_id']}).sort(
        [('isCorrect', -1), ('createdAt', 1), ('_id', 1)])
    q3 = lambda: database.posts.find({'spaceType': mine['spaceType'],
                                      'spaceId': mine['spaceId'],
                                      'author.userId': mine['author']['userId']}).sort(
        [('createdAt', -1), ('_id', -1)]).limit(11)
    return {'Q1_resolved_hot': q1, 'Q2_comments': q2, 'Q3_mine': q3}


def rare_regex_query(hot):
    pattern = re.escape(RARE_REGEX_TERM)
    return {
        'spaceId': hot['_id'], 'spaceType': 'TOPIC',
        '$or': [
            {'title': {'$regex': pattern, '$options': 'i'}},
            {'content': {'$regex': pattern, '$options': 'i'}},
        ],
    }


def validate_rare_regex_sample(hot, query):
    if database.posts.count_documents(query) != 1:
        raise RuntimeError('Từ khóa regex phải khớp đúng một bài trong chủ đề lớn')
    matched = database.posts.find_one(query, {'_id': 1})
    oldest = database.posts.find_one(
        {'spaceId': hot['_id'], 'spaceType': 'TOPIC'}, {'_id': 1},
        sort=[('createdAt', 1), ('_id', 1)],
    )
    if not matched or not oldest or matched['_id'] != oldest['_id']:
        raise RuntimeError('Bài khớp regex phải là bài cũ nhất trong chủ đề lớn')


def measure(phase, scale, ids, queries, server_repeats, client_repeats):
    hot, space, post, mine = ids
    result = {'phase': phase, 'scale': scale,
              'sample': {'hotTopicId': str(hot['_id']), 'classId': str(space['_id']),
                         'postId': str(post['_id']), 'minePostId': str(mine['_id'])},
              'queries': {}}
    for name, factory in query_factories(hot, post, mine).items():
        if name not in queries:
            continue
        result['queries'][name] = {
            'explain': repeated_explain(factory, server_repeats),
            'timing': time_query(factory, client_repeats),
        }
        print(f'{scale} {phase} {name}: {result["queries"][name]["timing"]["medianMs"]:.2f} ms', flush=True)
    if 'Q4_teacher_report' in queries:
        report = lambda: teacher_report(space['teacherId'], 'SUBJECT_CLASS', space['_id'])
        result['queries']['Q4_teacher_report'] = {
            'explain': repeated_explain(lambda: space, server_repeats, 'report'),
            'timing': time_query(report, client_repeats),
        }
        print(f'{scale} {phase} Q4_teacher_report: '
              f'{result["queries"]["Q4_teacher_report"]["timing"]["medianMs"]:.2f} ms', flush=True)
    if scale == 100000 and 'regex_hot' in queries:
        query = rare_regex_query(hot)
        validate_rare_regex_sample(hot, query)
        regex = lambda: database.posts.find(query).sort(
            [('createdAt', -1), ('_id', -1)]).limit(11)
        result['queries']['regex_hot'] = {
            'keyword': RARE_REGEX_TERM,
            'explain': repeated_explain(regex, server_repeats),
            'timing': time_query(regex, client_repeats),
        }
    return result


ALL_QUERIES = ('Q1_resolved_hot', 'Q2_comments', 'Q3_mine',
               'Q4_teacher_report', 'regex_hot')


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scales', nargs='+', type=int, choices=tuple(SCALES),
                        default=list(SCALES))
    parser.add_argument('--queries', nargs='+', choices=ALL_QUERIES,
                        default=list(ALL_QUERIES))
    parser.add_argument('--server-repeats', type=int, default=12)
    parser.add_argument('--client-repeats', type=int, default=12)
    parser.add_argument('--reuse-existing', action='store_true')
    parser.add_argument('--phase', choices=('both', 'after'), default='both')
    parser.add_argument('--keep-data', action='store_true')
    args = parser.parse_args(argv)
    if args.server_repeats < 1 or args.client_repeats < 1:
        parser.error('Số lần đo phải lớn hơn 0')
    if args.reuse_existing != (args.phase == 'after'):
        parser.error('--reuse-existing và --phase after phải đi cùng nhau')
    if args.reuse_existing and len(args.scales) != 1:
        parser.error('Chế độ reuse chỉ nhận một mốc')
    return args


def output_dir(args):
    if (args.scales == list(SCALES) and set(args.queries) == set(ALL_QUERIES)
            and not args.reuse_existing):
        return OUT
    from datetime import datetime, timezone
    suffix = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    return OUT / 'runs' / suffix


def verify_indexes():
    from ensure_indexes import INDEXES
    for name, definitions in INDEXES.items():
        current = database[name].index_information()
        for index_name, keys, unique in definitions:
            actual = current.get(index_name)
            if not actual or list(actual['key']) != keys or bool(actual.get('unique', False)) != unique:
                raise RuntimeError(f'Thiếu hoặc sai index {name}.{index_name}')


def dataset_signature(scale, ids):
    newest = database.posts.find_one(sort=[('createdAt', -1)], projection={'_id': 1})
    return {'seedVersion': SEED_VERSION, 'scale': scale,
            'hotTopicId': str(ids[0]['_id']), 'classId': str(ids[1]['_id']),
            'postId': str(ids[2]['_id']), 'minePostId': str(ids[3]['_id']),
            'newestPostId': str(newest['_id']) if newest else None}


def verify_manifest(scale, ids):
    if not MANIFEST.exists():
        raise RuntimeError('Thiếu manifest dữ liệu benchmark để dùng --reuse-existing')
    expected = json.loads(MANIFEST.read_text(encoding='utf-8'))
    if expected != dataset_signature(scale, ids):
        raise RuntimeError('Database không khớp manifest dữ liệu benchmark')


def run(args=None):
    args = args or parse_args()
    destination = output_dir(args)
    destination.mkdir(parents=True, exist_ok=True)
    results = []
    selected = set(args.queries)
    try:
        if args.reuse_existing:
            scale = args.scales[0]
            validate_scale(database, scale)
            verify_indexes()
            ids = sample_ids()
            verify_manifest(scale, ids)
            after = measure('after', scale, ids, selected,
                            args.server_repeats, args.client_repeats)
            results.append({'scale': scale, 'counts': validate_scale(database, scale),
                            'after': after})
        else:
            # Exactly one initial reset; later milestones append without replacing IDs.
            for name in database.list_collection_names():
                database.drop_collection(name)
            ids = None
            for scale in SCALES:
                print(f'=== Append to {scale} ===', flush=True)
                if scale != 5000:
                    for name in SCALES_NAMES:
                        database[name].drop_indexes()
                append_to_scale(database, scale)
                if ids is None:
                    ids = sample_ids()
                MANIFEST.write_text(json.dumps(dataset_signature(scale, ids)), encoding='utf-8')
                if scale not in args.scales:
                    continue
                before = measure('before', scale, ids, selected,
                                 args.server_repeats, args.client_repeats)
                ensure_indexes()
                after = measure('after', scale, ids, selected,
                                args.server_repeats, args.client_repeats)
                stats = {name: database.command('collStats', name) for name in SCALES_NAMES}
                results.append({'scale': scale, 'counts': validate_scale(database, scale),
                                'before': before, 'after': after,
                                'sizes': {name: {'dataSize': value['size'],
                                                 'indexSize': value['totalIndexSize']}
                                          for name, value in stats.items()}})
                (destination / 'results.json').write_text(
                    json.dumps(results, default=str, indent=2), encoding='utf-8')
                subprocess.run([sys.executable, str(ROOT / 'benchmark' / 'benchmark_render.py'),
                                '--results', str(destination / 'results.json')], check=True)
                if scale == max(args.scales):
                    break
        if args.reuse_existing:
            (destination / 'results.json').write_text(
                json.dumps(results, default=str, indent=2), encoding='utf-8')
            subprocess.run([sys.executable, str(ROOT / 'benchmark' / 'benchmark_render.py'),
                            '--results', str(destination / 'results.json')], check=True)
            print(f'After-only result: {destination / "results.json"}', flush=True)
    finally:
        if not args.reuse_existing and not args.keep_data:
            print('=== Restore development seed ===', flush=True)
            subprocess.run([sys.executable, str(ROOT / 'seed.py')], check=True)
            ensure_indexes()
            MANIFEST.unlink(missing_ok=True)
            print('Restored', flush=True)


if __name__ == '__main__':
    run()
