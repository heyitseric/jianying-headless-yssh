import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import yssh_assemble as assembly


class SequenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.media = self.root / 'media'
        self.media.mkdir()
        self.sequence = self.root / 'sequence.json'

    def set_sequence(self, value):
        self.sequence.write_text(json.dumps(value))

    def test_order_and_repeated_references_are_preserved(self):
        for name in ['first.mp4', 'second.mov']:
            (self.media / name).write_bytes(b'media')
        self.set_sequence(['second.mov', 'first.mp4', 'second.mov'])
        sequence, sources = assembly.read_sequence(self.sequence, self.media)
        self.assertEqual(sequence, ['second.mov', 'first.mp4', 'second.mov'])
        self.assertEqual(len(sources), 2)
        assets = {name: {'source': str(path), 'duration_us': 1000000}
                  for name, path in sources.items()}
        segments = assembly.make_plan(sequence, assets, 'test')['tracks'][0]['segments']
        self.assertEqual([s['start_us'] for s in segments], [0, 1000000, 2000000])
        self.assertEqual(segments[0]['source'], segments[2]['source'])
        self.assertTrue(all(s['source_start_us'] == 0 and s['speed'] == 1 for s in segments))

    def test_missing_case_mismatch_and_path_input_fail_before_output(self):
        (self.media / 'clip.mp4').write_bytes(b'media')
        for name in ['missing.mp4', 'CLIP.mp4', '../clip.mp4']:
            self.set_sequence([name])
            out = self.root / 'output'
            with self.assertRaises(ValueError):
                assembly.prepare(self.media, self.sequence, 'test', out)
            self.assertFalse(out.exists())

    def test_symlink_and_existing_output_are_rejected(self):
        (self.media / 'clip.mp4').write_bytes(b'media')
        (self.media / 'alias.mp4').symlink_to(self.media / 'clip.mp4')
        self.set_sequence(['alias.mp4'])
        with self.assertRaises(ValueError):
            assembly.read_sequence(self.sequence, self.media)
        out = self.root / 'output'
        out.mkdir()
        with self.assertRaisesRegex(ValueError, 'already exists'):
            assembly.prepare(self.media, self.sequence, 'test', out)

    def test_empty_and_non_array_sequences_fail(self):
        for value in [[], {}, [''], [False]]:
            self.set_sequence(value)
            with self.assertRaises(ValueError):
                assembly.read_sequence(self.sequence, self.media)

    def test_ambiguous_unicode_filenames_are_rejected(self):
        (self.media / 'clip.mp4').write_bytes(b'media')
        (self.media / 'other.mp4').write_bytes(b'media')
        self.set_sequence(['clip.mp4'])
        from unittest.mock import patch
        with patch.object(assembly.unicodedata, 'normalize', return_value='same'):
            with self.assertRaisesRegex(ValueError, 'ambiguous'):
                assembly.read_sequence(self.sequence, self.media)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg is required')
class MediaTests(unittest.TestCase):
    def test_real_media_reuse_rotation_pixel_conversion_audio_selection_and_source_preservation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            media = root / 'media'
            media.mkdir()
            normal = media / 'normal.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-n', '-f', 'lavfi', '-i',
                            'testsrc2=size=96x64:rate=30:duration=0.4', '-f', 'lavfi', '-i',
                            'sine=frequency=440:sample_rate=48000:duration=0.4',
                            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(normal)], check=True)
            rotated = media / 'rotated.mov'
            subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-n', '-display_rotation', '90', '-i', str(normal),
                            '-map', '0:v:0', '-map', '0:a:0', '-map', '0:a:0', '-c', 'copy',
                            '-disposition:a:0', 'default',
                            '-disposition:a:1', '0', str(rotated)], check=True)
            high = media / 'high.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-n', '-i', str(normal),
                            '-c:v', 'libx264', '-pix_fmt', 'yuv422p10le', '-c:a', 'copy', str(high)], check=True)
            before = {p.name: assembly.core.nd.digest(p) for p in media.iterdir()}
            sequence = root / 'sequence.json'
            sequence.write_text(json.dumps(['normal.mp4', 'rotated.mov', 'high.mp4', 'normal.mp4']))
            output = root / 'prepared'
            report = assembly.prepare(media, sequence, 'test', output)
            self.assertEqual(report['status'], 'prepared-and-validated')
            self.assertEqual(report['references'], 4)
            self.assertEqual(report['unique_sources'], 3)
            assets = {Path(a['source']).name: a for a in report['assets']}
            self.assertEqual(assets['normal.mp4']['action'], 'reused')
            self.assertEqual(assets['normal.mp4']['prepared']['source'], str(normal.resolve()))
            self.assertEqual(assets['rotated.mov']['prepared']['width'], 64)
            self.assertEqual(assets['rotated.mov']['prepared']['height'], 96)
            self.assertEqual(assets['rotated.mov']['audio_index'], 1)
            high_output = Path(assets['high.mp4']['prepared']['source'])
            probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams',
                                                       '-of', 'json', str(high_output)]))
            self.assertEqual(probe['streams'][0]['pix_fmt'], 'yuv420p')
            for p in media.iterdir():
                self.assertEqual(assembly.core.nd.digest(p), before[p.name])
            plan = json.loads((output / 'plan.json').read_text())
            assembly.core.validate_plan(plan)
            self.assertEqual(len(plan['tracks'][0]['segments']), 4)
            self.assertFalse(report['draft_registered'])


if __name__ == '__main__':
    unittest.main()
