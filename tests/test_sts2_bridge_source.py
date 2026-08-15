from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "integrations" / "sts2_ipet_bridge"


class Sts2BridgeSourceTests(unittest.TestCase):
    def test_project_uses_net9_local_references_without_shipping_game_dlls(self) -> None:
        project = (BRIDGE / "IpetSts2Bridge.csproj").read_text(encoding="utf-8")
        self.assertIn("<TargetFramework>net9.0</TargetFramework>", project)
        self.assertIn("$(Sts2InstallDir)", project)
        self.assertIn("$(STS2_INSTALL_DIR)", project)
        self.assertIn("$(Sts2DataDir)/sts2.dll", project)
        self.assertIn("$(Sts2DataDir)/0Harmony.dll", project)
        source_dlls = [
            path
            for path in BRIDGE.rglob("*.dll")
            if "bin" not in path.relative_to(BRIDGE).parts and "obj" not in path.relative_to(BRIDGE).parts
        ]
        self.assertEqual(source_dlls, [], "game DLLs must not be copied into the bridge source")

    def test_manifest_and_transport_are_read_only_bounded_and_nonblocking(self) -> None:
        manifest = json.loads((BRIDGE / "IpetSts2Bridge.json").read_text(encoding="utf-8"))
        client = (BRIDGE / "BridgeClient.cs").read_text(encoding="utf-8")
        self.assertFalse(manifest["affects_gameplay"])
        self.assertFalse(manifest["has_pck"])
        self.assertTrue(manifest["has_dll"])
        self.assertIn('BaseUrl = "http://127.0.0.1:8008"', client)
        self.assertIn("TimeSpan.FromMilliseconds(250)", client)
        self.assertIn("TimeSpan.FromSeconds(3)", client)
        self.assertIn("BoundedChannelOptions(64)", client)
        self.assertIn("BoundedChannelFullMode.DropOldest", client)
        self.assertIn("Task.Run(SendLoopAsync)", client)
        self.assertIn("event_sequence = Interlocked.Read", client)
        self.assertIn('path == "/api/game/sts2/events" ? 3 : 1', client)
        self.assertIn("ipet-game-bridge.json", client)
        self.assertIn("uri.IsLoopback", client)
        self.assertIn("TryPostAsync(DefaultBaseUrl", client)
        self.assertIn("ResolveToken", client)
        self.assertIn("X-Ipet-Local-Token", client)

    def test_public_hooks_harmony_fallbacks_thresholds_and_local_filter_exist(self) -> None:
        observer = (BRIDGE / "PublicHookObserver.cs").read_text(encoding="utf-8")
        patches = (BRIDGE / "PatchInstaller.cs").read_text(encoding="utf-8")
        protocol = (BRIDGE / "Protocol.cs").read_text(encoding="utf-8")
        snapshots = (BRIDGE / "SnapshotReader.cs").read_text(encoding="utf-8")
        for token in (
            "SubscribeForRunStateHooks", "SubscribeForCombatStateHooks", "AfterCurrentHpChanged",
            "AfterCombatVictory", "AfterItemPurchased", "AfterRewardTaken", "BeforeRoomEntered",
        ):
            self.assertIn(token, observer)
        self.assertIn("harmony.Patch(method, postfix:", patches)
        self.assertIn('"MegaCrit.Sts2.Core.Nodes.NGame", "StartRun"', patches)
        self.assertIn("InitializeNewRun", patches)
        self.assertIn("EnterMapPointInternal", patches)
        self.assertIn("EnterRoomInternal", patches)
        self.assertIn('"MegaCrit.Sts2.Core.Combat.CombatManager", "SetUpCombat"', patches)
        self.assertIn("RewardCollectedFrom", patches)
        self.assertIn("OptionButtonClicked", patches)
        self.assertNotIn("UpgradeInternal", patches)
        self.assertNotIn("FinalizeUpgradeInternal", patches)
        self.assertIn("Math.Max(10, maxHp * 0.2)", protocol)
        self.assertIn("maxHp * 0.25", protocol)
        self.assertIn("maxHp * 0.35", protocol)
        self.assertIn("ResolveLocalPlayer(rawPlayers, players)", snapshots)
        self.assertIn("Fail closed for multiplayer identity", snapshots)
        self.assertIn("ModelDb.Singleton<PublicHookObserver>()", observer)
        self.assertNotIn("PublicHookObserver Observer = new()", observer)
        detector = (BRIDGE / "EventDetector.cs").read_text(encoding="utf-8")
        self.assertIn('or "StartRun"', detector)
        self.assertIn("if (_runActive)", detector)
        self.assertIn('["source_id"] = "rest_site_smith"', detector)

    def test_csharp_self_test_covers_serialization_diff_threshold_and_identity(self) -> None:
        source = (BRIDGE / "SelfTest" / "Program.cs").read_text(encoding="utf-8")
        for token in (
            "SnapshotDiff.Compute", "large_damage_taken", "low_hp_entered", "heal_received",
            "JsonSerializer.Serialize", "max_hp", "single-player identity", "unknown multiplayer identity",
            "ResolveBaseUrl", "non-loopback endpoint",
        ):
            self.assertIn(token, source)


if __name__ == "__main__":
    unittest.main()
