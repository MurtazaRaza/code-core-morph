using System;

namespace Rates
{
    /// <summary>A parcel to ship: its weight in grams and its zone.</summary>
    public sealed class Parcel
    {
        private readonly int grams;
        private readonly Zone zone;

        public Parcel(int grams, Zone zone)
        {
            if (grams <= 0)
            {
                throw new ArgumentOutOfRangeException("grams", "a parcel weighs something");
            }
            this.grams = grams;
            this.zone = zone;
        }

        public int Grams
        {
            get { return grams; }
        }

        public Zone Zone
        {
            get { return zone; }
        }

        public override bool Equals(object obj)
        {
            Parcel other = obj as Parcel;
            if (other == null)
            {
                return false;
            }
            return grams == other.grams && zone == other.zone;
        }

        public override int GetHashCode()
        {
            return grams * 397 ^ (int)zone;
        }
    }
}
