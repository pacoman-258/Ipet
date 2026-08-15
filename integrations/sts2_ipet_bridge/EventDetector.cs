using System.Reflection;

namespace Ipet.Sts2Bridge;

internal sealed class EventDetector(BridgeClient client)
{
    private GameSnapshot? _previous;
    private bool _runActive;
    private bool _combatLost;
    private bool _lowHpLatched;

    internal void OnHook(MethodBase originalMethod, object? instance, IReadOnlyList<object?> args)
    {
        OnSignal(originalMethod.Name, originalMethod.DeclaringType?.FullName ?? string.Empty, instance, args);
    }

    internal void OnSignal(string method, object? instance, IReadOnlyList<object?> args)
    {
        OnSignal(method, instance?.GetType().FullName ?? string.Empty, instance, args);
    }

    private void OnSignal(string method, string typeName, object? instance, IReadOnlyList<object?> args)
    {
        var snapshot = SnapshotReader.Read(instance, args);
        client.UpdateSnapshot(snapshot);

        if (method is "InitializeNewRun" or "InitializeSavedRun" or "StartRun")
        {
            _previous = snapshot;
            if (_runActive)
            {
                return;
            }
            _runActive = true;
            _combatLost = false;
            _lowHpLatched = false;
            client.BeginRun(snapshot);
            if (!snapshot.LocalPlayerIdentified)
            {
                return;
            }
            Emit("run_started", snapshot, new Dictionary<string, object?>
            {
                ["act"] = snapshot.Run.Act,
                ["floor"] = snapshot.Run.Floor,
                ["character_id"] = snapshot.Player?.CharacterId ?? string.Empty,
            });
            return;
        }

        if (!snapshot.LocalPlayerIdentified)
        {
            _previous = snapshot;
            return;
        }
        if (IsRemotePlayerSpecific(typeName, instance, args))
        {
            _previous = snapshot;
            return;
        }

        EmitStateDiffs(snapshot);

        switch (method)
        {
            case "SetActInternal":
            case "AfterActEntered":
                Emit("act_started", snapshot, new Dictionary<string, object?> { ["act"] = snapshot.Run.Act });
                break;
            case "EnterMapPointInternal":
            case "EnterRoom":
            case "EnterRoomInternal":
            case "BeforeRoomEntered":
                EmitRoomEvent(snapshot, args);
                break;
            case "SetUpCombat":
            case "BeforeCombatStart":
                _combatLost = false;
                Emit("combat_started", snapshot, CombatFacts(snapshot));
                break;
            case "SetupPlayerTurn":
            case "AfterSideTurnStart" when IsPlayerSide(args):
                Emit("turn_started", snapshot, new Dictionary<string, object?>
                {
                    ["round"] = snapshot.Combat?.Round ?? 0,
                });
                break;
            case "LoseCombat":
            case "HandlePlayerDeath":
                _combatLost = true;
                Emit("combat_lost", snapshot, CombatFacts(snapshot));
                break;
            case "EndCombatInternal":
            case "AfterCombatVictory":
                if (!_combatLost)
                {
                    Emit("combat_won", snapshot, CombatFacts(snapshot));
                }
                break;
            case "AfterCombatEnd":
                break;
            case "OnEnded":
                var won = args.OfType<bool>().FirstOrDefault();
                Emit(won ? "run_won" : "run_lost", snapshot, new Dictionary<string, object?>
                {
                    ["act"] = snapshot.Run.Act,
                    ["floor"] = snapshot.Run.Floor,
                    ["result"] = won ? "won" : "lost",
                });
                client.EndRun();
                _runActive = false;
                break;
            case "Abandon":
                Emit("run_abandoned", snapshot, new Dictionary<string, object?>
                {
                    ["act"] = snapshot.Run.Act,
                    ["floor"] = snapshot.Run.Floor,
                    ["result"] = "abandoned",
                });
                client.EndRun();
                _runActive = false;
                break;
            case "RewardCollectedFrom":
            case "SelectCard":
                EmitModelEvent("card_selected", "card", snapshot, args.FirstOrDefault());
                break;
            case "RewardSkippedFrom":
                Emit("card_skipped", snapshot, new Dictionary<string, object?>());
                break;
            case "OptionButtonClicked":
            case "BeforeOptionChosen":
                EmitChoiceEvent("event_choice_made", snapshot, args.FirstOrDefault());
                break;
            case "OnSelect" when typeName.Contains("RestSite", StringComparison.Ordinal):
                EmitChoiceEvent("rest_site_choice", snapshot, instance);
                break;
            case "OnTryPurchase":
                EmitModelEvent("shop_purchase", "item", snapshot, instance);
                break;
            case "AfterItemPurchased":
                var purchasedItem = args.FirstOrDefault(value => value?.GetType().FullName?.Contains("MerchantEntry", StringComparison.Ordinal) == true);
                EmitModelEvent("shop_purchase", "item", snapshot, purchasedItem);
                break;
            case "UsePotion":
            case "RemoveUsedPotion":
                EmitModelEvent("potion_used", "potion", snapshot, instance);
                break;
            case "AfterPotionUsed":
                var usedPotion = args.FirstOrDefault(value => value?.GetType().FullName?.Contains("PotionModel", StringComparison.Ordinal) == true);
                EmitModelEvent("potion_used", "potion", snapshot, usedPotion);
                break;
            case "AfterPotionProcured":
                var procuredPotion = args.FirstOrDefault(value => value?.GetType().FullName?.Contains("PotionModel", StringComparison.Ordinal) == true);
                EmitModelEvent("potion_obtained", "potion", snapshot, procuredPotion);
                break;
            case "AfterRestSiteHeal":
                Emit("rest_site_choice", snapshot, new Dictionary<string, object?> { ["choice_id"] = "heal" });
                break;
            case "AfterRestSiteSmith":
                Emit("rest_site_choice", snapshot, new Dictionary<string, object?> { ["choice_id"] = "smith" });
                Emit("card_upgraded", snapshot, new Dictionary<string, object?> { ["source_id"] = "rest_site_smith" });
                break;
            case "AfterRewardTaken":
                // State diffs above identify cards/relics/potions without
                // trusting localized reward text. The hook is still useful as
                // the exact post-selection snapshot boundary.
                break;
            case "Die":
            case "OnDeath":
                if (!typeName.Contains("Player", StringComparison.Ordinal))
                {
                    EmitModelEvent("enemy_defeated", "enemy", snapshot, instance);
                }
                break;
            case "AfterDeath":
                var creature = args.FirstOrDefault(value => value?.GetType().FullName?.Contains("Entities.Creatures.Creature", StringComparison.Ordinal) == true);
                if (SnapshotReader.IsLocalCreature(creature))
                {
                    _combatLost = true;
                    Emit("combat_lost", snapshot, CombatFacts(snapshot));
                }
                else
                {
                    EmitModelEvent("enemy_defeated", "enemy", snapshot, creature);
                }
                break;
        }

        _previous = snapshot;
    }

