using System;
using System.Collections.Generic;

namespace Parking.Tests
{
    /// <summary>A rate plan that charges one fee and remembers what it was asked.</summary>
    internal sealed class RecordingPlan : IRatePlan
    {
        private readonly Money fee;

        public readonly List<Vehicle> Vehicles = new List<Vehicle>();

        public readonly List<int> Minutes = new List<int>();

        public RecordingPlan(Money fee)
        {
            this.fee = fee;
        }

        /// <summary>When set, what Charge throws.</summary>
        public Exception Failure { get; set; }

        public string Name
        {
            get { return "recording"; }
        }

        public Money Charge(Vehicle vehicle, int minutes)
        {
            if (Failure != null)
            {
                throw Failure;
            }
            Vehicles.Add(vehicle);
            Minutes.Add(minutes);
            return fee;
        }
    }
}
