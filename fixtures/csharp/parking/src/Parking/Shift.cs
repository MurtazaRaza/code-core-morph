using System;

namespace Parking
{
    /// <summary>Adds up a garage's departures until it is disposed.</summary>
    public sealed class Shift : IDisposable
    {
        private readonly Garage garage;
        private Money takings = Money.Zero;
        private int departures;
        private bool closed;

        public Shift(Garage garage)
        {
            if (garage == null)
            {
                throw new ArgumentNullException("garage");
            }
            this.garage = garage;
            garage.VehicleLeft += OnVehicleLeft;
        }

        public Money Takings
        {
            get { return takings; }
        }

        public int Departures
        {
            get { return departures; }
        }

        public bool IsOpen
        {
            get { return !closed; }
        }

        public void Dispose()
        {
            if (closed)
            {
                return;
            }
            garage.VehicleLeft -= OnVehicleLeft;
            closed = true;
        }

        private void OnVehicleLeft(string plate, Money fee)
        {
            takings = takings + fee;
            departures++;
        }
    }
}
