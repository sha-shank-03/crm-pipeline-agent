"""Pure cached parser/orchestration regressions: fake cache, blocked network/children; no copy."""
from contextlib import ExitStack, redirect_stdout, redirect_stderr
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools import bootstrap, clean_copy

class CachedPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/".deps").mkdir()
        self.payloads={}
        self.expected={}
        dependencies=[]
        for i,(coordinate,filename,digest) in enumerate(bootstrap.CACHED_PINS):
            data=("invented non-JAR cache "+str(i)).encode()
            self.payloads[filename]=data;self.expected[data]=digest
            (self.root/".deps"/filename).write_bytes(data)
            dependencies.append({"coordinate":coordinate,"filename":filename,"sha256":digest})
        (self.root/"dependencies.lock.json").write_text(json.dumps({"dependencies":dependencies}),encoding="utf-8")

    def guard(self,*,hashes=True):
        stack=ExitStack()
        stack.enter_context(patch("tools.bootstrap.ROOT",self.root))
        for target in ("tools.bootstrap.fetch","tools.bootstrap.pom_audit","tools.bootstrap.used_bytes",
                       "tools.bootstrap.ledger_entry","socket.socket","subprocess.Popen"):
            stack.enter_context(patch(target,side_effect=AssertionError("Forbidden network/ledger/child operation")))
        self.hasher=None
        if hashes:
            self.hasher=stack.enter_context(patch("tools.bootstrap.hashlib.sha256",
                side_effect=lambda data:Mock(hexdigest=lambda:self.expected.get(data,"0"*64))))
        return stack

    def test_cached_only_parser_accepts_explicit_mode_checks_both_pins_and_writes_nothing(self):
        before={p.relative_to(self.root).as_posix():p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        with self.guard(),redirect_stdout(io.StringIO()) as out:
            self.assertEqual(bootstrap.main(["--cached-only"]),0)
            self.assertEqual(self.hasher.call_count,2)
        result=json.loads(out.getvalue())
        self.assertEqual(len(result["dependencies"]),2)
        self.assertEqual(result["this_command_transfer_bytes"],0)
        self.assertIsNone(result["cumulative_new_transfer_bytes"])
        self.assertFalse(result["external_audit_performed"])
        self.assertEqual(before,{p.relative_to(self.root).as_posix():p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_missing_and_changed_second_jar_fail_without_any_fetch_or_write(self):
        second=bootstrap.CACHED_PINS[1][1]
        for kind in ("missing","changed"):
            path=self.root/".deps"/second
            if kind=="missing":path.unlink()
            else:path.write_bytes(b"invented altered cache")
            with self.subTest(kind=kind),self.guard(),redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(ValueError,"missing or changed"):bootstrap.main(["--cached-only"])
            path.write_bytes(self.payloads[second])
        self.assertFalse((self.root/"work").exists())

    def test_original_pin_or_closure_changes_cannot_make_changed_cache_acceptable(self):
        original=json.loads((self.root/"dependencies.lock.json").read_text())
        for kind in ("hash","filename","coordinate","omit","malformed-entry"):
            value=json.loads(json.dumps(original))
            if kind=="hash":value["dependencies"][1]["sha256"]="0"*64
            if kind=="filename":value["dependencies"][1]["filename"]="../outside.jar"
            if kind=="coordinate":value["dependencies"][1]["coordinate"]="invented:extra:1"
            if kind=="omit":value["dependencies"].pop()
            if kind=="malformed-entry":value["dependencies"][1]=False
            (self.root/"dependencies.lock.json").write_text(json.dumps(value))
            with self.subTest(kind=kind),self.guard():
                with self.assertRaisesRegex(ValueError,"both original"):bootstrap.main(["--cached-only"])
                self.assertEqual(self.hasher.call_count,0)
        (self.root/"dependencies.lock.json").write_text(json.dumps(original))

    def test_cached_path_performs_real_hash_comparison_on_invented_invalid_bytes(self):
        with self.guard(hashes=False):
            with self.assertRaisesRegex(ValueError,"missing or changed"):bootstrap.main(["--cached-only"])

    def test_no_mode_conflicting_modes_and_prepare_lock_are_parser_errors_before_cache_access(self):
        for args in ([],["--cached-only","--fetch-deps"],["--cached-only","--prepare-lock"]):
            with self.subTest(args=args),self.guard(),redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:bootstrap.main(args)
                self.assertEqual(error.exception.code,2)
                self.assertEqual(self.hasher.call_count,0)

    def test_quickstart_actual_first_argv_runs_cached_parser_and_then_only_mocked_native_steps(self):
        captured=[]
        def run(argv,**kwargs):
            captured.append(argv)
            if argv[1]=="tools/bootstrap.py":
                with redirect_stdout(io.StringIO()) as out:
                    code=bootstrap.main(argv[2:])
                return subprocess.CompletedProcess(argv,code,out.getvalue(),"")
            return subprocess.CompletedProcess(argv,0,"invented mocked step","")
        with self.guard(),patch("tools.clean_copy.subprocess.run",side_effect=run):
            self.assertEqual(clean_copy.verify_quickstart(self.root),0)
        self.assertEqual(captured[0][1:],["tools/bootstrap.py","--cached-only"])
        self.assertEqual(len(captured),10)
        self.assertFalse(any("--fetch-deps" in argv for argv in captured))
        receipt=json.loads((self.root/"work/clean-copy-check.json").read_text())
        self.assertEqual(len(receipt["commands"]),10)
        self.assertEqual(receipt["commands"][0]["argv"][1:],["tools/bootstrap.py","--cached-only"])

    def test_cached_parser_failure_stops_quickstart_before_build_and_retains_failure_receipt(self):
        (self.root/".deps"/bootstrap.CACHED_PINS[1][1]).unlink()
        captured=[]
        def run(argv,**kwargs):
            captured.append(argv)
            try:
                with redirect_stdout(io.StringIO()):code=bootstrap.main(argv[2:])
                return subprocess.CompletedProcess(argv,code,"","")
            except ValueError as error:
                return subprocess.CompletedProcess(argv,2,"",str(error))
        with self.guard(),patch("tools.clean_copy.subprocess.run",side_effect=run):
            self.assertEqual(clean_copy.verify_quickstart(self.root),2)
        self.assertEqual(len(captured),1)
        receipt=json.loads((self.root/"work/clean-copy-check.json").read_text())
        self.assertEqual(receipt["commands"][0]["exit_code"],2)
        self.assertIn("missing or changed",receipt["commands"][0]["stderr"])

    def test_cached_export_pin_failure_occurs_before_destination_or_copy(self):
        value=json.loads((self.root/"dependencies.lock.json").read_text())
        value["dependencies"][1]["sha256"]="0"*64
        (self.root/"dependencies.lock.json").write_text(json.dumps(value))
        destination=self.root/"never-created"
        with self.guard(),patch("tools.clean_copy.ROOT",self.root),patch("tools.clean_copy.shutil.copyfile") as copier:
            with self.assertRaisesRegex(ValueError,"both original"):clean_copy.export(destination,cached=True)
        self.assertFalse(destination.exists());copier.assert_not_called()