    private void EmitStateDiffs(GameSnapshot snapshot)
    {
        var current = snapshot.Player;
        var previous = _previous?.Player;
        if (current is null || previous is null)
        {
            _previous = snapshot;
            return;
        }
        var diff = SnapshotDiff.Compute(previous, current, _lowHpLatched);
        _lowHpLatched = diff.LowHpLatched;
        foreach (var detected in diff.Events)
        {
            Emit(detected.Kind, snapshot, detected.Facts);
        }
    }

    private void EmitRoomEvent(GameSnapshot snapshot, IReadOnlyList<object?> args)
    {
        var roomText = string.Join(" ", args.Where(value => value is not null).Select(value => value!.ToString())).ToLowerInvariant();
        var roomType = snapshot.Run.RoomType;
        var kind = roomText.Contains("boss", StringComparison.Ordinal) ? "boss_entered"
            : roomText.Contains("elite", StringComparison.Ordinal) ? "elite_entered"
            : roomText.Contains("shop", StringComparison.Ordinal) || roomText.Contains("merchant", StringComparison.Ordinal) ? "shop_entered"
            : "room_entered";
        Emit(kind, snapshot, new Dictionary<string, object?> { ["room_type"] = roomType });
    }

    private void EmitChoiceEvent(string kind, GameSnapshot snapshot, object? choice)
    {
        Emit(kind, snapshot, new Dictionary<string, object?>
        {
            ["choice_id"] = SnapshotReader.IdFor(choice),
            ["choice_label"] = SnapshotReader.NameFor(choice),
        });
    }

    private void EmitModelEvent(string kind, string modelKind, GameSnapshot snapshot, object? model)
    {
        var facts = new Dictionary<string, object?>();
        var idKey = modelKind switch
        {
            "card" => "card_id",
            "potion" => "potion_id",
            "enemy" => "enemy_id",
            _ => "item_id",
        };
        var nameKey = modelKind switch
        {
            "card" => "card_name",
            "potion" => "potion_name",
            "enemy" => "enemy_name",
            _ => "item_type",
        };
        facts[idKey] = SnapshotReader.IdFor(model);
        facts[nameKey] = SnapshotReader.NameFor(model);
        Emit(kind, snapshot, facts);
    }

    private static Dictionary<string, object?> CombatFacts(GameSnapshot snapshot) => new()
    {
        ["round"] = snapshot.Combat?.Round ?? 0,
        ["enemy_ids"] = snapshot.Combat?.EnemyIds ?? [],
        ["is_elite"] = snapshot.Combat?.IsElite ?? false,
        ["is_boss"] = snapshot.Combat?.IsBoss ?? false,
    };

    private static bool IsRemotePlayerSpecific(string typeName, object? instance, IReadOnlyList<object?> args)
    {
        if (typeName.Contains("Entities.Players.Player", StringComparison.Ordinal) && !SnapshotReader.IsLocalPlayer(instance))
        {
            return true;
        }
        var playerArgument = args.FirstOrDefault(value => value?.GetType().FullName?.Contains("Entities.Players.Player", StringComparison.Ordinal) == true);
        return playerArgument is not null && !SnapshotReader.IsLocalPlayer(playerArgument);
    }

    private static bool IsPlayerSide(IReadOnlyList<object?> args)
    {
        var side = args.FirstOrDefault(value => value?.GetType().Name.Contains("CombatSide", StringComparison.Ordinal) == true);
        var name = Convert.ToString(side) ?? string.Empty;
        return name.Contains("player", StringComparison.OrdinalIgnoreCase);
    }

    private void Emit(string kind, GameSnapshot snapshot, IReadOnlyDictionary<string, object?> facts)
    {
        client.Emit(new PendingEvent(kind, facts, snapshot));
    }
}
