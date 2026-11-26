import ast
import argparse
import csv
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile

parser = argparse.ArgumentParser(description='Replay the recorded EM410x failure through both decoder versions.')
parser.add_argument('--repo', type=Path, default=Path.cwd())
parser.add_argument('--out', type=Path)
args = parser.parse_args()
repo = args.repo.resolve()
root = Path(__file__).resolve().parent
out = args.out or root / 'aligned-capture'
out.mkdir(parents=True, exist_ok=True)
test = repo / 'software/script/tests/test_em410x_decoder.py'
fixture = repo / 'software/script/tests/data/em410x/pm5-simulation-rf64.u16'
program = r'''
#include <assert.h>
#include <inttypes.h>
#include <stdio.h>
#include "utils/manchester.h"

static int8_t emitted_count;
static char emitted[3];
static void record_manchester(manchester *modem, uint8_t interval, bool *bits, int8_t *count);
#define manchester_feed record_manchester
#include "decoder_under_test.c"
#undef manchester_feed

static void record_manchester(manchester *modem, uint8_t interval, bool *bits, int8_t *count) {
    manchester_feed(modem, interval, bits, count);
    emitted_count = *count;
    emitted[0] = '\0';
    if (*count > 0) {
        for (int8_t bit = 0; bit < *count; bit++) emitted[bit] = bits[bit] ? '1' : '0';
        emitted[*count] = '\0';
    }
}

int main(int argc, char **argv) {
    assert(argc == 2);
    FILE *input = fopen(argv[1], "rb");
    assert(input);
    em410x_codec *codec = em410x_64_alloc();
    em410x_decoder_start(codec, 0);
    puts("input,interval,bit_count,bits,sync_before,sync_after,bits_since_reset,buffer_hex,decoded,uid");
    unsigned index = 0;
    int low;
    while ((low = fgetc(input)) != EOF) {
        int high = fgetc(input);
        assert(high != EOF);
        uint16_t interval = low | (high << 8);
        bool before = codec->modem->sync;
        bool decoded = em410x_decoder_feed(codec, interval);
        printf("%u,%u,%d,%s,%u,%u,%u,%016" PRIX64 ",%u,", ++index, interval,
               emitted_count, emitted, before, codec->modem->sync, codec->raw_length,
               codec->raw, decoded);
        if (decoded) for (unsigned i = 0; i < 5; i++) printf("%02X", codec->data[i]);
        putchar('\n');
        if (decoded) break;
    }
    em410x_free(codec);
    fclose(input);
}
'''
template = test.read_text()
node = next(node for node in ast.parse(template).body if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == 'SOURCE' for target in node.targets))
lines = template.splitlines(keepends=True)
template = ''.join(lines[:node.lineno - 1]) + 'SOURCE = ' + repr(program) + '\n' + ''.join(lines[node.end_lineno:])
template = template[:template.index('    captures = ROOT /')]
template = template.replace('            str(RFID / "nfctag/lf/protocols/em410x.c"),\n', '')
template += '    subprocess.run([executable, ' + repr(str(fixture)) + '], check=True)\n'
commits = {'old': '6009a5fb8adbb92d3efb548eaac9e501bd179801',
           'fixed': 'cdd62c5'}
commits = {label: subprocess.check_output(['git', 'rev-parse', revision], cwd=repo, text=True).strip()
           for label, revision in commits.items()}
for label, revision in commits.items():
    code = subprocess.check_output(['git', 'show', revision + ':firmware/application/src/rfid/nfctag/lf/protocols/em410x.c'], cwd=repo).decode()
    script = template.replace('    tmp = Path(directory)\n',
                              '    tmp = Path(directory)\n    (tmp / "decoder_under_test.c").write_text(' + repr(code) + ')\n')
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'trace.py'
        path.write_text('__file__ = ' + repr(str(test)) + '\n' + script)
        run = subprocess.run(['python3', str(path)], capture_output=True, text=True, check=True)
        (out / (label + '.csv')).write_text(run.stdout)

raw = fixture.read_bytes()
intervals = struct.unpack('<' + 'H' * (len(raw) // 2), raw)
rows = {}
for label in commits:
    with (out / (label + '.csv')).open() as stream:
        rows[label] = {int(row['input']): row for row in csv.DictReader(stream)}
    assert all(int(row['interval']) == intervals[index - 1] for index, row in rows[label].items())
assert len(rows['old']) == 762 and not any(int(row['decoded']) for row in rows['old'].values())
assert len(rows['fixed']) == 99 and rows['fixed'][99]['uid'] == 'A1B2C3D4E5'
assert rows['old'][12]['sync_after'] == '0' and rows['fixed'][12]['sync_after'] == '1'
assert rows['old'][13]['bits'] == '1' and rows['fixed'][13]['bits'] == '0'

with (out / 'aligned.csv').open('w') as stream:
    writer = csv.writer(stream)
    writer.writerow(['input', 'recorded_periods', 'nominal_us', 'old_bits', 'fixed_bits',
                     'old_sync_after', 'fixed_sync_after', 'old_buffer_hex', 'fixed_buffer_hex', 'old_uid', 'fixed_uid'])
    for index, interval in enumerate(intervals, 1):
        before = rows['old'][index]
        after = rows['fixed'].get(index)
        def bits(row):
            if row is None:
                return 'stopped after UID'
            return row['bits'] if int(row['bit_count']) >= 0 else 'INVALID'
        writer.writerow([index, interval, interval * 8, bits(before), bits(after),
                         before['sync_after'], after['sync_after'] if after else '',
                         before['buffer_hex'], after['buffer_hex'] if after else '',
                         before['uid'], after['uid'] if after else ''])

metadata = {'fixture': fixture.relative_to(repo).as_posix(), 'sha256': hashlib.sha256(raw).hexdigest(), 'intervals': len(intervals),
            'commits': commits, 'interval_unit': '125 kHz carrier periods',
            'microseconds': 'nominal conversion: recorded count * 8',
            'outputs': 'actual calls to the production Manchester decoder during replay of the same captured input',
            'first_phase_difference_input': 12, 'first_bit_difference_input': 13, 'fixed_uid_input': 99}
(out / 'source.json').write_text(json.dumps(metadata, indent=2) + '\n')
print('Saved aligned output for all 762 captured inputs. Fixed decoder reads A1B2C3D4E5 at input 99.')
for index in range(10, 25):
    before, after = rows['old'][index], rows['fixed'][index]
    print(index, intervals[index - 1], before['bits'] or 'INVALID', after['bits'] or 'INVALID',
          before['sync_after'], after['sync_after'])
