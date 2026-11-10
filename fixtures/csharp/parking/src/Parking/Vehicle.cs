using System;

namespace Parking
{
    /// <summary>A vehicle, known by its plate.</summary>
    public abstract class Vehicle
    {
        private readonly string plate;

        protected Vehicle(string plate)
        {
            if (plate == null)
            {
                throw new ArgumentNullException("plate");
            }
            plate = plate.Trim();
            if (plate.Length == 0)
            {
                throw new ArgumentException("A plate can't be empty.", "plate");
            }
            this.plate = plate.ToUpperInvariant();
        }

        /// <summary>The plate, trimmed and in upper case.</summary>
        public string Plate
        {
            get { return plate; }
        }

        public abstract int Wheels { get; }

        public abstract string Kind { get; }

        /// <summary>How much of a car's place it takes: fees scale with it.</summary>
        public virtual decimal SizeFactor
        {
            get { return 1m; }
        }

        public override string ToString()
        {
            return Kind + " " + plate;
        }
    }
}
