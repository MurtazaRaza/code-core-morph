namespace Parking
{
    /// <summary>What a stay costs.</summary>
    public interface IRatePlan
    {
        string Name { get; }

        Money Charge(Vehicle vehicle, int minutes);
    }
}
