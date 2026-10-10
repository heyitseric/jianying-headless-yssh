#!/usr/bin/env python3
"""Prepare full-video assembly plans; never publish drafts or alter source media."""
import argparse
from fractions import Fraction
import json
import math
from pathlib import Path
import subprocess
import sys
import unicodedata

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'engine'))
import jy14_headless as core


def write_json(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def read_sequence(path, media_dir):
    sequence = core.read_json(path)
    core.require(isinstance(sequence, list) and sequence, 'Sequence must be a nonempty filename array')
    entries = list(media_dir.iterdir())
    lookup = {}
    for entry in entries:
        key = unicodedata.normalize('NFC', entry.name).casefold()
        lookup.setdefault(key, []).append(entry)
    sources = {}
    for name in sequence:
        core.require(isinstance(name, str) and name and name not in {'.', '..'}
                     and not any(c in name for c in '/\\\x00\n\r'), 'Use full filenames only; no paths')
        matches = lookup.get(unicodedata.normalize('NFC', name).casefold(), [])
        core.require(len(matches) == 1, 'Missing or ambiguous filename: ' + name)
        core.require(matches[0].name == name, 'Filename spelling/case differs: ' + name)
        sources[name] = core.regular_file(media_dir / name)
    return sequence, sources


def inspect_source(path):
    before = core.nd.digest(path)
    data = json.loads(subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)]))
    videos = [s for s in data['streams'] if s['codec_type'] == 'video'
              and not s.get('disposition', {}).get('attached_pic')]
    audios = [s for s in data['streams'] if s['codec_type'] == 'audio']
    core.require(len(videos) == 1, 'Exactly one video stream is required: ' + path.name)
    video = videos[0]
    core.require(video['codec_name'] in {'h264', 'hevc'}, 'Use H.264 or HEVC source video: ' + path.name)
    duration = float(video.get('duration', data['format'].get('duration', 0)))
    core.require(math.isfinite(duration) and duration > 0, 'Invalid video duration: ' + path.name)
    rotation = float(video.get('tags', {}).get('rotate', 0))
    for side in video.get('side_data_list', []):
        rotation = float(side.get('rotation', rotation))
    core.require(rotation % 90 == 0, 'Only right-angle source rotations are supported')
    core.require(video.get('sample_aspect_ratio', '1:1') in {'1:1', '0:1', 'N/A'},
                 'Non-square source pixels require a separate review')
    defaults = [a for a in audios if a.get('disposition', {}).get('default') == 1]
    core.require(len(audios) <= 1 or len(defaults) == 1,
                 'Multiple audio streams need one unambiguous default: ' + path.name)
    audio = defaults[0] if defaults else (audios[0] if audios else None)
    if audio:
        core.require(audio.get('codec_name'), 'Default audio stream has no supported decoder identity')
    fps = Fraction(video.get('avg_frame_rate', '0/1'))
    core.require(fps > 0, 'Unknown frame rate: ' + path.name)
    reasons = []
    if rotation % 360:
        reasons.append('rotation')
    if video.get('pix_fmt') not in {'yuv420p', 'yuvj420p', 'yuv420p10le'}:
        reasons.append('pixel_format')
    if len(audios) > 1:
        reasons.append('multiple_audio_streams')
    if reasons:
        core.require(video.get('color_transfer') not in {'smpte2084', 'arib-std-b67'},
                     'HDR conversion needs a separately approved color workflow')
    core.require(core.nd.digest(path) == before, 'Source changed during inspection')
    width, height = video['width'], video['height']
    if rotation % 180:
        width, height = height, width
    return {'source': str(path), 'source_sha256': before, 'duration_us': round(duration * core.MICROS),
            'fps': str(fps), 'display_size': [width, height], 'reasons': reasons,
            'audio_index': audio['index'] if audio else None,
            'audio_streams': [{'index': a['index'], 'codec': a.get('codec_name'),
                               'channels': a.get('channels'),
                               'default': a.get('disposition', {}).get('default') == 1} for a in audios]}


