import argparse
import csv
import json
from pathlib import Path
import struct

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

parser = argparse.ArgumentParser(description='Plot captured EM410x inputs alongside both decoder outputs.')
parser.add_argument('--repo', type=Path, default=Path.cwd())
parser.add_argument('--out', type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parent
out = args.out or root / 'aligned-capture'
metadata = json.loads((out / 'source.json').read_text())
fixture = args.repo / metadata['fixture']
raw = fixture.read_bytes()
intervals = struct.unpack('<' + 'H' * (len(raw) // 2), raw)
states = {}
for version in ('old', 'fixed'):
    with (out / (version + '.csv')).open() as stream:
        states[version] = {int(row['input']): row for row in csv.DictReader(stream)}

red, blue, grey = '#a33b32', '#246384', '#5a626a'
plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 11})

def bits(version, index):
    row = states[version][index]
    return 'ERR' if int(row['bit_count']) < 0 else row['bits']

def draw(start, stop, name, title, show_uid=False):
    columns = list(range(start, stop + 1))
    row_specs = [
        ('Captured bytes\nhex, low byte first', lambda i: raw[2 * (i - 1):2 * i].hex(' ').upper(), None),
        ('Old decoder\nemitted bits', lambda i: bits('old', i), 'old'),
        ('Fixed decoder\nemitted bits', lambda i: bits('fixed', i), 'fixed'),
        ('Old sync flag\nafter this input', lambda i: states['old'][i]['sync_after'], 'old'),
        ('Fixed sync flag\nafter this input', lambda i: states['fixed'][i]['sync_after'], 'fixed'),
    ]
    if show_uid:
        row_specs += [
            ('Old validated ID', lambda i: states['old'][i]['uid'] or '—', 'old'),
            ('Fixed validated ID', lambda i: states['fixed'][i]['uid'] or '—', 'fixed'),
        ]
    fig = plt.figure(figsize=(14, 8 if show_uid else 6.7))
    grid = fig.add_gridspec(1 + len(row_specs), 1, height_ratios=[2.3] + [1] * len(row_specs),
                           left=.15, right=.98, top=.81, bottom=.11, hspace=.04)
    fig.text(.035, .955, title, fontsize=18, weight='bold')
    fig.text(.035, .91, 'Captured receiver input above; production decoder outputs below. Each column is the same recorded input.',
             fontsize=11, color=grey)
    revisions = metadata['commits']
    fig.text(.035, .867, f"Old {revisions['old'][:7]} · Fixed {revisions['fixed'][:7]} · {fixture.name} · 125 kHz carrier",
             fontsize=10, color=grey)
    axis = fig.add_subplot(grid[0])
    values = [intervals[index - 1] for index in columns]
    axis.plot(columns, values, color='#30383f', marker='o', markersize=4, linewidth=1)
    for index, value in zip(columns, values):
        axis.annotate(str(value), (index, value), xytext=(0, 7), textcoords='offset points', ha='center', fontsize=10)
    axis.set(xlim=(start - .5, stop + .5), ylim=(40, 148), yticks=[64, 96, 128])
    axis.set_ylabel('Captured gap\n(carrier periods)', fontsize=10)
    axis.set_xticks(columns)
    axis.xaxis.tick_top()
    axis.tick_params(axis='x', length=0, pad=8)
    axis.set_title('Recorded input number', fontsize=10, pad=30)
    axis.grid(axis='y', color='#e1e5e8', linewidth=.7)
    axis.spines[['top', 'right']].set_visible(False)
    axes = [axis]
    for number, (label, value, version) in enumerate(row_specs, 1):
        row_axis = fig.add_subplot(grid[number], sharex=axis)
        row_axis.set(ylim=(0, 1), yticks=[])
        row_axis.tick_params(axis='x', which='both', bottom=False, top=False, labelbottom=False, labeltop=False)
        row_axis.spines[['top', 'bottom', 'left', 'right']].set_visible(False)
        row_axis.text(-.018, .5, label, transform=row_axis.transAxes, ha='right', va='center',
                      fontsize=13, color=red if version == 'old' else blue if version == 'fixed' else grey)
        for index in columns:
            text = value(index)
            different = False
            if version and 'emitted' in label:
                different = bits('old', index) != bits('fixed', index)
            elif version and 'sync' in label:
                different = states['old'][index]['sync_after'] != states['fixed'][index]['sync_after']
            elif version and 'validated' in label:
                different = states['old'][index]['uid'] != states['fixed'][index]['uid']
            fill = ('#fbeae8' if version == 'old' else '#e5f0f7') if different else '#f5f6f7'
            if text == 'ERR':
                fill = '#fff0d8'
            row_axis.add_patch(Rectangle((index - .48, .05), .96, .9, facecolor=fill, edgecolor='none'))
            row_axis.text(index, .5, text, ha='center', va='center', family='DejaVu Sans Mono',
                          fontsize=14.5,
                          color=red if different and version == 'old' else blue if different and version == 'fixed' else '#20272c',
                          weight='bold' if different or text == 'ERR' else 'normal')
        axes.append(row_axis)
    for chart in axes:
        for edge in range(start, stop + 2):
            chart.axvline(edge - .5, color='#dce1e5', linewidth=.5, zorder=0)
    fig.text(.035, .055, 'ERR = rejected in the current phase. sync = internal phase flag. 10 = two emitted bits. — = no validated ID.',
             fontsize=10)
    fig.text(.035, .022, 'Input bytes are from the saved live failed read. Output bits and flags are recorded directly during decoder replay.',
             fontsize=9.5, color=grey)
    for suffix in ('png', 'svg'):
        fig.savefig(out / (name + '.' + suffix), dpi=180, facecolor='white')
    plt.close(fig)

draw(9, 17, 'first-difference', 'The first difference, aligned with the captured input')
draw(91, 99, 'read-result', 'The same input at the point where the fixed decoder reads the tag', show_uid=True)
