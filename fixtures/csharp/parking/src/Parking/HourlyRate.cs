using System;

namespace Parking
{
    /// <summary>A fee per started hour, at most a daily cap for each day.</summary>
    public sealed class HourlyRate : IRatePlan
    {
        private readonly Money perHour;
        private readonly Money dailyCap;

        public HourlyRate(Money perHour, Money dailyCap)
        {
            if (dailyCap.Amount < perHour.Amount)
            {
                throw new ArgumentException("The daily cap is less than an hour.", "dailyCap");
            }
            this.perHour = perHour;
            this.dailyCap = dailyCap;
        }

        public Money PerHour
        {
            get { return perHour; }
        }

        public Money DailyCap
        {
            get { return dailyCap; }
        }

        public string Name
        {
            get { return "hourly " + perHour; }
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
            int days = minutes / Limits.MinutesPerDay;
            int rest = minutes % Limits.MinutesPerDay;
            int hours = (rest + Limits.MinutesPerHour - 1) / Limits.MinutesPerHour;
            Money total = dailyCap * days + Money.Min(perHour * hours, dailyCap);
            return total * vehicle.SizeFactor;
        }
    }
}
