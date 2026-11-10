using System;

namespace Parking
{
    /// <summary>One fee per stay, however long.</summary>
    public sealed class FlatRate : IRatePlan
    {
        private readonly Money fee;

        public FlatRate(Money fee)
        {
            this.fee = fee;
        }

        public string Name
        {
            get { return "flat " + fee; }
        }

        public Money Charge(Vehicle vehicle, int minutes)
        {
            if (vehicle == null)
            {
                throw new ArgumentNullException("vehicle");
            }
            if (minutes < 0)
            {
                throw new ArgumentOutOfRangeException("minutes", "A stay can't be negative.");
            }
            return fee * vehicle.SizeFactor;
        }
    }
}
