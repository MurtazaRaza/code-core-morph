using System;

namespace Parking
{
    /// <summary>Thrown when a vehicle comes to a garage with no free place.</summary>
    public class GarageFullException : InvalidOperationException
    {
        public GarageFullException(int capacity)
            : base("The garage is full (" + capacity + " places).")
        {
            Capacity = capacity;
        }

        public int Capacity { get; private set; }
    }
}