def normalize(source, target, info, log):
    command = ['ffmpeg', '-v', 'error', '-nostdin', '-n', '-i', str(source), '-map', '0:v:0']
    if info['audio_index'] is not None:
        command += ['-map', '0:' + str(info['audio_index'])]
    command += ['-c:v', 'libx264', '-preset', 'medium', '-profile:v', 'main',
                '-pix_fmt', 'yuv420p', '-b:v', '10000k', '-fps_mode', 'passthrough',
                '-c:a', 'aac', '-b:a', '128k', '-ar', '48000', '-map_metadata', '-1',
                '-metadata:s:v:0', 'rotate=0', '-movflags', '+faststart', str(target)]
    with log.open('x', encoding='utf-8') as stream:
        subprocess.run(command, stdout=stream, stderr=stream, check=True)
    prepared = core.probe(target)
    core.require(prepared['media_type'] == 'video' and prepared['codec'] == 'h264', 'Unexpected output codec')
    core.require([prepared['width'], prepared['height']] == info['display_size'], 'Display size changed')
    tolerance = math.ceil(core.MICROS / float(Fraction(info['fps']))) + 2
    core.require(abs(prepared['duration_us'] - info['duration_us']) <= tolerance, 'Video duration changed')
    core.require(prepared['has_audio'] == (info['audio_index'] is not None), 'Audio presence changed')
    with log.open('a', encoding='utf-8') as stream:
        subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-i', str(target),
                        '-map', '0:v:0', '-map', '0:a:0?', '-f', 'null', '-'],
                       stdout=stream, stderr=stream, check=True)
    core.require(core.nd.digest(source) == info['source_sha256'], 'Source changed during normalization')
    return prepared


def make_plan(sequence, assets, name):
    cursor, segments = 0, []
    for filename in sequence:
        asset = assets[filename]
        duration = asset['duration_us']
        segments.append({'source': asset['source'], 'start_us': cursor, 'duration_us': duration,
                         'source_start_us': 0, 'source_duration_us': duration, 'speed': 1, 'volume': 1})
        cursor += duration
    return {'schema': core.SCHEMA, 'name': name,
            'canvas': {'width': 1080, 'height': 1920, 'fps': 30},
            'tracks': [{'type': 'video', 'name': '初排视频', 'segments': segments}]}


def prepare(media_dir, sequence_path, name, out):
    core.require(not media_dir.is_symlink(), 'Media directory cannot be a symlink')
    media_dir = media_dir.resolve(strict=True)
    core.require(media_dir.is_dir(), 'Media directory is unavailable')
    core.require(not out.is_symlink() and not out.exists(), 'Output already exists; choose a new directory')
    out = out.resolve()
    core.require(out != media_dir and media_dir not in out.parents, 'Output must be outside the media directory')
    core.require(out != core.nd.DRAFT_ROOT and core.nd.DRAFT_ROOT not in out.parents,
                 'Output must be outside live Jianying drafts')
    # Validate names and every source before any conversion or output creation.
    sequence, sources = read_sequence(sequence_path, media_dir)
    inspections = {filename: inspect_source(path) for filename, path in sources.items()}
    core.require(isinstance(name, str) and name.strip() == name and name and len(name.encode()) < 200
                 and not name.startswith('.') and not any(c in name for c in '/\\\x00\n\r'), 'Invalid draft name')
    out.mkdir(parents=True, mode=0o700)
    report = {'status': 'preparing', 'sequence': sequence, 'references': len(sequence),
              'unique_sources': len(sources), 'assets': [], 'draft_registered': False}
    try:
        write_json(out / 'sequence.json', sequence)
        (out / 'normalized').mkdir(mode=0o700)
        (out / 'logs').mkdir(mode=0o700)
        assets = {}
        for filename, source in sources.items():
            info = inspections[filename]
            print(('Normalize: ' if info['reasons'] else 'Reuse: ') + filename, flush=True)
            if info['reasons']:
                target = out / 'normalized' / (filename + '.mp4')
                asset = normalize(source, target, info, out / 'logs' / (filename + '.log'))
            else:
                asset = core.probe(source)
            core.require(asset['media_type'] == 'video', 'Only video clips are assembled')
            assets[filename] = asset
            report['assets'].append(dict(info, prepared=asset,
                                         action='normalized' if info['reasons'] else 'reused'))
        plan = make_plan(sequence, assets, name)
        core.validate_plan(plan)
        for filename, source in sources.items():
            core.require(core.nd.digest(source) == inspections[filename]['source_sha256'],
                         'Source changed before final validation: ' + filename)
        write_json(out / 'plan.json', plan)
        report.update(status='prepared-and-validated', source_unchanged=True,
                      duration_us=sum(s['duration_us'] for s in plan['tracks'][0]['segments']))
        write_json(out / 'report.json', report)
        print(json.dumps({'status': report['status'], 'references': len(sequence),
                          'unique_sources': len(sources), 'plan': str(out / 'plan.json')}, ensure_ascii=False))
        return report
    except Exception as error:
        report.update(status='failed', error=str(error))
        write_json(out / 'failure.json', report)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--media-dir', type=Path, required=True)
    parser.add_argument('--sequence', type=Path, required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    prepare(args.media_dir, args.sequence, args.name, args.out)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
