using System;
using NUnit.Framework;

namespace Parking.Tests
{
    [TestFixture]
    public class VehicleTests
    {
        [Test]
        public void ACarHasFourWheels()
        {
            Vehicle car = new Car("ab-123");
            Assert.AreEqual(4, car.Wheels);
            Assert.AreEqual("car", car.Kind);
            Assert.AreEqual(1m, car.SizeFactor);
        }

        [Test]
        public void AMotorcycleTakesHalfAPlace()
        {
            Vehicle motorcycle = new Motorcycle("m-1");
            Assert.AreEqual(2, motorcycle.Wheels);
            Assert.AreEqual("motorcycle", motorcycle.Kind);
            Assert.AreEqual(0.5m, motorcycle.SizeFactor);
        }

        [TestCase("ab-123", "AB-123")]
        [TestCase("  xy 9 ", "XY 9")]
        public void PlatesAreTrimmedAndInUpperCase(string plate, string expected)
        {
            Assert.AreEqual(expected, new Car(plate).Plate);
        }

        [Test]
        public void ANullPlateIsRefused()
        {
            var e = Assert.Throws<ArgumentNullException>(() => new Car(null));
            Assert.AreEqual("plate", e.ParamName);
        }

        [Test]
        public void ABlankPlateIsRefused()
        {
            var e = Assert.Throws<ArgumentException>(() => new Motorcycle("   "));
            Assert.AreEqual("plate", e.ParamName);
        }

        [Test]
        public void ToStringNamesTheKindAndThePlate()
        {
            Assert.AreEqual("motorcycle M-1", new Motorcycle("m-1").ToString());
            Assert.AreEqual("car AB 12", new Car("ab 12").ToString());
        }
    }
}
