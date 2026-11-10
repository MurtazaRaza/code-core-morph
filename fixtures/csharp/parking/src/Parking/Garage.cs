using System;
using System.Collections.Generic;
using System.Linq;

namespace Parking
{
    /// <summary>A garage with a fixed number of places, charging by a rate plan.</summary>
    public sealed class Garage
    {
        private readonly List<Vehicle> parked = new List<Vehicle>();
        private readonly Dictionary<string, int> entered = new Dictionary<string, int>();
        private readonly Tally<string> visits = new Tally<string>();
        private readonly Tally<int> hours = new Tally<int>();
        private readonly IRatePlan plan;
        private Money takings = Money.Zero;

        public Garage(int capacity, IRatePlan plan)
        {
            if (capacity < 1 || capacity > Limits.MaxCapacity)
            {
                throw new ArgumentOutOfRangeException(
                    "capacity", "A garage has 1 to " + Limits.MaxCapacity + " places.");
            }
            if (plan == null)
            {
                throw new ArgumentNullException("plan");
            }
            Capacity = capacity;
            this.plan = plan;
        }

        /// <summary>Raised after a vehicle left, with its plate and its fee.</summary>
        public event Action<string, Money> VehicleLeft;

        public int Capacity { get; private set; }

        public int Count
        {
            get { return parked.Count; }
        }

        public int Free
        {
            get { return Capacity - parked.Count; }
        }

        public Money Takings
        {
            get { return takings; }
        }

        public IRatePlan Plan
        {
            get { return plan; }
        }

        /// <summary>The parked vehicle with this plate.</summary>
        public Vehicle this[string plate]
        {
            get
            {
                string key = Key(plate);
                foreach (Vehicle vehicle in parked)
                {
                    if (vehicle.Plate == key)
                    {
                        return vehicle;
                    }
                }
                throw new KeyNotFoundException("No vehicle " + key + " is parked.");
            }
        }

        public void Enter(Vehicle vehicle, int minute)
        {
            if (vehicle == null)
            {
                throw new ArgumentNullException("vehicle");
            }
            if (minute < 0)
            {
                throw new ArgumentOutOfRangeException("minute", "Time starts at minute 0.");
            }
            if (entered.ContainsKey(vehicle.Plate))
            {
                throw new InvalidOperationException(vehicle.Plate + " is parked already.");
            }
            if (parked.Count == Capacity)
            {
                throw new GarageFullException(Capacity);
            }
            parked.Add(vehicle);
            entered.Add(vehicle.Plate, minute);
            visits.Add(vehicle.Plate);
            hours.Add(minute / Limits.MinutesPerHour % 24);
        }

        /// <summary>Lets the vehicle out at a minute, charging the plan's fee for its stay.</summary>
        public Money Leave(string plate, int minute)
        {
            Vehicle vehicle = this[plate];
            int since = entered[vehicle.Plate];
            if (minute < since)
            {
                throw new ArgumentOutOfRangeException("minute", "A vehicle can't leave before it came.");
            }
            Money fee = plan.Charge(vehicle, minute - since);
            parked.Remove(vehicle);
            entered.Remove(vehicle.Plate);
            takings = takings + fee;
            Action<string, Money> left = VehicleLeft;
            if (left != null)
            {
                left(vehicle.Plate, fee);
            }
            return fee;
        }

        /// <summary>How many times a vehicle with this plate came in.</summary>
        public int VisitsOf(string plate)
        {
            return visits[Key(plate)];
        }

        /// <summary>The hour of the day in which most vehicles came in.</summary>
        public int BusiestHour()
        {
            return hours.MostCommon();
        }

        public int CountWhere(Func<Vehicle, bool> predicate)
        {
            if (predicate == null)
            {
                throw new ArgumentNullException("predicate");
            }
            return parked.Count(predicate);
        }

        /// <summary>The parked vehicles' plates, in ordinal order.</summary>
        public IList<string> Plates()
        {
            return parked.Select(v => v.Plate).OrderBy(p => p, StringComparer.Ordinal).ToList();
        }

        private static string Key(string plate)
        {
            if (plate == null)
            {
                throw new ArgumentNullException("plate");
            }
            return plate.Trim().ToUpperInvariant();
        }
    }
}
