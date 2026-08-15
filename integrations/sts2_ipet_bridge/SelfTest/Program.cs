using System.Text.Json;
using Ipet.Sts2Bridge;

static void Check(bool condition, string message)
{
    if (!condition)
    {
        throw new InvalidOperationException(message);
    }
}

var baseline = new PlayerSnapshot("ironclad", 80, 80, 0, 3, 99, 10, ["starter"], []);
var hurt = baseline with { Hp = 20, DeckSize = 11, RelicIds = ["starter", "new_relic"] };
var diff = SnapshotDiff.Compute(baseline, hurt, false);
Check(diff.Events.Any(item => item.Kind == "large_damage_taken"), "large damage diff missing");
Check(diff.Events.Any(item => item.Kind == "low_hp_entered"), "low HP crossing missing");
Check(diff.Events.Any(item => item.Kind == "card_selected"), "deck growth diff missing");
Check(diff.Events.Any(item => item.Kind == "relic_obtained"), "relic diff missing");
Check(diff.LowHpLatched, "low HP latch missing");

var recovered = hurt with { Hp = 40 };
var recovery = SnapshotDiff.Compute(hurt, recovered, true);
Check(!recovery.LowHpLatched, "low HP latch did not reset above 35%");
Check(recovery.Events.Any(item => item.Kind == "heal_received"), "large heal diff missing");
Check(Thresholds.IsLargeDamage(16, 80), "20% damage threshold mismatch");
Check(!Thresholds.IsLargeDamage(15, 80), "damage threshold accepted too early");

var snapshot = new GameSnapshot(new RunSnapshot(1, 4, 0, "combat"), hurt, null, false, true);
var json = JsonSerializer.Serialize(snapshot.ToWireSnapshot(), new JsonSerializerOptions(JsonSerializerDefaults.Web)
{
    PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
});
Check(json.Contains("\"max_hp\":80", StringComparison.Ordinal), "wire snapshot casing mismatch");
Check(!json.Contains("local_player_identified", StringComparison.Ordinal), "internal identity leaked into snapshot");

var single = SnapshotReader.Read(new FakeRunState([new FakePlayer()]), []);
Check(single.LocalPlayerIdentified, "single-player identity should be reliable");
var multiplayer = SnapshotReader.Read(new FakeRunState([new FakePlayer(), new FakePlayer()]), []);
Check(!multiplayer.LocalPlayerIdentified, "unknown multiplayer identity must fail closed");

var discoveryPath = Path.GetTempFileName();
try
{
    File.WriteAllText(discoveryPath, "{\"base_url\":\"http://127.0.0.1:8017\",\"token\":\"secret-token\"}");
    Check(
        BridgeClient.ResolveBaseUrl(discoveryPath) == "http://127.0.0.1:8017",
        "loopback endpoint discovery failed");
    Check(
        BridgeClient.ResolveToken(discoveryPath) == "secret-token",
        "token discovery failed");
    File.WriteAllText(discoveryPath, "{\"base_url\":\"https://example.com:8017\"}");
    Check(
        BridgeClient.ResolveBaseUrl(discoveryPath) == "http://127.0.0.1:8008",
        "non-loopback endpoint must be rejected");
    Check(
        BridgeClient.ResolveToken(discoveryPath) == string.Empty,
        "missing token should resolve to empty");
}
finally
{
    File.Delete(discoveryPath);
}

Console.WriteLine("Ipet STS2 bridge self-test passed.");

internal sealed class FakeRunState(IReadOnlyList<FakePlayer> players)
{
    public IReadOnlyList<FakePlayer> Players { get; } = players;
    public int CurrentActIndex => 0;
    public int TotalFloor => 1;
}

internal sealed class FakePlayer
{
    public int CurrentHp => 80;
    public int MaxHp => 80;
    public IReadOnlyList<object> Deck => [];
    public IReadOnlyList<object> Relics => [];
    public IReadOnlyList<object> Potions => [];
}
