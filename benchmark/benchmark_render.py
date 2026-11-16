"""Render CSV, Markdown and SVG from a benchmark results JSON file."""
import argparse
import csv
import json
from pathlib import Path


def server_value(explain):
    return explain.get('serverMedianMs', explain.get('executionTimeMillisEstimate')
                            if explain.get('executionTimeMillis') is None
                            else explain.get('executionTimeMillis'))


def render(results_path):
    results_path = Path(results_path)
    out = results_path.parent
    rows = json.loads(results_path.read_text(encoding='utf-8'))
    if not rows:
        raise ValueError('Không có kết quả benchmark')
    with (out / 'benchmark_table.csv').open('w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(['scale', 'query', 'phase', 'server_median_ms', 'server_metric',
                         'client_median_ms', 'client_mean_ms', 'docs_examined',
                         'keys_examined', 'sort'])
        for item in rows:
            for phase in ('before', 'after'):
                for name, query in item.get(phase, {}).get('queries', {}).items():
                    explain = query.get('explain', {})
                    writer.writerow([item['scale'], name, phase, server_value(explain),
                        'executionTimeMillisEstimate' if name == 'Q4_teacher_report'
                        else 'executionTimeMillis',
                        round(query['timing']['medianMs'], 3),
                        round(query['timing']['meanMs'], 3),
                        explain.get('totalDocsExamined'),
                        explain.get('totalKeysExamined'), explain.get('hasSort')])
    lines = ['# Kết quả benchmark', '',
             'Server: trung vị các lần `explain`, không gồm truyền dữ liệu qua mạng. '
             'Q4 dùng ước lượng của stage cuối aggregation (≈). '
             'Ứng dụng: trung vị các lần chạy thật đo bằng `perf_counter`.', '',
             '| Tổng document | Truy vấn | Server trước → sau (ms) | '
             'Ứng dụng trước → sau (ms) | Docs trước → sau | SORT trước → sau |',
             '|---:|---|---:|---:|---:|---|']
    chart_rows = []
    for item in rows:
        before = item.get('before', {}).get('queries', {})
        after = item.get('after', {}).get('queries', {})
        for name in sorted(set(before) | set(after)):
            a, b = before.get(name), after.get(name)
            def show(query, key):
                if query is None:
                    return '—'
                value = query.get('explain', {}).get(key)
                return '—' if value is None else str(value)
            def server(query):
                if query is None:
                    return '—'
                value = server_value(query.get('explain', {}))
                return '—' if value is None else ('≈' if name == 'Q4_teacher_report' else '') + f'{value:g}'
            def client(query):
                return '—' if query is None else f'{query["timing"]["medianMs"]:.2f}'
            lines.append(f'| {item["scale"]:,} | {name} | {server(a)} → {server(b)} | '
                         f'{client(a)} → {client(b)} | '
                         f'{show(a, "totalDocsExamined")} → {show(b, "totalDocsExamined")} | '
                         f'{show(a, "hasSort")} → {show(b, "hasSort")} |')
            if a and b:
                chart_rows.append((item['scale'], name, server_value(a['explain']),
                                   server_value(b['explain'])))
    lines += ['', 'Xem [results.json](results.json) để biết từng lần đo, key/document đã quét '
              'và winning plan. Một lượt chỉ có phase `after` được giữ riêng trong `runs/`.', '']
    (out / 'benchmark_summary.md').write_text('\n'.join(lines), encoding='utf-8')
    method = ('Dữ liệu được nạp lũy tiến trên cùng database; `createdAt` tăng liên tục. '
              'Các mốc được đo với cùng ID mẫu trước và sau khi tạo 9 index phụ. ' if all('before' in item for item in rows) else
              'Lượt này chỉ đo phase `after` trên bộ dữ liệu và index đã có. ')
    report = ['# Báo cáo thực nghiệm index', '',
              '## Phương pháp', '',
              method + 'Thời gian server là trung vị các lượt `explain`; '
              'thời gian ứng dụng là trung vị `perf_counter`.', '',
              '## Kết quả', '', *lines[4:]]
    if rows[-1].get('before'):
        latest = rows[-1]
        q4_before = latest['before']['queries'].get('Q4_teacher_report')
        q4_after = latest['after']['queries'].get('Q4_teacher_report')
        if q4_before and q4_after:
            old = server_value(q4_before['explain'])
            new = server_value(q4_after['explain'])
            report += ['', '## Nhận xét từ mốc lớn nhất', '',
                       f'Q4 giảm từ khoảng {old:g} xuống {new:g} ms trên server '
                       f'(xấp xỉ {old/new:.1f} lần) ở mốc {latest["scale"]:,}.']
        regex_before = latest['before']['queries'].get('regex_hot')
        regex_after = latest['after']['queries'].get('regex_hot')
        if regex_before and regex_after:
            a = regex_before['explain']
            b = regex_after['explain']
            regex_note = ('Ở lượt này, thời gian server có index cao hơn; không thể kết luận regex được tăng tốc.'
                          if server_value(b) >= server_value(a) else
                          'Thời gian server giảm trong lượt đo này.')
            report += [f'Tìm kiếm `$regex` hiếm quét {a["totalDocsExamined"]:,} → '
                       f'{b["totalDocsExamined"]:,} document; trung vị server '
                       f'{server_value(a):g} → {server_value(b):g} ms. '
                       'Index khoanh vùng được chủ đề nhưng vẫn phải xét gần hết bài trong đó. '
                       + regex_note, '']
    report += ['', '## Giới hạn', '',
               'Trung vị `explain` không tính truyền dữ liệu về ứng dụng nhưng vẫn chịu tác động '
               'của tải server và bỏ qua plan cache. Q4 dùng `executionTimeMillisEstimate`, '
               'khác chỉ số của Q1–Q3. Xem JSON để đối chiếu phân bố các mẫu đo.', '']
    if any('regex_hot' in item.get('after', {}).get('queries', {}) for item in rows):
        report += ['Phép `$regex` dùng từ khóa hiếm chỉ khớp bài cũ nhất trong chủ đề lớn. '
                   'Có `limit(11)` nhưng vẫn phải duyệt gần hết chủ đề để tìm kết quả.', '']
    if 'sizes' in rows[-1]:
        latest = rows[-1]
        data_bytes = sum(value['dataSize'] for value in latest['sizes'].values())
        index_bytes = sum(value['indexSize'] for value in latest['sizes'].values())
        report += [f'Ở mốc {latest["scale"]:,}, `dataSize` = {data_bytes:,} byte, '
                   f'`totalIndexSize` = {index_bytes:,} byte. Đây là dung lượng lưu trữ, '
                   'không phải RAM đang dùng.', '']
    (out / 'benchmark_report.md').write_text('\n'.join(report), encoding='utf-8')
    # Server-time chart: one panel per query, x-axis is the actual selected scales.
    names = sorted({name for _, name, _, _ in chart_rows})
    width, panel_h = 900, 220
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
             f'height="{60 + panel_h * len(names)}">',
             f'<rect width="{width}" height="{60 + panel_h * len(names)}" fill="white"/>',
             '<text x="30" y="30" font-size="20" font-family="sans-serif">'
             'Trung vị thời gian server từ explain (ms)</text>']
    for index, name in enumerate(names):
        subset = [(scale, before, after) for scale, query, before, after in chart_rows if query == name]
        y0 = 55 + index * panel_h
        maximum = max(max(before, after) for _, before, after in subset) or 1
        parts.append(f'<text x="30" y="{y0+20}" font-size="15" font-family="sans-serif">{name}</text>')
        for phase, color, position in [('before', '#2563eb', 1), ('after', '#dc2626', 2)]:
            points = []
            for i, (scale, before, after) in enumerate(subset):
                value = before if phase == 'before' else after
                x = 120 + i * (650 / max(1, len(subset)-1))
                y = y0 + 175 - value / maximum * 125
                points.append(f'{x:.1f},{y:.1f}')
                parts += [f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{color}"/>',
                          f'<text x="{x-8:.1f}" y="{y-8:.1f}" fill="{color}" '
                          f'font-size="11" font-family="sans-serif">{value:g}</text>']
                if position == 1:
                    parts.append(f'<text x="{x-12:.1f}" y="{y0+200}" font-size="11" '
                                 f'font-family="sans-serif">{scale//1000}k</text>')
            if len(points) > 1:
                parts.append(f'<polyline points="{" ".join(points)}" fill="none" '
                             f'stroke="{color}" stroke-width="2"/>')
    parts.append('</svg>')
    (out / 'benchmark_chart.svg').write_text(''.join(parts), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--results', default='benchmark_results/results.json')
    render(parser.parse_args().results)
