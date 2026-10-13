"""Pure file/hash/copy corruption controls; never invoke Java, sockets or quickstart."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from tools import check_current_upgrade, clean_copy, release

ROOT = Path(__file__).resolve().parents[1]


class UpgradeProvenanceTests(unittest.TestCase):
    def setUp(self):
        (ROOT/"work").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT/"work")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)
        self.value = json.loads((ROOT/check_current_upgrade.MANIFEST).read_text(encoding="utf-8"))

    def test_original_data_and_u1_manifest_are_verified_without_git_java_or_network(self):
        result = check_current_upgrade.verify()
        self.assertTrue(result["original_freeze_preserved"] and result["u1_manifest_preserved"])
        self.assertTrue(result["original_u2a_manifest_preserved"])
        self.assertEqual(result["original_data_files_checked"],80)
        self.assertFalse(result["worker_self_accepted"] or result["new_evaluation_executed"])

    def test_manifest_corruptions_and_u1_runtime_change_are_rejected(self):
        variants = []
        p = copy.deepcopy(self.value); p["accepted_u1_manifest_sha256"] = "0"*64; variants.append(p)
        p = copy.deepcopy(self.value); p["original_u2a_manifest_sha256"] = "0"*64; variants.append(p)
        p = copy.deepcopy(self.value); p["files"][0]["u2a_sha256"] = "0"*64; variants.append(p)
        p = copy.deepcopy(self.value); p["files"][0]["sha256"] = "0"*64; variants.append(p)
        p = copy.deepcopy(self.value); p["files"].append(copy.deepcopy(p["files"][0])); variants.append(p)
        p = copy.deepcopy(self.value); p["files"][0]["path"] = "../outside"; variants.append(p)
        p = copy.deepcopy(self.value)
        old = next(e for e in p["files"] if e["path"]=="crm_agent/loop.py")
        old["sha256"] = "0"*64; variants.append(p)
        for packet in variants:
            with self.subTest(packet=packet["files"][0]["path"]):
                with self.assertRaises(ValueError): check_current_upgrade.verify(value=packet)

    def test_export_is_standalone_current_source_and_original_data_tamper_is_detected(self):
        target = clean_copy.export(self.path/"export")
        self.assertFalse((target/".deps").exists() or (target/"work").exists() or (target/"AGENTS.md").exists())
        self.assertEqual(check_current_upgrade.verify(target)["original_data_files_checked"],80)
        with patch("tools.release.ROOT",target):
            self.assertEqual(release.check()["upgrade_version"],"upgrade-v7-file-cli")
        original = json.loads((target/"evaluation/frozen-manifest.json").read_text(encoding="utf-8"))
        data = target/original["splits"]["dev"]["cases"][0]["input"]
        data.write_bytes(data.read_bytes()+b" ")
        with self.assertRaises(ValueError): check_current_upgrade.verify(target)

    def test_shipped_live_profile_is_rejected_even_with_matching_current_hash(self):
        target = clean_copy.export(self.path/"export")
        example = target/"configs/salesforce.disabled.json"
        packet = json.loads(example.read_text(encoding="utf-8")); packet["external_enabled"] = True
        example.write_text(json.dumps(packet),encoding="utf-8")
        manifest = json.loads((target/check_current_upgrade.MANIFEST).read_text(encoding="utf-8"))
        next(e for e in manifest["files"] if e["path"]=="configs/salesforce.disabled.json")["sha256"] = hashlib.sha256(example.read_bytes()).hexdigest()
        (target/check_current_upgrade.MANIFEST).write_text(json.dumps(manifest),encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"outside file CLI correction"):
            check_current_upgrade.verify(target)
        with patch("tools.release.ROOT",target), patch("tools.release.verify_provenance",return_value=check_current_upgrade.verify()):
            with self.assertRaisesRegex(ValueError,"disable external traffic"): release.check()

    def test_v3_identity_manifest_and_new_capability_task_bytes_are_preserved(self):
        target = clean_copy.export(self.path/"export")
        predecessor = target/"evaluation/upgrade-v3/manifest.json"
        original = predecessor.read_bytes()
        predecessor.write_bytes(original+b" ")
        with self.assertRaisesRegex(ValueError,"identity manifest changed"):
            check_current_upgrade.verify(target)
        predecessor.write_bytes(original)
        registry = json.loads((target/"evaluation/upgrade-tasks-v1/registry.json").read_text(encoding="utf-8"))
        data = target/registry["splits"]["capability"]["cases"][0]["input"]
        data.write_bytes(data.read_bytes()+b" ")
        with self.assertRaisesRegex(ValueError,"Current source hash differs"):
            check_current_upgrade.verify(target)

    def test_v4_binding_and_new_summary_provenance_corruptions_are_rejected_without_export(self):
        for kind in ("v4-reference","v4-source","summary-reference","new-file-history"):
            packet = copy.deepcopy(self.value)
            if kind=="v4-reference":packet["accepted_u2b_manifest_sha256"]="0"*64
            if kind=="v4-source":packet["files"][0]["v4_sha256"]="0"*64
            if kind=="summary-reference":packet["redacted_u2b_summary_sha256"]="0"*64
            if kind=="new-file-history":
                next(e for e in packet["files"] if e["path"]=="tools/summarize_u2b.py")["v4_sha256"]="0"*64
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError):check_current_upgrade.verify(value=packet)

    def test_u2b_baseline_dataset_and_domain_are_immutable_in_v5_without_export(self):
        for path in ("crm_agent/providers/evidence_workflow.py", "crm_agent/loop.py",
                     "evaluation/upgrade-tasks-v1/capability/inputs/u2b-cap-01.json"):
            packet=copy.deepcopy(self.value)
            next(e for e in packet["files"] if e["path"]==path)["sha256"]="0"*64
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError,"outside file CLI correction"):
                    check_current_upgrade.verify(value=packet)

    def test_v6_failure_binding_and_native_source_carry_forward_are_required_without_export(self):
        for kind in ("v6-reference","v6-source","native-byte-change","entry-regression-omitted"):
            packet=copy.deepcopy(self.value)
            if kind=="v6-reference":packet["pre_cli_fix_v6_manifest_sha256"]="0"*64
            if kind=="v6-source":packet["files"][0]["v6_sha256"]="0"*64
            if kind=="native-byte-change":
                next(e for e in packet["files"] if e["path"]=="tools/bootstrap.py")["sha256"]="0"*64
            if kind=="entry-regression-omitted":packet["files"]=[e for e in packet["files"] if e["path"]!="tests/test_provenance_cli.py"]
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError):check_current_upgrade.verify(value=packet)

    def test_v5_failure_snapshot_binding_and_cached_regression_inventory_required_without_export(self):
        for kind in ("v5-reference","v5-source","cached-test-omitted","previous-unsealed-pin"):
            packet=copy.deepcopy(self.value)
            if kind=="v5-reference":packet["pre_correction_u2c_manifest_sha256"]="0"*64
            if kind=="v5-source":packet["files"][0]["v5_sha256"]="0"*64
            if kind=="cached-test-omitted":packet["files"]=[e for e in packet["files"] if e["path"]!="tests/test_cached_packaging.py"]
            if kind=="previous-unsealed-pin":packet["newly_sealed_existing_pre_correction_sha256"]["tools/bootstrap.py"]="0"*64
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError):check_current_upgrade.verify(value=packet)
    def test_reviewed_u2a_manifest_bytes_cannot_be_replaced_by_current_hashes(self):
        target = clean_copy.export(self.path/"export")
        predecessor = target/"evaluation/upgrade-v2/manifest.json"
        predecessor.write_bytes(predecessor.read_bytes()+b" ")
        with self.assertRaisesRegex(ValueError,"Reviewed U2-A manifest changed"):
            check_current_upgrade.verify(target)

