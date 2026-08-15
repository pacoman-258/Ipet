using System.Collections;
using System.Reflection;

namespace Ipet.Sts2Bridge;

internal static class SnapshotReader
{
    private const BindingFlags AnyMember = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static;
    private static WeakReference<object>? _lastRunState;
    private static WeakReference<object>? _lastLocalPlayer;
    private static WeakReference<object>? _lastLocalCreature;

    internal static GameSnapshot Read(object? instance, IReadOnlyList<object?> args)
    {
        var runState = FindRunState(instance, args);
        if (runState is null)
        {
            return new GameSnapshot(new RunSnapshot(0, 0, 0, string.Empty), null, null, false, true);
        }

        _lastRunState = new WeakReference<object>(runState);
        var rawPlayers = ReadMember(runState, "Players");
        var players = Enumerate(rawPlayers).Take(8).ToList();
        var multiplayer = players.Count > 1;
        var localPlayer = ResolveLocalPlayer(rawPlayers, players);
        var localIdentified = localPlayer is not null || !multiplayer;
        if (localPlayer is not null)
        {
            _lastLocalPlayer = new WeakReference<object>(localPlayer);
            var localCreature = ReadMember(localPlayer, "Creature", "PlayerCreature");
            if (localCreature is not null)
            {
                _lastLocalCreature = new WeakReference<object>(localCreature);
            }
        }

        var roomType = Text(ReadMember(runState, "RunLocation", "MapLocation", "BaseRoom", "CurrentMapPoint"), 64);
        var run = new RunSnapshot(
            Int(ReadMember(runState, "CurrentActIndex", "ActIndex")) + 1,
            Int(ReadMember(runState, "TotalFloor", "ActFloor", "CurrentRoomCount")),
            Int(ReadMember(runState, "AscensionLevel")),
            roomType);
        var player = localPlayer is null ? null : ReadPlayer(localPlayer);
        var combat = ReadCombat(args, roomType);
        return new GameSnapshot(run, player, combat, multiplayer, localIdentified);
    }

    internal static bool IsLocalPlayer(object? candidate)
    {
        if (candidate is null)
        {
            return true;
        }
        if (_lastLocalPlayer is not null && _lastLocalPlayer.TryGetTarget(out var local))
        {
            return ReferenceEquals(candidate, local);
        }
        return true;
    }

    internal static bool IsLocalCreature(object? candidate)
    {
        if (candidate is null)
        {
            return false;
        }
        if (_lastLocalCreature is not null && _lastLocalCreature.TryGetTarget(out var localCreature))
        {
            return ReferenceEquals(candidate, localCreature);
        }
        return false;
    }

    internal static string IdFor(object? value)
    {
        if (value is null)
        {
            return string.Empty;
        }
        var id = ReadMember(value, "Id", "ID", "CanonicalId", "ModelId", "EntryId", "OptionId", "Index");
        var text = Text(id, 64);
        return string.IsNullOrWhiteSpace(text) ? Text(value.GetType().Name, 64) : text;
    }

    internal static string NameFor(object? value)
    {
        if (value is null)
        {
            return string.Empty;
        }
        return Text(ReadMember(value, "Name", "Title", "Label", "Description"), 80);
    }

    private static object? FindRunState(object? instance, IReadOnlyList<object?> args)
    {
        foreach (var value in args.Prepend(instance))
        {
            if (value is null)
            {
                continue;
            }
            if (LooksLikeRunState(value))
            {
                return value;
            }
            var direct = ReadMember(value, "RunState", "State", "CurrentRun", "Run");
            if (direct is not null && LooksLikeRunState(direct))
            {
                return direct;
            }
        }
        if (_lastRunState is not null && _lastRunState.TryGetTarget(out var cached))
        {
            return cached;
        }
        try
        {
            var type = Type.GetType("MegaCrit.Sts2.Core.Runs.RunManager, sts2", throwOnError: false);
            var manager = type is null ? null : ReadStaticMember(type, "Instance", "Current", "Singleton");
            var state = manager is null ? null : ReadMember(manager, "RunState", "State", "CurrentRun", "Run");
            return state is not null && LooksLikeRunState(state) ? state : null;
        }
        catch
        {
            return null;
        }
    }

    private static bool LooksLikeRunState(object value)
    {
        var typeName = value.GetType().FullName ?? value.GetType().Name;
        return typeName.Contains("RunState", StringComparison.Ordinal) && ReadMember(value, "Players") is not null;
    }

    internal static void RememberRunState(object runState)
    {
        _lastRunState = new WeakReference<object>(runState);
    }

