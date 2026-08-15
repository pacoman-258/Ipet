using System.Reflection;
using HarmonyLib;
using MegaCrit.Sts2.Core.Modding;

namespace Ipet.Sts2Bridge;

/// <summary>Game Mod Loader entry point for the read-only Ipet bridge.</summary>
[ModInitializer(nameof(Initialize))]
public static class ModEntry
{
    /// <summary>Starts bounded observation and compatibility hooks.</summary>
    public static void Initialize()
    {
        BridgeRuntime.Start();
    }
}

internal static class BridgeRuntime
{
    private static readonly object StartLock = new();
    private static BridgeClient? _client;
    private static EventDetector? _detector;

    internal static void Start()
    {
        lock (StartLock)
        {
            if (_client is not null)
            {
                return;
            }
            try
            {
                _client = new BridgeClient();
                _detector = new EventDetector(_client);
                _client.Start();
                var publicHooksReady = PublicHookObserver.Install();
                var harmony = new Harmony("ipet.sts2.readonly_bridge");
                var lifecyclePatchesReady = PatchInstaller.Install(harmony);
                _client.SetCompatibility(publicHooksReady && lifecyclePatchesReady);
            }
            catch
            {
                // The mod is observational. Initialization failure must be
                // invisible to game flow.
            }
        }
    }

    internal static void OnHook(MethodBase originalMethod, object? instance, IReadOnlyList<object?> args)
    {
        try
        {
            _detector?.OnHook(originalMethod, instance, args);
        }
        catch
        {
            // Early Access state can disappear while rooms transition. A bad
            // snapshot is dropped instead of propagating into the game.
        }
    }

    internal static void OnPublicHook(string method, object? instance, params object?[] args)
    {
        try
        {
            _detector?.OnSignal(method, instance, args);
        }
        catch
        {
            // Public hooks share the same fail-closed boundary as Harmony
            // fallbacks. Observation can disappear; gameplay cannot.
        }
    }
}
