"""Host backup and restore round trip without touching the real state image."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[3] / 'scripts'


class StateBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'scripts').mkdir()
        (self.root / 'runtime').mkdir()
        for name in ('backup-eduos-state.sh', 'restore-eduos-state.sh'):
            shutil.copy2(SCRIPTS / name, self.root / 'scripts' / name)
        self.state = self.root / 'runtime/eduos-state.img'
        self.backup = self.root / 'state-backup.img'
        subprocess.run(['truncate', '-s', '16M', str(self.state)], check=True)
        subprocess.run(['mkfs.ext4', '-q', '-F', '-L', 'EDUOS_STATE', str(self.state)], check=True)

    def run_script(self, name):
        return subprocess.run([str(self.root / 'scripts' / name), str(self.backup)],
                              capture_output=True, text=True, check=False)

    def test_backup_restore_and_checksum_rejection(self):
        expected = self.state.read_bytes()
        self.assertEqual(self.run_script('backup-eduos-state.sh').returncode, 0)
        self.assertEqual(self.backup.read_bytes(), expected)
        self.assertEqual(self.run_script('backup-eduos-state.sh').returncode, 1)
        self.assertEqual(self.run_script('restore-eduos-state.sh').returncode, 1)
        self.state.unlink()
        self.assertEqual(self.run_script('restore-eduos-state.sh').returncode, 0)
        self.assertEqual(self.state.read_bytes(), expected)
        self.state.unlink()
        self.backup.write_bytes(b'corrupt backup')
        self.assertEqual(self.run_script('restore-eduos-state.sh').returncode, 1)
        self.assertFalse(self.state.exists())


if __name__ == '__main__':
    unittest.main()
