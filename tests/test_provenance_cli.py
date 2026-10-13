"""Actual source-copy file CLI subprocesses; no JARs, build, Java, network or native journeys."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from tools import check_current_upgrade, release

ROOT=Path(__file__).resolve().parents[1]
BLOCKED_ENTRY = """
import json, os, pathlib, runpy, socket, subprocess, sys, urllib.request
def forbidden(*args, **kwargs):
    raise AssertionError('Network/native child dispatch is forbidden in this source CLI probe')
socket.socket = forbidden
socket.create_connection = forbidden
urllib.request.urlopen = forbidden
urllib.request.OpenerDirector.open = forbidden
subprocess.Popen = forbidden
subprocess.run = forbidden
os.system = forbidden
script = pathlib.Path(sys.argv[1]).resolve()
root = script.parents[1]
sys.argv = [str(script), *sys.argv[2:]]
try:
    runpy.run_path(str(script), run_name='__main__')
except SystemExit as error:
    if error.code not in (None, 0):
        raise
for name in ('evaluation.upgrade_cases', 'tools.summarize_u2b', 'tools.check_current_upgrade'):
    path = pathlib.Path(sys.modules[name].__file__).resolve()
    assert path.is_relative_to(root), 'Import escaped the isolated source copy'
print(json.dumps({'imports_from_source_copy': True, 'network_or_native_dispatches': 0}))
"""

class ProvenanceCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT/"work").mkdir(exist_ok=True)
        cls.temporary=tempfile.TemporaryDirectory(dir=ROOT/"work")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.folder=Path(cls.temporary.name)
        cls.source=cls.folder/"source"
        cls.source.mkdir()
        # Copy only public source/data. No cache, generated/native outputs or checkout metadata.
        for relative in release.source_files(ROOT):
            target=cls.source/relative
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/relative,target)
        cls.neutral=cls.folder/"neutral"
        cls.neutral.mkdir()
        # Do not inherit credentials or Python path settings; retain Windows loader roots.
        cls.environment={"PYTHONNOUSERSITE":"1","PYTHONDONTWRITEBYTECODE":"1"}
        if os.name=="nt":
            for name in ("SystemRoot","SystemDrive"):
                if os.environ.get(name):
                    cls.environment[name]=os.environ[name]

    def run_cli(self,arguments,*,cwd=None):
        return subprocess.run([sys.executable,*arguments],cwd=cwd or self.source,
                              env=self.environment,capture_output=True,text=True,encoding="utf-8",timeout=30)

    def test_exact_documented_provenance_and_release_file_commands_without_pythonpath(self):
        self.assertNotIn("PYTHONPATH",self.environment)
        for excluded in (".git",".deps","build","work"):
            self.assertFalse((self.source/excluded).exists())
        source=self.run_cli(["tools/check_current_upgrade.py"])
        self.assertEqual(source.returncode,0,source.stderr)
        value=json.loads(source.stdout)
        self.assertEqual(value["version"],check_current_upgrade.VERSION)
        self.assertEqual(value["original_data_files_checked"],80)
        self.assertFalse(value["new_evaluation_executed"])
        final=self.run_cli(["tools/check.py","--suite","release"])
        self.assertEqual(final.returncode,0,final.stderr)
        self.assertEqual(json.loads(final.stdout)["upgrade_version"],check_current_upgrade.VERSION)

    def test_file_entries_under_python_isolation_block_network_and_native_children(self):
        for path,args in (("tools/check_current_upgrade.py",[]),("tools/check.py",["--suite","release"])):
            result=self.run_cli(["-I","-c",BLOCKED_ENTRY,str(self.source/path),*args],cwd=self.neutral)
            self.assertEqual(result.returncode,0,result.stderr)
            marker=json.loads(result.stdout.splitlines()[-1])
            self.assertTrue(marker["imports_from_source_copy"])
            self.assertEqual(marker["network_or_native_dispatches"],0)

    def test_clone_data_tamper_fails_locally_instead_of_using_checkout_data(self):
        registry=json.loads((self.source/"evaluation/upgrade-tasks-v1/registry.json").read_text(encoding="utf-8"))
        relative=registry["splits"]["capability"]["cases"][0]["input"]
        path=self.source/relative
        original=path.read_bytes()
        try:
            path.write_bytes(original+b" ")
            result=self.run_cli([str(self.source/"tools/check_current_upgrade.py")],cwd=self.neutral)
            self.assertNotEqual(result.returncode,0)
            self.assertIn("Current source hash differs: "+relative,result.stderr)
        finally:
            path.write_bytes(original)
