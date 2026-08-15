using System.Reflection;
using HarmonyLib;

namespace Ipet.Sts2Bridge;

internal static class PatchInstaller
{
    private const BindingFlags AnyMethod = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static;

    private static readonly (string TypeName, string MethodName, bool Critical)[] Targets =
    [
        // NGame.StartRun receives the materialized RunState on both new and
        // continued runs in the current public beta. The older RunManager
        // initializers remain independent fallbacks.
        ("MegaCrit.Sts2.Core.Nodes.NGame", "StartRun", true),
        ("MegaCrit.Sts2.Core.Runs.RunManager", "InitializeNewRun", true),
        ("MegaCrit.Sts2.Core.Runs.RunManager", "InitializeSavedRun", true),
        ("MegaCrit.Sts2.Core.Runs.RunManager", "SetActInternal", false),
        ("MegaCrit.Sts2.Core.Runs.RunManager", "EnterMapPointInternal", false),
        ("MegaCrit.Sts2.Core.Runs.RunManager", "EnterRoom", false),
        ("MegaCrit.Sts2.Core.Runs.RunManager", "EnterRoomInternal", false),
        ("MegaCrit.Sts2.Core.Runs.RunManager", "Abandon", true),
        ("MegaCrit.Sts2.Core.Runs.RunManager", "OnEnded", true),
        ("MegaCrit.Sts2.Core.Combat.CombatManager", "SetUpCombat", false),
        ("MegaCrit.Sts2.Core.Combat.CombatManager", "SetupPlayerTurn", false),
        ("MegaCrit.Sts2.Core.Combat.CombatManager", "HandlePlayerDeath", false),
        ("MegaCrit.Sts2.Core.Combat.CombatManager", "LoseCombat", false),
        ("MegaCrit.Sts2.Core.Combat.CombatManager", "EndCombatInternal", false),
        ("MegaCrit.Sts2.Core.Nodes.Screens.CardSelection.NCardRewardSelectionScreen", "SelectCard", false),
        ("MegaCrit.Sts2.Core.Nodes.Screens.NRewardsScreen", "RewardCollectedFrom", false),
        ("MegaCrit.Sts2.Core.Nodes.Screens.NRewardsScreen", "RewardSkippedFrom", false),
        ("MegaCrit.Sts2.Core.Nodes.Rooms.NEventRoom", "OptionButtonClicked", false),
        ("MegaCrit.Sts2.Core.Nodes.Rooms.NEventRoom", "BeforeOptionChosen", false),
    ];

    internal static bool Install(Harmony harmony)
    {
        var patchedCriticalMethods = new HashSet<string>(StringComparer.Ordinal);
        var instancePostfix = new HarmonyMethod(typeof(PatchInstaller).GetMethod(nameof(InstancePostfix), AnyMethod));
        var staticPostfix = new HarmonyMethod(typeof(PatchInstaller).GetMethod(nameof(StaticPostfix), AnyMethod));
        foreach (var target in Targets)
        {
            var type = Type.GetType(target.TypeName + ", sts2", throwOnError: false);
            if (type is null)
            {
                continue;
            }
            foreach (var method in type.GetMethods(AnyMethod).Where(method => method.Name == target.MethodName && !method.IsAbstract))
            {
                try
                {
                    harmony.Patch(method, postfix: method.IsStatic ? staticPostfix : instancePostfix);
                    if (target.Critical)
                    {
                        patchedCriticalMethods.Add(target.MethodName);
                    }
                }
                catch
                {
                    // One changed signature must not prevent other independent
                    // observation hooks from loading.
                }
            }
        }
        // Without a verified run-entry hook the bridge cannot assign a Run ID,
        // so it must report incompatible even if an end/abandon fallback still
        // happens to match this game build.
        return patchedCriticalMethods.Contains("StartRun")
            || patchedCriticalMethods.Contains("InitializeNewRun")
            || patchedCriticalMethods.Contains("InitializeSavedRun");
    }

    private static void InstancePostfix(object __instance, object[] __args, MethodBase __originalMethod)
    {
        BridgeRuntime.OnHook(__originalMethod, __instance, __args);
    }

    private static void StaticPostfix(object[] __args, MethodBase __originalMethod)
    {
        BridgeRuntime.OnHook(__originalMethod, null, __args);
    }
}
