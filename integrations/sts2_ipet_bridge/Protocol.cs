using System.Text.Json.Serialization;

namespace Ipet.Sts2Bridge;

internal static class Protocol
{
    internal const int SchemaVersion = 1;
    internal const string GameId = "slay_the_spire_2";
    internal const string AdapterVersion = "0.1.3";
}

internal sealed record RunSnapshot(
    [property: JsonPropertyName("act")] int Act,
    [property: JsonPropertyName("floor")] int Floor,
    [property: JsonPropertyName("ascension")] int Ascension,
    [property: JsonPropertyName("room_type")] string RoomType);

internal sealed record PlayerSnapshot(
    [property: JsonPropertyName("character_id")] string CharacterId,
    [property: JsonPropertyName("hp")] int Hp,
    [property: JsonPropertyName("max_hp")] int MaxHp,
    [property: JsonPropertyName("block")] int Block,
    [property: JsonPropertyName("energy")] int Energy,
    [property: JsonPropertyName("gold")] int Gold,
    [property: JsonPropertyName("deck_size")] int DeckSize,
    [property: JsonPropertyName("relic_ids")] IReadOnlyList<string> RelicIds,
    [property: JsonPropertyName("potion_ids")] IReadOnlyList<string> PotionIds);

internal sealed record CombatSnapshot(
    [property: JsonPropertyName("round")] int Round,
    [property: JsonPropertyName("enemy_ids")] IReadOnlyList<string> EnemyIds,
    [property: JsonPropertyName("intent_ids")] IReadOnlyList<string> IntentIds,
    [property: JsonPropertyName("is_elite")] bool IsElite,
    [property: JsonPropertyName("is_boss")] bool IsBoss);

internal sealed record GameSnapshot(
    [property: JsonPropertyName("run")] RunSnapshot Run,
    [property: JsonPropertyName("player")] PlayerSnapshot? Player,
    [property: JsonPropertyName("combat")] CombatSnapshot? Combat,
    bool Multiplayer,
    bool LocalPlayerIdentified)
{
    internal object ToWireSnapshot() => new
    {
        run = Run,
        player = Player,
        combat = Combat,
    };
}

internal sealed record PendingEvent(string Kind, IReadOnlyDictionary<string, object?> Facts, GameSnapshot Snapshot);

internal sealed record SnapshotDelta(string Kind, IReadOnlyDictionary<string, object?> Facts);

internal sealed record SnapshotDiffResult(IReadOnlyList<SnapshotDelta> Events, bool LowHpLatched);

internal static class SnapshotDiff
{
    internal static SnapshotDiffResult Compute(PlayerSnapshot previous, PlayerSnapshot current, bool lowHpLatched)
    {
        var events = new List<SnapshotDelta>();
        if (Thresholds.IsRecoveredFromLowHp(current.Hp, current.MaxHp))
        {
            lowHpLatched = false;
        }
        if (current.Hp < previous.Hp)
        {
            var damage = previous.Hp - current.Hp;
            if (Thresholds.IsLargeDamage(damage, current.MaxHp))
            {
                events.Add(new SnapshotDelta("large_damage_taken", HealthFacts(current, "damage", damage)));
            }
            if (!lowHpLatched && Thresholds.IsLowHp(current.Hp, current.MaxHp))
            {
                lowHpLatched = true;
                events.Add(new SnapshotDelta("low_hp_entered", HealthFacts(current)));
            }
        }
        else if (current.Hp > previous.Hp)
        {
            var healed = current.Hp - previous.Hp;
            if (Thresholds.IsLargeHeal(healed, current.MaxHp))
            {
                events.Add(new SnapshotDelta("heal_received", HealthFacts(current, "healed", healed)));
            }
        }
        foreach (var relic in current.RelicIds.Except(previous.RelicIds, StringComparer.Ordinal).Take(4))
        {
            events.Add(new SnapshotDelta("relic_obtained", new Dictionary<string, object?> { ["relic_id"] = relic }));
        }
        foreach (var potion in current.PotionIds.Except(previous.PotionIds, StringComparer.Ordinal).Take(4))
        {
            events.Add(new SnapshotDelta("potion_obtained", new Dictionary<string, object?> { ["potion_id"] = potion }));
        }
        if (current.DeckSize > previous.DeckSize)
        {
            events.Add(new SnapshotDelta("card_selected", new Dictionary<string, object?> { ["deck_size"] = current.DeckSize }));
        }
        else if (current.DeckSize < previous.DeckSize)
        {
            events.Add(new SnapshotDelta("card_removed", new Dictionary<string, object?> { ["deck_size"] = current.DeckSize }));
        }
        return new SnapshotDiffResult(events, lowHpLatched);
    }

    private static Dictionary<string, object?> HealthFacts(
        PlayerSnapshot player,
        string? deltaKey = null,
        int delta = 0)
    {
        var facts = new Dictionary<string, object?>
        {
            ["hp"] = player.Hp,
            ["max_hp"] = player.MaxHp,
        };
        if (!string.IsNullOrEmpty(deltaKey))
        {
            facts[deltaKey] = delta;
        }
        return facts;
    }
}

internal static class Thresholds
{
    internal static bool IsLargeDamage(int damage, int maxHp) => damage >= Math.Max(10, maxHp * 0.2);

    internal static bool IsLowHp(int hp, int maxHp) => maxHp > 0 && hp <= maxHp * 0.25;

    internal static bool IsRecoveredFromLowHp(int hp, int maxHp) => maxHp > 0 && hp > maxHp * 0.35;

    internal static bool IsLargeHeal(int healed, int maxHp) => maxHp > 0 && healed >= maxHp * 0.2;
}