    private static object? ResolveLocalPlayer(object? rawPlayers, IReadOnlyList<object> players)
    {
        if (players.Count == 0)
        {
            return null;
        }
        if (players.Count == 1)
        {
            return players[0];
        }
        try
        {
            var localContext = Type.GetType("MegaCrit.Sts2.Core.Context.LocalContext, sts2", throwOnError: false);
            if (localContext is null)
            {
                return null;
            }
            foreach (var method in localContext.GetMethods(AnyMember).Where(method => method.Name == "GetMe" && method.IsStatic))
            {
                var parameters = method.GetParameters();
                if (parameters.Length != 1)
                {
                    continue;
                }
                try
                {
                    var parameterType = parameters[0].ParameterType;
                    var argument = rawPlayers is not null && parameterType.IsInstanceOfType(rawPlayers)
                        ? rawPlayers
                        : parameterType.IsInstanceOfType(players)
                            ? players
                            : null;
                    if (argument is null)
                    {
                        continue;
                    }
                    var result = method.Invoke(null, [argument]);
                    if (result is not null)
                    {
                        return result;
                    }
                }
                catch
                {
                    // Try the other public overload. Failure means we cannot
                    // safely distinguish the local player in this build.
                }
            }
        }
        catch
        {
            // Fail closed for multiplayer identity.
        }
        return null;
    }

    private static PlayerSnapshot ReadPlayer(object player)
    {
        var creature = ReadMember(player, "Creature", "PlayerCreature") ?? player;
        var character = ReadMember(player, "Character", "CharacterModel", "Model");
        var deck = Enumerate(ReadMember(player, "Deck", "MasterDeck", "Cards")).Take(512).ToList();
        var relics = Enumerate(ReadMember(player, "Relics", "RelicModels")).Select(IdFor).Where(NotEmpty).Take(128).ToArray();
        var potions = Enumerate(ReadMember(player, "Potions", "PotionModels")).Select(IdFor).Where(NotEmpty).Take(128).ToArray();
        return new PlayerSnapshot(
            IdFor(character ?? player),
            Int(ReadMember(creature, "CurrentHp", "Hp", "Health")),
            Int(ReadMember(creature, "MaxHp", "MaxHealth")),
            Int(ReadMember(creature, "Block")),
            Int(ReadMember(player, "Energy", "CurrentEnergy")),
            Int(ReadMember(player, "Gold")),
            deck.Count,
            relics,
            potions);
    }

    private static CombatSnapshot? ReadCombat(IReadOnlyList<object?> args, string roomType)
    {
        object? state = args.FirstOrDefault(value => value?.GetType().Name.Contains("CombatState", StringComparison.Ordinal) == true);
        if (state is null)
        {
            try
            {
                var manager = Type.GetType("MegaCrit.Sts2.Core.Combat.CombatManager, sts2", throwOnError: false);
                var getter = manager?.GetMethods(AnyMember).FirstOrDefault(method => method.Name == "DebugOnlyGetState" && method.IsStatic);
                state = getter?.Invoke(null, null);
            }
            catch
            {
                state = null;
            }
        }
        if (state is null)
        {
            return null;
        }
        var enemies = Enumerate(ReadMember(state, "Enemies", "HittableEnemies")).Take(12).ToList();
        var enemyIds = enemies.Select(IdFor).Where(NotEmpty).ToArray();
        var intentIds = enemies
            .Select(enemy => IdFor(ReadMember(enemy, "Intent", "CurrentIntent", "Move")))
            .Where(NotEmpty)
            .Take(12)
            .ToArray();
        var normalizedRoom = roomType.ToLowerInvariant();
        return new CombatSnapshot(
            Int(ReadMember(state, "RoundNumber", "Round")),
            enemyIds,
            intentIds,
            normalizedRoom.Contains("elite", StringComparison.Ordinal),
            normalizedRoom.Contains("boss", StringComparison.Ordinal));
    }

    private static object? ReadMember(object target, params string[] names)
    {
        var type = target.GetType();
        foreach (var name in names)
        {
            try
            {
                var property = type.GetProperty(name, AnyMember);
                if (property?.GetIndexParameters().Length == 0)
                {
                    return property.GetValue(target);
                }
                var field = type.GetField(name, AnyMember);
                if (field is not null)
                {
                    return field.GetValue(target);
                }
            }
            catch
            {
                // A volatile Godot object may disappear while a room exits.
            }
        }
        return null;
    }

    private static object? ReadStaticMember(Type type, params string[] names)
    {
        foreach (var name in names)
        {
            try
            {
                var property = type.GetProperty(name, AnyMember);
                if (property?.GetMethod?.IsStatic == true)
                {
                    return property.GetValue(null);
                }
                var field = type.GetField(name, AnyMember);
                if (field?.IsStatic == true)
                {
                    return field.GetValue(null);
                }
            }
            catch
            {
                // Compatibility probing is best effort.
            }
        }
        return null;
    }

    private static IEnumerable<object> Enumerate(object? value)
    {
        if (value is not IEnumerable enumerable || value is string)
        {
            yield break;
        }
        foreach (var item in enumerable)
        {
            if (item is not null)
            {
                yield return item;
            }
        }
    }

    private static int Int(object? value)
    {
        try
        {
            return Math.Max(0, Convert.ToInt32(value));
        }
        catch
        {
            return 0;
        }
    }

    private static string Text(object? value, int limit)
    {
        var text = string.Join(" ", Convert.ToString(value)?.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries) ?? []);
        return text.Length <= limit ? text : text[..limit];
    }

    private static bool NotEmpty(string value) => !string.IsNullOrWhiteSpace(value);
}
