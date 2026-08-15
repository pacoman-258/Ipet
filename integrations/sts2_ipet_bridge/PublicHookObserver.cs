using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Merchant;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.GameActions.Multiplayer;
using MegaCrit.Sts2.Core.Modding;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Rewards;
using MegaCrit.Sts2.Core.Rooms;

namespace Ipet.Sts2Bridge;

/// <summary>
/// Receives the game's supported run/combat hooks. Harmony is reserved for UI
/// selections and lifecycle gaps that this surface does not expose.
/// </summary>
internal sealed class PublicHookObserver : SingletonModel
{
    private const string SubscriptionId = "ipet.sts2.readonly_bridge";
    private static PublicHookObserver Observer => ModelDb.Singleton<PublicHookObserver>();

    public override bool ShouldReceiveCombatHooks => true;

    internal static bool Install()
    {
        try
        {
            ModHelper.SubscribeForRunStateHooks(SubscriptionId, runState =>
            {
                SnapshotReader.RememberRunState(runState);
                return [Observer];
            });
            ModHelper.SubscribeForCombatStateHooks(SubscriptionId, _ => [Observer]);
            return true;
        }
        catch
        {
            return false;
        }
    }

    public override Task AfterActEntered() => Signal(nameof(AfterActEntered));

    public override Task BeforeCombatStart() => Signal(nameof(BeforeCombatStart));

    public override Task AfterCombatEnd(CombatRoom room) => Signal(nameof(AfterCombatEnd), room);

    public override Task AfterCombatVictory(CombatRoom room) => Signal(nameof(AfterCombatVictory), room);

    public override Task AfterCurrentHpChanged(Creature creature, decimal delta) =>
        Signal(nameof(AfterCurrentHpChanged), creature, delta);

    public override Task AfterDeath(
        PlayerChoiceContext choiceContext,
        Creature creature,
        bool wasRemovalPrevented,
        float deathAnimLength) =>
        Signal(nameof(AfterDeath), creature, wasRemovalPrevented, deathAnimLength);

    public override Task AfterItemPurchased(Player player, MerchantEntry itemPurchased, int goldSpent) =>
        Signal(nameof(AfterItemPurchased), player, itemPurchased, goldSpent);

    public override Task AfterPotionProcured(PotionModel potion) =>
        Signal(nameof(AfterPotionProcured), potion);

    public override Task AfterPotionUsed(PotionModel potion, Creature? target) =>
        Signal(nameof(AfterPotionUsed), potion, target);

    public override Task AfterRestSiteHeal(Player player, bool isMimicked) =>
        Signal(nameof(AfterRestSiteHeal), player, isMimicked);

    public override Task AfterRestSiteSmith(Player player) =>
        Signal(nameof(AfterRestSiteSmith), player);

    public override Task AfterRewardTaken(Player player, Reward reward) =>
        Signal(nameof(AfterRewardTaken), player, reward);

    public override Task BeforeRoomEntered(AbstractRoom room) =>
        Signal(nameof(BeforeRoomEntered), room);

    public override Task AfterSideTurnStart(
        CombatSide side,
        IReadOnlyList<Creature> creatures,
        ICombatState combatState) =>
        Signal(nameof(AfterSideTurnStart), side, creatures, combatState);

    private static Task Signal(string method, params object?[] args)
    {
        BridgeRuntime.OnPublicHook(method, Observer, args);
        return Task.CompletedTask;
    }
}
