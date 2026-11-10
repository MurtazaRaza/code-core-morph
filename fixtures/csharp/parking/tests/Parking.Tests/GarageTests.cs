using System;
using System.Collections.Generic;
using System.Linq;
using NUnit.Framework;

namespace Parking.Tests
{
    [TestFixture]
    public class GarageTests
    {
        private static Garage NewGarage(int capacity)
        {
            return new Garage(capacity, new HourlyRate(new Money(2.50m), new Money(20m)));
        }

        [Test]
        public void ParkingTakesAPlace()
        {
            Garage garage = NewGarage(2);
            garage.Enter(new Car("a-1"), 0);
            Assert.AreEqual(1, garage.Count);
            Assert.AreEqual(1, garage.Free);
            Assert.AreEqual(2, garage.Capacity);
        }

        [Test]
        public void LeavingChargesThePlan()
        {
            Garage garage = NewGarage(2);
            garage.Enter(new Car("a-1"), 30);
            Assert.AreEqual(new Money(5m), garage.Leave("A-1", 120));
            Assert.AreEqual(0, garage.Count);
            Assert.AreEqual(new Money(5m), garage.Takings);
        }

        [Test]
        public void TheIndexerFindsTheSameVehicle()
        {
            Garage garage = NewGarage(2);
            var car = new Car("k-9");
            garage.Enter(car, 0);
            Assert.AreSame(car, garage[" k-9 "]);
        }

        [Test]
        public void AnUnknownPlateIsNotFound()
        {
            Garage garage = NewGarage(2);
            Assert.Throws<KeyNotFoundException>(() => garage.Leave("nope", 10));
            Assert.Throws<KeyNotFoundException>(() => { Vehicle unused = garage["nope"]; });
        }

        [Test]
        public void AFullGarageRefusesAVehicle()
        {
            Garage garage = NewGarage(1);
            garage.Enter(new Car("a"), 0);
            var e = Assert.Throws<GarageFullException>(() => garage.Enter(new Car("b"), 1));
            Assert.AreEqual(1, e.Capacity);
            Assert.AreEqual("The garage is full (1 places).", e.Message);
        }

        [Test]
        public void AVehicleCannotParkTwice()
        {
            Garage garage = NewGarage(3);
            garage.Enter(new Car("a"), 0);
            Assert.Throws<InvalidOperationException>(() => garage.Enter(new Motorcycle("A"), 5));
            Assert.AreEqual(1, garage.Count);
        }

        [TestCase(0)]
        [TestCase(501)]
        public void ACapacityOutOfRangeIsRefused(int capacity)
        {
            var e = Assert.Throws<ArgumentOutOfRangeException>(() => NewGarage(capacity));
            Assert.AreEqual("capacity", e.ParamName);
        }

        [Test]
        public void AGarageNeedsAPlan()
        {
            var e = Assert.Throws<ArgumentNullException>(() => new Garage(1, null));
            Assert.AreEqual("plan", e.ParamName);
        }

        [Test]
        public void LeavingRaisesTheEvent()
        {
            Garage garage = NewGarage(2);
            var seen = new List<string>();
            garage.VehicleLeft += (plate, fee) => seen.Add(plate + " " + fee);
            garage.Enter(new Motorcycle("m"), 0);
            garage.Leave("m", 61);
            CollectionAssert.AreEqual(new[] { "M 2.50 EUR" }, seen);
        }

        [Test]
        public void AnUnsubscribedHandlerIsNotCalled()
        {
            Garage garage = NewGarage(2);
            int calls = 0;
            Action<string, Money> handler = (plate, fee) => calls++;
            garage.VehicleLeft += handler;
            garage.Enter(new Car("a"), 0);
            garage.Leave("a", 1);
            garage.VehicleLeft -= handler;
            garage.Enter(new Car("a"), 2);
            garage.Leave("a", 3);
            Assert.AreEqual(1, calls);
        }

        [Test]
        public void CountWhereAsksThePredicate()
        {
            Garage garage = NewGarage(3);
            garage.Enter(new Car("a"), 0);
            garage.Enter(new Motorcycle("b"), 0);
            garage.Enter(new Car("c"), 0);
            Assert.AreEqual(2, garage.CountWhere(v => v.Wheels == 4));
            Assert.AreEqual(1, garage.CountWhere(v => v is Motorcycle));
            Assert.Throws<ArgumentNullException>(() => garage.CountWhere(null));
        }

        [Test]
        public void PlatesAreInOrdinalOrder()
        {
            Garage garage = NewGarage(3);
            garage.Enter(new Car("b"), 0);
            garage.Enter(new Car("C"), 0);
            garage.Enter(new Car("a"), 0);
            CollectionAssert.AreEqual(new[] { "A", "B", "C" }, garage.Plates());
        }

        [Test]
        public void VisitsAndTheBusiestHourAreCounted()
        {
            Garage garage = NewGarage(3);
            garage.Enter(new Car("a"), 0);
            garage.Leave("a", 10);
            garage.Enter(new Car("a"), 70);
            garage.Enter(new Car("b"), 75);
            Assert.AreEqual(2, garage.VisitsOf(" A"));
            Assert.AreEqual(0, garage.VisitsOf("z"));
            Assert.AreEqual(1, garage.BusiestHour());
            Assert.Throws<InvalidOperationException>(() => NewGarage(1).BusiestHour());
        }

        [Test]
        public void ATestDoublePlanIsAskedForTheFee()
        {
            var plan = new RecordingPlan(new Money(1m));
            var garage = new Garage(5, plan);
            var car = new Car("x");
            garage.Enter(car, 10);
            Assert.AreEqual(new Money(1m), garage.Leave("x", 25));
            Assert.AreSame(car, plan.Vehicles.Single());
            CollectionAssert.AreEqual(new[] { 15 }, plan.Minutes);
            Assert.AreSame(plan, garage.Plan);
        }

        [Test]
        public void TheExceptionOfAPlanReachesTheCaller()
        {
            var failure = new FormatException("no tariff today");
            var plan = new RecordingPlan(new Money(1m)) { Failure = failure };
            var garage = new Garage(5, plan);
            garage.Enter(new Car("x"), 0);
            var e = Assert.Throws<FormatException>(() => garage.Leave("x", 5));
            Assert.AreSame(failure, e);
            Assert.AreEqual(1, garage.Count);
        }

        [Test]
        public void AShiftCountsDeparturesUntilItIsDisposed()
        {
            Garage garage = NewGarage(3);
            Shift shift;
            using (shift = new Shift(garage))
            {
                garage.Enter(new Car("a"), 0);
                garage.Enter(new Car("b"), 0);
                garage.Leave("a", 60);
                garage.Leave("b", 61);
                Assert.AreEqual(2, shift.Departures);
                Assert.AreEqual(new Money(7.50m), shift.Takings);
                Assert.IsTrue(shift.IsOpen);
            }
            garage.Enter(new Car("c"), 0);
            garage.Leave("c", 30);
            Assert.AreEqual(2, shift.Departures);
            Assert.IsFalse(shift.IsOpen);
            Assert.AreEqual(new Money(10m), garage.Takings);
            shift.Dispose();
        }

        [Test]
        public void AShiftNeedsAGarage()
        {
            var e = Assert.Throws<ArgumentNullException>(() => new Shift(null));
            Assert.AreEqual("garage", e.ParamName);
        }
    }
}
